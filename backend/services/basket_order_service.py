"""
Basket order service - places multiple orders concurrently via broker APIs.
"""

import importlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from backend.utils.constants import (
    VALID_ACTIONS,
    VALID_EXCHANGES,
    VALID_PRICE_TYPES,
    VALID_PRODUCT_TYPES,
)

logger = logging.getLogger(__name__)

REQUIRED_ORDER_FIELDS = ["symbol", "exchange", "action", "quantity", "pricetype", "product"]
BATCH_SIZE = 10
BATCH_DELAY_SEC = 1.0


def _import_broker_order_module(broker_name: str):
    try:
        return importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
    except ImportError as error:
        logger.error("Error importing broker order module '%s': %s", broker_name, error)
        return None


def _validate_order(order: dict[str, Any]) -> tuple[bool, str | None]:
    missing = [f for f in REQUIRED_ORDER_FIELDS if f not in order]
    if missing:
        return False, f"Missing mandatory field(s): {', '.join(missing)}"

    if order.get("exchange") not in VALID_EXCHANGES:
        return False, f"Invalid exchange. Must be one of: {', '.join(sorted(VALID_EXCHANGES))}"

    order["action"] = order["action"].upper()
    if order["action"] not in VALID_ACTIONS:
        return False, f"Invalid action. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    if order.get("pricetype") not in VALID_PRICE_TYPES:
        return False, f"Invalid pricetype. Must be one of: {', '.join(sorted(VALID_PRICE_TYPES))}"

    if order.get("product") not in VALID_PRODUCT_TYPES:
        return False, f"Invalid product. Must be one of: {', '.join(sorted(VALID_PRODUCT_TYPES))}"

    return True, None


def _place_single_order(order_data: dict, broker_module, auth_token: str) -> dict:
    symbol = order_data.get("symbol", "Unknown")
    try:
        res, response_data, order_id = broker_module.place_order_api(order_data, auth_token)
        status_code = getattr(res, "status", None) or getattr(res, "status_code", 500)

        if status_code == 200 and order_id:
            return {"symbol": symbol, "status": "success", "orderid": order_id}

        message = (
            response_data.get("message", "Failed to place order")
            if isinstance(response_data, dict)
            else "Failed to place order"
        )
        return {"symbol": symbol, "status": "error", "message": message}

    except Exception as e:
        logger.exception("Error placing basket leg for %s: %s", symbol, e)
        return {
            "symbol": symbol,
            "status": "error",
            "message": "Failed to place order due to internal error",
        }


def place_basket_order(
    basket_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place a basket of orders concurrently. BUY orders are placed before SELL orders."""
    orders = basket_data.get("orders")
    if not isinstance(orders, list) or not orders:
        return False, {"status": "error", "message": "orders array cannot be empty"}, 400

    strategy = basket_data.get("strategy", "")

    # Validate every leg up front so we fail fast
    for idx, order in enumerate(orders, 1):
        is_valid, error_message = _validate_order(order)
        if not is_valid:
            return False, {"status": "error", "message": f"Order {idx}: {error_message}"}, 400

    broker_module = _import_broker_order_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    # Prioritize BUY before SELL to free up margin for opposing SELL legs
    buy_orders = [o for o in orders if o.get("action") == "BUY"]
    sell_orders = [o for o in orders if o.get("action") == "SELL"]
    sorted_orders = buy_orders + sell_orders

    orders_with_meta = [{**o, "strategy": strategy} for o in sorted_orders]

    results: list[dict] = []
    total = len(orders_with_meta)

    for batch_start in range(0, total, BATCH_SIZE):
        if batch_start > 0:
            time.sleep(BATCH_DELAY_SEC)
        batch = orders_with_meta[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(_place_single_order, order, broker_module, auth_token): order
                for order in batch
            }
            for future in as_completed(futures):
                results.append(future.result())

    return True, {"status": "success", "results": results}, 200
