"""
Upstox funds API - fetch margin and funds data.
Adapted from OpenAlgo's upstox funds.py. Key change: accepts config dict.
"""

import json
import logging

import httpx

from backend.broker.upstox.api.order_api import get_holdings, get_positions
from backend.broker.upstox.mapping.order_data import map_order_data
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def calculate_total_collateral(holdings: list[dict]) -> float:
    """Calculate total potential collateral value from holdings."""
    total = 0.0
    for h in holdings:
        qty = h.get("quantity", 0)
        price = h.get("average_price", 0.0)
        haircut = h.get("haircut", 0.0)
        holding_value = qty * price
        collateral_value = holding_value * (1 - haircut)
        total += collateral_value
    return round(total, 2)


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin data from Upstox API.

    Args:
        auth_token: Upstox access token
        config: Broker config dict with api_key, api_secret (optional, not needed for margin)
    """
    logger.debug("Fetching Upstox margin data")
    try:
        client = get_httpx_client()
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = client.get("https://api.upstox.com/v2/user/get-funds-and-margin", headers=headers)
        response.raise_for_status()
        margin_data = response.json()

        if margin_data.get("status") == "error":
            logger.error("API error fetching margin data: %s", margin_data.get("errors"))
            return {}

        total_available_margin = sum([
            margin_data["data"]["commodity"]["available_margin"],
            margin_data["data"]["equity"]["available_margin"],
        ])
        total_used_margin = sum([
            margin_data["data"]["commodity"]["used_margin"],
            margin_data["data"]["equity"]["used_margin"],
        ])

        # Calculate PnL from positions
        total_realised = 0.0
        total_unrealised = 0.0
        try:
            position_book = get_positions(auth_token)
            if position_book and position_book.get("status") == "success" and "data" in position_book:
                mapped = map_order_data(position_book)
                total_realised = sum(p.get("realised", 0) for p in mapped)
                total_unrealised = sum(p.get("unrealised", 0) for p in mapped)
        except Exception as e:
            logger.warning("Failed to fetch positions for margin calc: %s", e)

        # Get holdings and calculate collateral
        total_collateral = 0.0
        try:
            holdings_response = get_holdings(auth_token)
            if holdings_response.get("status") == "success" and holdings_response.get("data"):
                total_collateral = calculate_total_collateral(holdings_response["data"])
        except Exception as e:
            logger.warning("Failed to fetch holdings for collateral calc: %s", e)

        return {
            "availablecash": round(total_available_margin, 2),
            "collateral": round(total_collateral, 2),
            "m2munrealized": round(total_unrealised, 2),
            "m2mrealized": round(total_realised, 2),
            "utiliseddebits": round(total_used_margin, 2),
        }

    except httpx.HTTPStatusError as e:
        # Handle service hours error (423 Locked)
        if e.response.status_code == 423:
            try:
                error_data = e.response.json()
                if error_data.get("status") == "error":
                    for error in error_data.get("errors", []):
                        if error.get("errorCode") == "UDAPI100072":
                            logger.info("Upstox funds service outside operating hours. Returning defaults.")
                            return {
                                "availablecash": 0.0,
                                "collateral": 0.0,
                                "m2munrealized": 0.0,
                                "m2mrealized": 0.0,
                                "utiliseddebits": 0.0,
                            }
            except json.JSONDecodeError:
                pass

        logger.error("HTTP error fetching margin data: %s", e.response.text)
        return {}
    except (KeyError, TypeError) as e:
        logger.error("Error processing margin data structure: %s", e)
        return {}
    except Exception:
        logger.error("Unexpected error fetching margin data")
        return {}
