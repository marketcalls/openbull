"""
Holdings service - fetches and transforms holdings/portfolio data.
Dual-entry pattern: get_holdings_with_auth() + get_holdings()
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


def _format_holdings_data(holdings_data):
    """Format numeric values in holdings data."""
    if isinstance(holdings_data, list):
        return [
            {
                key: _format_decimal(value) if key in ("pnl", "pnlpercent") else value
                for key, value in item.items()
            }
            for item in holdings_data
        ]
    return holdings_data


def _format_statistics(stats):
    """Format numeric values in portfolio statistics."""
    if isinstance(stats, dict):
        return {key: _format_decimal(value) for key, value in stats.items()}
    return stats


def _import_broker_modules(broker_name: str) -> dict[str, Any] | None:
    """Dynamically import broker-specific holdings modules."""
    try:
        api_module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
        mapping_module = importlib.import_module(f"backend.broker.{broker_name}.mapping.order_data")
        return {
            "get_holdings": api_module.get_holdings,
            "map_portfolio_data": mapping_module.map_portfolio_data,
            "calculate_portfolio_statistics": mapping_module.calculate_portfolio_statistics,
            "transform_holdings_data": mapping_module.transform_holdings_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error("Error importing broker modules: %s", error)
        return None


def get_holdings_with_auth(
    auth_token: str, broker: str, config: dict | None = None, user_id: int | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Get holdings using provided auth token.

    Returns:
        (success, response_data, http_status_code)
    """
    if user_id is not None:
        try:
            from backend.services.trading_mode_service import get_trading_mode_sync

            if get_trading_mode_sync() == "sandbox":
                from backend.services.sandbox_service import get_holdings as sbx_hld

                return sbx_hld(user_id)
        except Exception:
            logger.exception("sandbox dispatch failed; falling back to live")

    broker_funcs = _import_broker_modules(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        holdings = broker_funcs["get_holdings"](auth_token)

        if isinstance(holdings, dict) and holdings.get("status") == "error":
            return (
                False,
                {"status": "error", "message": holdings.get("message", "Error fetching holdings")},
                500,
            )

        holdings = broker_funcs["map_portfolio_data"](holdings)
        portfolio_stats = broker_funcs["calculate_portfolio_statistics"](holdings)
        holdings = broker_funcs["transform_holdings_data"](holdings)

        formatted_holdings = _format_holdings_data(holdings)
        formatted_stats = _format_statistics(portfolio_stats)

        return (
            True,
            {
                "status": "success",
                "data": {"holdings": formatted_holdings, "statistics": formatted_stats},
            },
            200,
        )
    except Exception as e:
        logger.error("Error processing holdings data: %s", e)
        traceback.print_exc()
        return False, {"status": "error", "message": str(e)}, 500


def get_holdings(
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
    user_id: int | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Get holdings. Supports both API-key and direct auth token calls."""
    if auth_token and broker:
        return get_holdings_with_auth(auth_token, broker, config, user_id=user_id)

    return (
        False,
        {"status": "error", "message": "auth_token and broker must be provided"},
        400,
    )
