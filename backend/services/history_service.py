"""
History service - fetches historical OHLCV candles from broker APIs.
"""

import importlib
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def get_history_with_auth(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, broker: str, config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get historical OHLCV candles."""
    try:
        data_module = importlib.import_module(f"backend.broker.{broker}.api.data")
    except ImportError:
        return False, {"status": "error", "message": "Broker module not found"}, 404

    # Validate dates
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return False, {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD"}, 400

    if start_dt > end_dt:
        return False, {"status": "error", "message": "start_date must be before end_date"}, 400

    # Validate interval
    supported = getattr(data_module, "TIMEFRAME_MAP", {})
    if interval not in supported:
        return False, {
            "status": "error",
            "message": f"Unsupported interval: {interval}. Supported: {list(supported.keys())}",
        }, 400

    try:
        result = data_module.get_history(
            symbol, exchange, interval, start_date, end_date, auth_token, config
        )
        return True, {"status": "success", "data": result}, 200
    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.error("Error fetching history: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
