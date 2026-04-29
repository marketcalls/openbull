"""
Dhan funds API - fetch margin and funds data.
Adapted from OpenAlgo's dhan funds.py. Returns f"{x:.2f}" STRINGS for the
margin dict (matches openbull convention).
"""

import json
import logging

from backend.broker.dhan.api.order_api import get_positions
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

DHAN_BASE_URL = "https://api.dhan.co"


def _zero_margin() -> dict:
    return {
        "availablecash": "0.00",
        "collateral": "0.00",
        "m2munrealized": "0.00",
        "m2mrealized": "0.00",
        "utiliseddebits": "0.00",
    }


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch Dhan margin data.

    Returns dict with f"{x:.2f}" STRING values (matches openbull convention).
    """
    try:
        client = get_httpx_client()
        headers = {
            "access-token": auth_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = client.get(f"{DHAN_BASE_URL}/v2/fundlimit", headers=headers)
        try:
            margin_data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            logger.error("Invalid Dhan fund response: %s", response.text[:200])
            return _zero_margin()

        if not isinstance(margin_data, dict):
            return _zero_margin()

        if margin_data.get("errorType") == "Invalid_Authentication":
            logger.error("Authentication error: %s", margin_data.get("errorMessage"))
            return _zero_margin()

        if margin_data.get("status") == "error":
            logger.error("Error fetching Dhan margin data: %s", margin_data.get("errors"))
            return _zero_margin()

        # Aggregate realized/unrealized PnL from positions
        total_realised = 0.0
        total_unrealised = 0.0
        try:
            position_book = get_positions(auth_token)
            if isinstance(position_book, dict) and position_book.get("errorType"):
                logger.error("Error getting positions: %s", position_book.get("errorMessage"))
            elif isinstance(position_book, list):
                total_realised = sum(
                    float(p.get("realizedProfit") or 0) for p in position_book
                )
                total_unrealised = sum(
                    float(p.get("unrealizedProfit") or 0) for p in position_book
                )
        except Exception as e:
            logger.warning("Failed to fetch positions for PnL calc: %s", e)

        return {
            "availablecash": "{:.2f}".format(float(margin_data.get("availabelBalance") or 0)),
            "collateral": "{:.2f}".format(float(margin_data.get("collateralAmount") or 0)),
            "m2munrealized": f"{total_unrealised:.2f}",
            "m2mrealized": f"{total_realised:.2f}",
            "utiliseddebits": "{:.2f}".format(float(margin_data.get("utilizedAmount") or 0)),
        }

    except Exception:
        logger.exception("Unexpected error fetching Dhan margin data")
        return _zero_margin()
