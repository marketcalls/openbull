"""
Margin service - calculates margin requirement for a basket of positions via broker APIs.
"""

import importlib
import logging
from typing import Any

from backend.utils.constants import (
    VALID_ACTIONS,
    VALID_EXCHANGES,
    VALID_PRICE_TYPES,
    VALID_PRODUCT_TYPES,
)

logger = logging.getLogger(__name__)

REQUIRED_POSITION_FIELDS = ["exchange", "symbol", "action", "quantity", "product", "pricetype"]
MAX_POSITIONS = 50


def _import_broker_margin_module(broker_name: str):
    """Dynamically import the broker-specific margin API module."""
    try:
        module_path = f"backend.broker.{broker_name}.api.margin_api"
        return importlib.import_module(module_path)
    except ImportError as error:
        logger.error("Error importing broker margin module '%s': %s", broker_name, error)
        return None


def _validate_position(position: dict[str, Any], index: int) -> tuple[bool, str | None]:
    missing = [f for f in REQUIRED_POSITION_FIELDS if f not in position]
    if missing:
        return False, f"Position {index}: Missing mandatory field(s): {', '.join(missing)}"

    if position.get("exchange") not in VALID_EXCHANGES:
        return False, f"Position {index}: Invalid exchange. Must be one of: {', '.join(sorted(VALID_EXCHANGES))}"

    position["action"] = position["action"].upper()
    if position["action"] not in VALID_ACTIONS:
        return False, f"Position {index}: Invalid action. Must be one of: {', '.join(sorted(VALID_ACTIONS))}"

    if position["pricetype"] not in VALID_PRICE_TYPES:
        return False, f"Position {index}: Invalid pricetype. Must be one of: {', '.join(sorted(VALID_PRICE_TYPES))}"

    if position["product"] not in VALID_PRODUCT_TYPES:
        return False, f"Position {index}: Invalid product. Must be one of: {', '.join(sorted(VALID_PRODUCT_TYPES))}"

    try:
        qty = int(position.get("quantity", 0))
        if qty <= 0:
            return False, f"Position {index}: Quantity must be a positive number"
    except (ValueError, TypeError):
        return False, f"Position {index}: Invalid quantity format"

    try:
        price = float(position.get("price", 0))
        if price < 0:
            return False, f"Position {index}: Price cannot be negative"
    except (ValueError, TypeError):
        return False, f"Position {index}: Invalid price format"

    return True, None


def _validate_margin_data(data: dict) -> tuple[bool, list[dict] | None, str | None]:
    positions = data.get("positions")
    if positions is None:
        return False, None, "Missing mandatory field: positions"
    if not isinstance(positions, list):
        return False, None, "positions must be an array"
    if len(positions) == 0:
        return False, None, "positions array cannot be empty"
    if len(positions) > MAX_POSITIONS:
        return False, None, f"Maximum {MAX_POSITIONS} positions allowed per request"

    validated = []
    for i, p in enumerate(positions, 1):
        ok, err = _validate_position(p, i)
        if not ok:
            return False, None, err
        validated.append(p)
    return True, validated, None


def calculate_margin(
    margin_data: dict[str, Any],
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Calculate margin requirement for a basket of positions."""
    is_valid, positions, error_message = _validate_margin_data(margin_data)
    if not is_valid:
        return False, {"status": "error", "message": error_message}, 400

    broker_module = _import_broker_margin_module(broker)
    if broker_module is None:
        return False, {
            "status": "error",
            "message": f"Margin calculation not supported for broker: {broker}",
        }, 404

    if not hasattr(broker_module, "calculate_margin_api"):
        return False, {
            "status": "error",
            "message": f"Margin calculation not implemented for broker: {broker}",
        }, 501

    try:
        response, response_data = broker_module.calculate_margin_api(positions, auth_token)
    except Exception as e:
        logger.exception("Error in broker margin API: %s", e)
        return False, {
            "status": "error",
            "message": "Failed to calculate margin due to internal error",
        }, 500

    status_code = getattr(response, "status_code", None) or getattr(response, "status", 500)

    if status_code == 200 and isinstance(response_data, dict) and response_data.get("status") == "success":
        return True, response_data, 200

    message = (
        response_data.get("message", "Failed to calculate margin")
        if isinstance(response_data, dict)
        else "Failed to calculate margin"
    )
    return False, {"status": "error", "message": message}, status_code if status_code != 200 else 500
