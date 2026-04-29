"""
Fyers funds API - fetch margin and funds data.
Adapted from OpenAlgo's fyers funds.py.

The auth_token here is the combined ``"api_key:access_token"`` string produced
by ``authenticate_broker`` (mirrors the Zerodha layout).
"""

import json
import logging

import httpx

from backend.broker.fyers.api.order_api import get_positions
from backend.broker.fyers.mapping.order_data import map_position_data
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


_DEFAULT_RESPONSE = {
    "availablecash": "0.00",
    "collateral": "0.00",
    "m2munrealized": "0.00",
    "m2mrealized": "0.00",
    "utiliseddebits": "0.00",
}


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin data from Fyers API.

    Args:
        auth_token: Combined ``"api_key:access_token"`` string.
        config: Optional broker config dict (unused for funds; kept for contract).

    Returns a dict where every value is a ``"{x:.2f}"`` formatted STRING — this
    matches the openbull funds convention (zerodha follows the same shape).
    """
    headers = {
        "Authorization": auth_token,
        "Content-Type": "application/json",
    }

    try:
        client = get_httpx_client()
        response = client.get(
            "https://api-t1.fyers.in/api/v3/funds", headers=headers, timeout=30.0,
        )
        response.raise_for_status()
        funds_data = response.json()

        if funds_data.get("code") != 200:
            logger.error("Error in Fyers funds API: %s", funds_data.get("message"))
            return dict(_DEFAULT_RESPONSE)

        # Process the funds data into a per-title dict.
        processed_funds: dict = {}
        for fund in funds_data.get("fund_limit", []):
            try:
                key = fund["title"].lower().replace(" ", "_")
                processed_funds[key] = {
                    "equity_amount": float(fund.get("equityAmount", 0) or 0),
                    "commodity_amount": float(fund.get("commodityAmount", 0) or 0),
                }
            except (KeyError, ValueError) as e:
                logger.warning("Error processing fund entry: %s", e)
                continue

        def _sum(key: str) -> float:
            entry = processed_funds.get(key, {})
            return float(entry.get("equity_amount", 0) or 0) + float(
                entry.get("commodity_amount", 0) or 0
            )

        total_balance = _sum("available_balance")
        total_collateral = _sum("collaterals")
        total_real_pnl = _sum("realized_profit_and_loss")
        total_utilized = _sum("utilized_amount")

        # Fyers' funds endpoint reports realized pnl, but unrealized pnl has to
        # come from the position book. Mirror the openalgo logic.
        total_unrealised = 0.0
        total_realised_from_positions = 0.0
        try:
            position_book_raw = get_positions(auth_token)
            position_book = map_position_data(position_book_raw)
            for p in position_book:
                total_realised_from_positions += float(p.get("realized_profit", 0) or 0)
                total_unrealised += float(p.get("unrealized_profit", 0) or 0)
        except Exception as e:
            logger.warning("Could not fetch positions for unrealized P&L: %s", e)

        # Prefer the per-position realized total when available; fall back to
        # the funds-endpoint figure.
        total_realised = total_realised_from_positions or total_real_pnl

        return {
            "availablecash": f"{total_balance:.2f}",
            "collateral": f"{total_collateral:.2f}",
            "m2munrealized": f"{total_unrealised:.2f}",
            "m2mrealized": f"{total_realised:.2f}",
            "utiliseddebits": f"{total_utilized:.2f}",
        }

    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error %s fetching Fyers funds: %s",
            e.response.status_code, e.response.text,
        )
    except httpx.RequestError as e:
        logger.error("Request failed fetching Fyers funds: %s", e)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Fyers funds response: %s", e)
    except Exception:
        logger.exception("Unexpected error in Fyers get_margin_data")

    return dict(_DEFAULT_RESPONSE)
