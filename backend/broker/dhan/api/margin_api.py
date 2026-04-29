"""
Dhan margin API - calculates margin requirement for a basket of positions.

Dhan's margin calculator API accepts only ONE order at a time. For multi-leg
strategies we calculate each leg individually and SUM the margins. This does
NOT account for spread/hedge benefits — Dhan API limitation.
"""

import json
import logging

from backend.broker.dhan.mapping.margin_data import (
    parse_batch_margin_response,
    parse_margin_response,
    transform_margin_positions,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

DHAN_BASE_URL = "https://api.dhan.co"


class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code


def _calculate_single_margin(payload: dict, auth: str, client_id: str | None) -> tuple:
    """Call Dhan margin calculator for one payload, return (response, parsed)."""
    headers = {
        "access-token": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if client_id:
        headers["client-id"] = client_id

    body = json.dumps(payload)
    client = get_httpx_client()
    try:
        response = client.post(
            f"{DHAN_BASE_URL}/v2/margincalculator", headers=headers, content=body
        )
        response.status = response.status_code

        try:
            response_data = response.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse JSON margin response: %s", response.text[:200])
            return response, {"status": "error", "message": "Invalid response from broker API"}

        return response, parse_margin_response(response_data)

    except Exception as e:
        logger.exception("Error calling Dhan margin API")
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }


def calculate_margin_api(positions: list[dict], auth: str, config: dict | None = None) -> tuple:
    """Calculate margin for a basket of positions via Dhan API.

    Returns (response, response_data) tuple matching openbull convention.
    """
    client_id: str | None = None
    if config:
        client_id = config.get("client_id") or config.get("dhan_client_id")
        if not client_id:
            api_key = config.get("api_key") or ""
            if ":::" in api_key:
                client_id, _, _ = api_key.partition(":::")

    if not client_id:
        return _MockResponse(400), {
            "status": "error",
            "message": "Could not determine Dhan client ID. Please configure broker credentials.",
        }

    transformed_positions = transform_margin_positions(positions, client_id=client_id)
    if not transformed_positions:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    margin_responses: list[dict] = []
    last_response = None

    for idx, payload in enumerate(transformed_positions, 1):
        logger.info(
            "Calculating Dhan margin for leg %d/%d: %s",
            idx, len(transformed_positions), payload.get("securityId"),
        )
        response, parsed = _calculate_single_margin(payload, auth, client_id)
        last_response = response
        margin_responses.append(parsed)

    if len(margin_responses) == 1:
        final_response = margin_responses[0]
    else:
        final_response = parse_batch_margin_response(margin_responses)

    return last_response or _MockResponse(200), final_response
