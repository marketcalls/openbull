"""
Options multi-order service.
Resolves each leg's offset/expiry into a tradable symbol, then dispatches BUY legs
before SELL legs concurrently — same pattern as basket_order_service.
"""

import importlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from backend.services.option_symbol_service import get_option_symbol

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
BATCH_DELAY_SEC = 1.0


def _import_broker_order_module(broker_name: str):
    try:
        return importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
    except ImportError as error:
        logger.error("Error importing broker order module '%s': %s", broker_name, error)
        return None


def _resolve_leg(
    leg: dict, default_underlying: str, default_exchange: str, default_expiry: str | None,
    auth_token: str, broker: str, config: dict | None,
) -> tuple[bool, dict, int]:
    underlying = leg.get("underlying") or default_underlying
    exchange = leg.get("exchange") or default_exchange
    expiry_date = leg.get("expiry_date") or default_expiry
    offset = leg.get("offset")
    option_type = leg.get("option_type")

    missing = [n for n, v in [
        ("underlying", underlying), ("exchange", exchange), ("expiry_date", expiry_date),
        ("offset", offset), ("option_type", option_type),
    ] if not v]
    if missing:
        return False, {"status": "error", "message": f"Missing leg field(s): {', '.join(missing)}"}, 400

    return get_option_symbol(
        underlying=underlying, exchange=exchange, expiry_date=expiry_date,
        offset=offset, option_type=option_type,
        auth_token=auth_token, broker=broker, config=config,
    )


def _place_leg(order_data: dict, broker_module, auth_token: str, leg_meta: dict) -> dict:
    try:
        res, response_data, order_id = broker_module.place_order_api(order_data, auth_token)
        status_code = getattr(res, "status", None) or getattr(res, "status_code", 500)

        if status_code == 200 and order_id:
            return {**leg_meta, "status": "success", "orderid": order_id}

        message = (
            response_data.get("message", "Failed to place order")
            if isinstance(response_data, dict)
            else "Failed to place order"
        )
        return {**leg_meta, "status": "error", "message": message}

    except Exception as e:
        logger.exception("Error placing options leg %s: %s", leg_meta.get("symbol"), e)
        return {**leg_meta, "status": "error", "message": "Failed to place order due to internal error"}


def place_options_multiorder(
    multi_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place a multi-leg options strategy. BUY legs go before SELL legs."""
    legs = multi_data.get("legs")
    if not isinstance(legs, list) or not legs:
        return False, {"status": "error", "message": "legs array cannot be empty"}, 400

    underlying = multi_data.get("underlying")
    exchange = multi_data.get("exchange")
    expiry_date = multi_data.get("expiry_date")
    strategy = multi_data.get("strategy", "")
    default_pricetype = multi_data.get("pricetype", "MARKET")
    default_product = multi_data.get("product", "NRML")

    # Resolve each leg → symbol; capture LTP from first successful resolution
    resolved_legs: list[dict] = []
    underlying_ltp: float | None = None

    for idx, leg in enumerate(legs, 1):
        ok, sym_resp, status_code = _resolve_leg(
            leg, underlying, exchange, expiry_date, auth_token, broker, config,
        )
        if not ok:
            return False, {
                "status": "error",
                "message": f"Leg {idx}: {sym_resp.get('message')}",
            }, status_code

        action = (leg.get("action") or "").upper()
        if action not in ("BUY", "SELL"):
            return False, {"status": "error", "message": f"Leg {idx}: action must be BUY or SELL"}, 400

        quantity = leg.get("quantity")
        if not quantity:
            return False, {"status": "error", "message": f"Leg {idx}: quantity required"}, 400

        if underlying_ltp is None:
            underlying_ltp = sym_resp.get("underlying_ltp")

        resolved_legs.append({
            "leg": idx,
            "symbol": sym_resp["symbol"],
            "exchange": sym_resp["exchange"],
            "action": action,
            "quantity": str(quantity),
            "pricetype": leg.get("pricetype", default_pricetype),
            "product": leg.get("product", default_product),
            "price": str(leg.get("price", "0")),
            "trigger_price": str(leg.get("trigger_price", "0")),
            "disclosed_quantity": str(leg.get("disclosed_quantity", "0")),
            "strategy": strategy,
            "offset": leg.get("offset"),
            "option_type": leg.get("option_type"),
        })

    broker_module = _import_broker_order_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    buy_legs = [l for l in resolved_legs if l["action"] == "BUY"]
    sell_legs = [l for l in resolved_legs if l["action"] == "SELL"]
    sorted_legs = buy_legs + sell_legs

    results: list[dict] = []
    total = len(sorted_legs)

    for batch_start in range(0, total, BATCH_SIZE):
        if batch_start > 0:
            time.sleep(BATCH_DELAY_SEC)
        batch = sorted_legs[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {}
            for leg in batch:
                order_data = {k: leg[k] for k in (
                    "symbol", "exchange", "action", "quantity", "pricetype", "product",
                    "price", "trigger_price", "disclosed_quantity", "strategy",
                )}
                leg_meta = {
                    "leg": leg["leg"], "symbol": leg["symbol"], "action": leg["action"],
                    "offset": leg["offset"], "option_type": leg["option_type"],
                }
                fut = executor.submit(_place_leg, order_data, broker_module, auth_token, leg_meta)
                futures[fut] = leg

            for future in as_completed(futures):
                results.append(future.result())

    results.sort(key=lambda r: r["leg"])

    return True, {
        "status": "success",
        "underlying": underlying,
        "underlying_ltp": underlying_ltp,
        "results": results,
    }, 200
