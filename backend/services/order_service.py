"""
Order placement service - places, modifies, cancels orders via broker APIs.
Dual-entry pattern: place_order_with_auth() + place_order()
"""

import copy
import importlib
import logging
import traceback
from typing import Any

from backend.utils.constants import (
    VALID_ACTIONS,
    VALID_EXCHANGES,
    VALID_PRICE_TYPES,
    VALID_PRODUCT_TYPES,
)

logger = logging.getLogger(__name__)

REQUIRED_ORDER_FIELDS = ["symbol", "exchange", "action", "quantity", "pricetype", "product"]


def _import_broker_module(broker_name: str):
    """Dynamically import the broker-specific order API module."""
    try:
        module_path = f"backend.broker.{broker_name}.api.order_api"
        return importlib.import_module(module_path)
    except ImportError as error:
        logger.error("Error importing broker order module '%s': %s", broker_name, error)
        return None


def validate_order_data(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate order data against required fields and valid values.

    Returns:
        (is_valid, error_message)
    """
    missing_fields = [field for field in REQUIRED_ORDER_FIELDS if field not in data]
    if missing_fields:
        return False, f"Missing mandatory field(s): {', '.join(missing_fields)}"

    if data.get("exchange") not in VALID_EXCHANGES:
        return False, f"Invalid exchange. Must be one of: {', '.join(sorted(VALID_EXCHANGES))}"

    if "action" in data:
        data["action"] = data["action"].upper()
        if data["action"] not in VALID_ACTIONS:
            return False, f"Invalid action. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    if data.get("pricetype") not in VALID_PRICE_TYPES:
        return False, f"Invalid pricetype. Must be one of: {', '.join(sorted(VALID_PRICE_TYPES))}"

    if data.get("product") not in VALID_PRODUCT_TYPES:
        return False, f"Invalid product. Must be one of: {', '.join(sorted(VALID_PRODUCT_TYPES))}"

    return True, None


def place_order_with_auth(
    order_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place an order using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    # Sandbox dispatch: if trading mode is sandbox and we know the user, route
    # to the simulated engine. Live broker API is not called.
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import place_order as sbx_place

                return sbx_place(user_id, order_data)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        res, response_data, order_id = broker_module.place_order_api(order_data, auth_token)
    except Exception as e:
        logger.error("Error in broker_module.place_order_api: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": "Failed to place order due to internal error"}, 500

    if res and res.status == 200:
        return True, {"status": "success", "orderid": order_id}, 200
    else:
        message = (
            response_data.get("message", "Failed to place order")
            if isinstance(response_data, dict)
            else "Failed to place order"
        )
        status = res.status if res and res.status != 200 else 500
        return False, {"status": "error", "message": message}, status


def place_order(
    order_data: dict[str, Any],
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place an order. Supports both API-key and direct auth token calls.

    Returns:
        (success, response_data, http_status_code)
    """
    # Validate
    is_valid, error_message = validate_order_data(order_data)
    if not is_valid:
        return False, {"status": "error", "message": error_message}, 400

    if auth_token and broker:
        return place_order_with_auth(order_data, auth_token, broker, config, user_id=user_id)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )


def place_smart_order(
    order_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place a smart order (position-aware)."""
    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        res, response_data, order_id = broker_module.place_smartorder_api(order_data, auth_token)
    except Exception as e:
        logger.error("Error in place_smartorder_api: %s", e)
        return False, {"status": "error", "message": str(e)}, 500

    if response_data.get("status") == "success":
        result = {"status": "success"}
        if order_id:
            result["orderid"] = order_id
        if response_data.get("message"):
            result["message"] = response_data["message"]
        return True, result, 200
    else:
        return False, response_data, 500


def modify_order_service(
    data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Modify an existing order."""
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import modify_order as sbx_mod

                return sbx_mod(user_id, data)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        result, status_code = broker_module.modify_order(data, auth_token)
        success = result.get("status") == "success"
        return success, result, status_code
    except Exception as e:
        logger.error("Error modifying order: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def cancel_order_service(
    orderid: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Cancel a specific order."""
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import cancel_order as sbx_cancel

                return sbx_cancel(user_id, orderid)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        result, status_code = broker_module.cancel_order(orderid, auth_token)
        success = result.get("status") == "success"
        return success, result, status_code
    except Exception as e:
        logger.error("Error canceling order: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def cancel_all_orders_service(
    auth_token: str,
    broker: str,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Cancel all open orders."""
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import cancel_all_orders as sbx_cancel_all

                return sbx_cancel_all(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        canceled, failed = broker_module.cancel_all_orders_api({}, auth_token)
        return True, {
            "status": "success",
            "data": {"canceled": canceled, "failed": failed},
        }, 200
    except Exception as e:
        logger.error("Error canceling all orders: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def close_all_positions_service(
    api_key: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Close all open positions."""
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import close_all_positions as sbx_close

                return sbx_close(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        result, status_code = broker_module.close_all_positions(api_key, auth_token)
        success = result.get("status") == "success"
        return success, result, status_code
    except Exception as e:
        logger.error("Error closing all positions: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
