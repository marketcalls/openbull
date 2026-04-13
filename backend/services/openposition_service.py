"""
Open position service - fetches the net quantity of a specific symbol's open position.
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_openposition_with_auth(
    symbol: str,
    exchange: str,
    product: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get open position quantity for a specific symbol."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker}.api.order_api")
    except ImportError as error:
        logger.error("Error importing broker modules: %s", error)
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        quantity = api_module.get_open_position(symbol, exchange, product, auth_token)
        return True, {"status": "success", "data": {"quantity": int(quantity)}}, 200
    except Exception as e:
        logger.error("Error fetching open position: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
