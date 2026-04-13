"""
Order status service - fetches status of a specific order by orderid.
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_orderstatus_with_auth(
    orderid: str, auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get status of a specific order using provided auth token."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker}.mapping.order_data")
    except ImportError as error:
        logger.error("Error importing broker modules: %s", error)
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        order_data = api_module.get_order_book(auth_token)

        if isinstance(order_data, dict) and order_data.get("status") == "error":
            return False, {"status": "error", "message": order_data.get("message", "Error fetching orders")}, 500

        mapped_data = mapping_module.map_order_data(order_data=order_data)
        transformed = mapping_module.transform_order_data(mapped_data)

        for order in transformed:
            if order.get("orderid") == orderid:
                return True, {"status": "success", "data": order}, 200

        return False, {"status": "error", "message": f"Order {orderid} not found"}, 404

    except Exception as e:
        logger.error("Error fetching order status: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
