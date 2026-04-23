"""
Positions service - fetches and transforms position book data.
Dual-entry pattern: get_positions_with_auth() + get_positions()
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


def _format_position_data(position_data):
    """Format numeric values in position data."""
    quantity_fields = {"quantity", "qty", "netqty", "net_qty", "buyqty", "sellqty"}

    if isinstance(position_data, list):
        return [
            {
                key: (int(value) if value == int(value) else value)
                if (key.lower() in quantity_fields and isinstance(value, (int, float)))
                else (_format_decimal(value) if isinstance(value, (int, float)) else value)
                for key, value in item.items()
            }
            for item in position_data
        ]
    return position_data


def _import_broker_modules(broker_name: str) -> dict[str, Any] | None:
    """Dynamically import broker-specific position modules."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker_name}.mapping.order_data")
        return {
            "get_positions": api_module.get_positions,
            "map_position_data": mapping_module.map_position_data,
            "transform_positions_data": mapping_module.transform_positions_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error("Error importing broker modules: %s", error)
        return None


def get_positions_with_auth(
    auth_token: str, broker: str, config: dict | None = None, user_id: int | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get positions using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import get_positions as sbx_pos

                return sbx_pos(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_funcs = _import_broker_modules(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        positions_data = broker_funcs["get_positions"](auth_token)

        if isinstance(positions_data, dict) and positions_data.get("status") == "error":
            return (
                False,
                {"status": "error", "message": positions_data.get("message", "Error fetching positions")},
                500,
            )

        positions_data = broker_funcs["map_position_data"](positions_data)
        positions_data = broker_funcs["transform_positions_data"](positions_data)

        formatted_positions = _format_position_data(positions_data)
        return True, {"status": "success", "data": formatted_positions}, 200

    except Exception as e:
        logger.error("Error processing positions data: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_positions(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get positions. Supports both API-key and direct auth token calls."""
    if auth_token and broker:
        return get_positions_with_auth(auth_token, broker, config, user_id=user_id)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
