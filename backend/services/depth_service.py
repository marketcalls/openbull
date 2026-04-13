"""
Market depth service - fetches 5-level bid/ask data from broker APIs.
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_depth_with_auth(
    symbol: str, exchange: str, auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get market depth for a single symbol."""
    try:
        data_module = importlib.import_module(f"backend.broker.{broker}.api.data")
    except ImportError:
        return False, {"status": "error", "message": "Broker module not found"}, 404

    try:
        result = data_module.get_market_depth(symbol, exchange, auth_token, config)
        return True, {"status": "success", "data": result}, 200
    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.error("Error fetching market depth: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
