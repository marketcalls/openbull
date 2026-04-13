"""
Quotes service - fetches LTP/OHLC quotes from broker APIs.
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_quotes_with_auth(
    symbol: str, exchange: str, auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get quotes for a single symbol."""
    try:
        data_module = importlib.import_module(f"backend.broker.{broker}.api.data")
    except ImportError:
        return False, {"status": "error", "message": "Broker module not found"}, 404

    try:
        result = data_module.get_quotes(symbol, exchange, auth_token, config)
        return True, {"status": "success", "data": result}, 200
    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.error("Error fetching quotes: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def get_multi_quotes_with_auth(
    symbols_list: list[dict], auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get quotes for multiple symbols."""
    try:
        data_module = importlib.import_module(f"backend.broker.{broker}.api.data")
    except ImportError:
        return False, {"status": "error", "message": "Broker module not found"}, 404

    try:
        results = data_module.get_multi_quotes(symbols_list, auth_token, config)
        return True, {"status": "success", "data": results}, 200
    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.error("Error fetching multi quotes: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
