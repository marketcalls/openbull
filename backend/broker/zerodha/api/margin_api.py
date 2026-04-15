"""
Zerodha margin API - calculates margin using Kite basket/orders endpoints.
Adapted from OpenAlgo's zerodha margin_api.py.
"""

import json
import logging

from backend.broker.zerodha.mapping.margin_data import (
    parse_margin_response,
    transform_margin_positions,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code


def calculate_margin_api(positions: list[dict], auth: str) -> tuple:
    """Calculate margin for a basket of positions via Zerodha Kite API."""
    transformed_positions = transform_margin_positions(positions)

    if not transformed_positions:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth}",
        "Content-Type": "application/json",
    }

    if len(transformed_positions) > 1:
        endpoint = "https://api.kite.trade/margins/basket?consider_positions=true"
    else:
        endpoint = "https://api.kite.trade/margins/orders"

    client = get_httpx_client()
    try:
        response = client.post(endpoint, headers=headers, json=transformed_positions)
        response.status = response.status_code

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.error("Failed to parse Zerodha margin response: %s", response.text)
            return response, {"status": "error", "message": "Invalid response from broker API"}

        return response, parse_margin_response(response_data)

    except Exception as e:
        logger.error("Error calling Zerodha margin API: %s", e)
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }
