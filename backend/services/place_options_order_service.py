"""
Place options order service.
Resolves an option symbol from underlying+expiry+offset+type, then places the order.
Optionally splits large quantities via the existing split_order service.
"""

import logging
from typing import Any

from backend.services.option_symbol_service import get_option_symbol
from backend.services.order_service import place_order
from backend.services.split_order_service import split_order

logger = logging.getLogger(__name__)


def place_options_order(
    options_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place an options order using offset-based symbol resolution."""
    underlying = options_data.get("underlying")
    exchange = options_data.get("exchange")
    expiry_date = options_data.get("expiry_date")
    offset = options_data.get("offset")
    option_type = options_data.get("option_type")
    action = options_data.get("action")
    quantity = options_data.get("quantity")
    pricetype = options_data.get("pricetype")
    product = options_data.get("product")

    missing = [
        n for n, v in [
            ("underlying", underlying), ("exchange", exchange), ("offset", offset),
            ("option_type", option_type), ("action", action), ("quantity", quantity),
            ("pricetype", pricetype), ("product", product),
        ] if not v
    ]
    if missing:
        return False, {
            "status": "error",
            "message": f"Missing mandatory field(s): {', '.join(missing)}",
        }, 400

    ok, sym_resp, status_code = get_option_symbol(
        underlying=underlying, exchange=exchange, expiry_date=expiry_date,
        offset=offset, option_type=option_type,
        auth_token=auth_token, broker=broker, config=config,
    )
    if not ok:
        return False, sym_resp, status_code

    resolved_symbol = sym_resp["symbol"]
    options_exchange = sym_resp["exchange"]

    order_data = {
        "symbol": resolved_symbol,
        "exchange": options_exchange,
        "action": action.upper(),
        "quantity": str(quantity),
        "pricetype": pricetype,
        "product": product,
        "price": str(options_data.get("price", "0")),
        "trigger_price": str(options_data.get("trigger_price", "0")),
        "disclosed_quantity": str(options_data.get("disclosed_quantity", "0")),
        "strategy": options_data.get("strategy", ""),
    }

    splitsize_raw = options_data.get("splitsize", 0) or 0
    try:
        splitsize = int(splitsize_raw)
    except (ValueError, TypeError):
        splitsize = 0

    if splitsize > 0:
        split_payload = {**order_data, "splitsize": str(splitsize)}
        ok_split, split_resp, status_code = split_order(
            split_data=split_payload, auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_split:
            return False, split_resp, status_code
        return True, {
            "status": "success",
            "symbol": resolved_symbol,
            "exchange": options_exchange,
            "underlying": underlying,
            "underlying_ltp": sym_resp["underlying_ltp"],
            "offset": offset,
            "option_type": option_type.upper(),
            "split": split_resp,
        }, 200

    ok_order, order_resp, status_code = place_order(
        order_data=order_data, auth_token=auth_token, broker=broker, config=config,
    )
    if not ok_order:
        return False, order_resp, status_code

    return True, {
        "status": "success",
        "symbol": resolved_symbol,
        "exchange": options_exchange,
        "underlying": underlying,
        "underlying_ltp": sym_resp["underlying_ltp"],
        "offset": offset,
        "option_type": option_type.upper(),
        "orderid": order_resp.get("orderid"),
    }, 200
