"""
Tradebook service - fetches and transforms trade book data.
Dual-entry pattern: get_tradebook_with_auth() + get_tradebook()
"""

import importlib
import logging
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def _format_decimal(value):
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def _format_trade_data(trade_data):
    """Format numeric values in trade data."""
    quantity_fields = {"quantity", "qty", "tradedqty", "filledqty", "filled_quantity"}

    if isinstance(trade_data, list):
        return [
            {
                key: (int(value) if value == int(value) else value)
                if (key.lower() in quantity_fields and isinstance(value, (int, float)))
                else (_format_decimal(value) if isinstance(value, (int, float)) else value)
                for key, value in item.items()
            }
            for item in trade_data
        ]
    return trade_data


def _import_broker_modules(broker_name: str) -> dict[str, Any] | None:
    """Dynamically import broker-specific tradebook modules."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker_name}.mapping.order_data")
        return {
            "get_trade_book": api_module.get_trade_book,
            "map_trade_data": mapping_module.map_trade_data,
            "transform_tradebook_data": mapping_module.transform_tradebook_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error("Error importing broker modules: %s", error)
        return None


def get_tradebook_with_auth(
    auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get trade book using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    broker_funcs = _import_broker_modules(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        trade_data = broker_funcs["get_trade_book"](auth_token)

        if isinstance(trade_data, dict) and trade_data.get("status") == "error":
            return (
                False,
                {"status": "error", "message": trade_data.get("message", "Error fetching trade data")},
                500,
            )

        trade_data = broker_funcs["map_trade_data"](trade_data=trade_data)
        trade_data = broker_funcs["transform_tradebook_data"](trade_data)

        formatted_trades = _format_trade_data(trade_data)
        return True, {"status": "success", "data": formatted_trades}, 200

    except Exception as e:
        logger.error("Error processing trade data: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_tradebook(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get trade book. Supports both API-key and direct auth token calls."""
    if auth_token and broker:
        return get_tradebook_with_auth(auth_token, broker, config)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
