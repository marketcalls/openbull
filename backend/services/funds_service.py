"""
Funds service - fetches margin/funds data from broker APIs.
Dual-entry pattern: get_funds_with_auth() + get_funds()
"""

import importlib
import logging
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def _import_broker_module(broker_name: str):
    """Dynamically import the broker-specific funds module."""
    try:
        module_path = f"backend.broker.{broker_name}.api.funds"
        return importlib.import_module(module_path)
    except ImportError as error:
        logger.error("Error importing broker funds module '%s': %s", broker_name, error)
        return None


def get_funds_with_auth(
    auth_token: str, broker: str, config: dict | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get account funds using provided auth token.

    Args:
        auth_token: Broker authentication token
        broker: Broker name
        config: Broker config dict (api_key, api_secret, etc.)

    Returns:
        (success, response_data, http_status_code)
    """
    broker_module = _import_broker_module(broker)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        funds = broker_module.get_margin_data(auth_token, config)
        return True, {"status": "success", "data": funds}, 200
    except Exception as e:
        logger.error("Error in broker_module.get_margin_data: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_funds(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get account funds. Supports both API-key and direct auth token calls.

    Args:
        api_key: OpenBull API key (for external API calls - resolved upstream)
        auth_token: Direct broker auth token
        broker: Broker name
        config: Broker config dict

    Returns:
        (success, response_data, http_status_code)
    """
    if auth_token and broker:
        return get_funds_with_auth(auth_token, broker, config)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
