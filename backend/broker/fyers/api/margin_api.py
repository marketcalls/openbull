"""
Fyers margin API - calculates margin requirement for a basket of positions.
Adapted from OpenAlgo's fyers margin_api.py.
"""

import json
import logging

from backend.broker.fyers.mapping.margin_data import (
    parse_margin_response,
    transform_margin_positions,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code


def calculate_margin_api(positions: list[dict], auth_token: str) -> tuple:
    """Calculate margin for a basket of positions via Fyers multiorder/margin endpoint.

    The auth_token is the combined ``"api_key:access_token"`` string.

    Returns ``(response_or_mock, response_data_dict)`` to mirror the upstox /
    zerodha integrations — callers index into both halves.
    """
    transformed_positions = transform_margin_positions(positions)

    if not transformed_positions:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    headers = {
        "Authorization": auth_token,
        "Content-Type": "application/json",
    }
    payload = {"data": transformed_positions}

    client = get_httpx_client()
    try:
        response = client.post(
            "https://api-t1.fyers.in/api/v3/multiorder/margin",
            headers=headers, json=payload,
        )
        response.status = response.status_code

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.error("Failed to parse Fyers margin response: %s", response.text)
            return response, {"status": "error", "message": "Invalid response from broker API"}

        return response, parse_margin_response(response_data)

    except Exception as e:
        logger.error("Error calling Fyers margin API: %s", e)
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }
