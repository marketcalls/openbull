"""
Orderbook service - fetches and transforms order book data.
Dual-entry pattern: get_orderbook_with_auth() + get_orderbook()
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


def _format_order_data(order_data):
    """Format numeric values in order data to 2 decimal places."""
    quantity_fields = {"quantity", "qty", "filledqty", "filled_quantity", "tradedqty", "traded_quantity"}

    if isinstance(order_data, list):
        formatted_orders = []
        for item in order_data:
            formatted_item = {}
            for key, value in item.items():
                if isinstance(value, (int, float)):
                    if key.lower() in quantity_fields:
                        formatted_item[key] = int(value) if value == int(value) else value
                    else:
                        formatted_item[key] = _format_decimal(value)
                else:
                    formatted_item[key] = value

            pricetype = formatted_item.get("pricetype", "").upper()
            if pricetype == "MARKET":
                formatted_item["price"] = 0.0

            formatted_orders.append(formatted_item)
        return formatted_orders
    return order_data


def _format_statistics(stats):
    if isinstance(stats, dict):
        formatted = {}
        for key, value in stats.items():
            if any(t in key for t in ["total_", "orders", "completed", "open", "rejected"]):
                formatted[key] = int(value) if isinstance(value, (int, float)) else value
            elif isinstance(value, (int, float)):
                formatted[key] = _format_decimal(value)
            else:
                formatted[key] = value
        return formatted
    return stats


def _import_broker_modules(broker_name: str) -> dict[str, Any] | None:
    """Dynamically import broker-specific order modules."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker_name}.mapping.order_data")
        return {
            "get_order_book": api_module.get_order_book,
            "map_order_data": mapping_module.map_order_data,
            "calculate_order_statistics": mapping_module.calculate_order_statistics,
            "transform_order_data": mapping_module.transform_order_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error("Error importing broker modules: %s", error)
        return None


def get_orderbook_with_auth(
    auth_token: str, broker: str, config: dict | None = None, user_id: int | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get order book using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import get_orderbook as sbx_ob

                return sbx_ob(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_funcs = _import_broker_modules(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        order_data = broker_funcs["get_order_book"](auth_token)

        if isinstance(order_data, dict) and order_data.get("status") == "error":
            return (
                False,
                {"status": "error", "message": order_data.get("message", "Error fetching order data")},
                500,
            )

        order_data = broker_funcs["map_order_data"](order_data=order_data)
        order_stats = broker_funcs["calculate_order_statistics"](order_data)
        order_data = broker_funcs["transform_order_data"](order_data)

        formatted_orders = _format_order_data(order_data)
        formatted_stats = _format_statistics(order_stats)

        return (
            True,
            {"status": "success", "data": {"orders": formatted_orders, "statistics": formatted_stats}},
            200,
        )
    except Exception as e:
        logger.error("Error processing order data: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_orderbook(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get order book. Supports both API-key and direct auth token calls."""
    if auth_token and broker:
        return get_orderbook_with_auth(auth_token, broker, config, user_id=user_id)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
