"""
Upstox margin API - calculates margin requirement for a basket of positions.
Adapted from OpenAlgo's upstox margin_api.py.
"""

import json
import logging

from backend.broker.upstox.mapping.margin_data import (
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
    """Calculate margin for a basket of positions via Upstox API."""
    transformed_positions = transform_margin_positions(positions)

    if not transformed_positions:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    if len(transformed_positions) > 20:
        return _MockResponse(400), {
            "status": "error",
            "message": "Upstox supports maximum 20 instruments per margin request.",
        }

    headers = {
        "Authorization": f"Bearer {auth}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"instruments": transformed_positions}

    client = get_httpx_client()
    try:
        response = client.post(
            "https://api.upstox.com/v2/charges/margin", headers=headers, json=payload
        )
        response.status = response.status_code

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.error("Failed to parse margin response: %s", response.text)
            return response, {"status": "error", "message": "Invalid response from broker API"}

        return response, parse_margin_response(response_data)

    except Exception as e:
        logger.error("Error calling Upstox margin API: %s", e)
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }
