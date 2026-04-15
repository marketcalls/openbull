"""
Split order service - splits a large order into multiple smaller orders placed sequentially.
"""

import copy
import importlib
import logging
import os
import time
from typing import Any

from backend.utils.constants import (
    VALID_ACTIONS,
    VALID_EXCHANGES,
    VALID_PRICE_TYPES,
    VALID_PRODUCT_TYPES,
)

logger = logging.getLogger(__name__)

MAX_ORDERS = 100
REQUIRED_ORDER_FIELDS = ["symbol", "exchange", "action", "quantity", "pricetype", "product"]


def _import_broker_order_module(broker_name: str):
    try:
        return importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
    except ImportError as error:
        logger.error("Error importing broker order module '%s': %s", broker_name, error)
        return None


def _get_order_delay_seconds() -> float:
    """Parse ORDER_RATE_LIMIT ('N per second') into a delay between orders."""
    raw = os.getenv("ORDER_RATE_LIMIT", "10 per second")
    try:
        rate = int(raw.split()[0])
        return 1.0 / rate if rate > 0 else 0.1
    except (ValueError, IndexError):
        return 0.1


def _validate_split_data(data: dict) -> tuple[bool, str | None]:
    missing = [f for f in REQUIRED_ORDER_FIELDS if f not in data]
    if missing:
        return False, f"Missing mandatory field(s): {', '.join(missing)}"
    if "splitsize" not in data:
        return False, "Missing mandatory field: splitsize"

    if data["exchange"] not in VALID_EXCHANGES:
        return False, f"Invalid exchange. Must be one of: {', '.join(sorted(VALID_EXCHANGES))}"
    data["action"] = data["action"].upper()
    if data["action"] not in VALID_ACTIONS:
        return False, f"Invalid action. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"
    if data["pricetype"] not in VALID_PRICE_TYPES:
        return False, f"Invalid pricetype. Must be one of: {', '.join(sorted(VALID_PRICE_TYPES))}"
    if data["product"] not in VALID_PRODUCT_TYPES:
        return False, f"Invalid product. Must be one of: {', '.join(sorted(VALID_PRODUCT_TYPES))}"

    return True, None


def _place_single_order(order_data: dict, broker_module, auth_token: str, order_num: int) -> dict:
    try:
        res, response_data, order_id = broker_module.place_order_api(order_data, auth_token)
        status_code = getattr(res, "status", None) or getattr(res, "status_code", 500)

        if status_code == 200 and order_id:
            return {
                "order_num": order_num,
                "quantity": int(order_data["quantity"]),
                "status": "success",
                "orderid": order_id,
            }

        message = (
            response_data.get("message", "Failed to place order")
            if isinstance(response_data, dict)
            else "Failed to place order"
        )
        return {
            "order_num": order_num,
            "quantity": int(order_data["quantity"]),
            "status": "error",
            "message": message,
        }

    except Exception as e:
        logger.exception("Error placing split order %d: %s", order_num, e)
        return {
            "order_num": order_num,
            "quantity": int(order_data["quantity"]),
            "status": "error",
            "message": "Failed to place order due to internal error",
        }


def split_order(
    split_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Split a quantity into chunks and place orders sequentially with rate limiting."""
    is_valid, err = _validate_split_data(split_data)
    if not is_valid:
        return False, {"status": "error", "message": err}, 400

    try:
        split_size = int(split_data["splitsize"])
        total_quantity = int(split_data["quantity"])
    except (ValueError, TypeError):
        return False, {"status": "error", "message": "Invalid quantity or split size"}, 400

    if split_size <= 0:
        return False, {"status": "error", "message": "Split size must be greater than 0"}, 400
    if total_quantity <= 0:
        return False, {"status": "error", "message": "Quantity must be greater than 0"}, 400

    num_full_orders = total_quantity // split_size
    remaining_qty = total_quantity % split_size
    total_orders = num_full_orders + (1 if remaining_qty > 0 else 0)

    if total_orders > MAX_ORDERS:
        return False, {
            "status": "error",
            "message": f"Total number of orders would exceed maximum limit of {MAX_ORDERS}",
        }, 400

    broker_module = _import_broker_order_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    results: list[dict] = []
    delay = _get_order_delay_seconds()

    for i in range(num_full_orders):
        if i > 0:
            time.sleep(delay)
        order_data = copy.deepcopy(split_data)
        order_data["quantity"] = str(split_size)
        results.append(_place_single_order(order_data, broker_module, auth_token, i + 1))

    if remaining_qty > 0:
        if num_full_orders > 0:
            time.sleep(delay)
        order_data = copy.deepcopy(split_data)
        order_data["quantity"] = str(remaining_qty)
        results.append(_place_single_order(order_data, broker_module, auth_token, total_orders))

    return True, {
        "status": "success",
        "split_size": split_size,
        "total_quantity": total_quantity,
        "results": results,
    }, 200
