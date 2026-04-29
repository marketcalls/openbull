"""
Angel One margin calculator API.
Adapted from OpenAlgo's angel margin_api.py.
"""

import json
import logging

from backend.broker.angel.mapping.margin_data import (
    parse_margin_response,
    transform_margin_positions,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


class _MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code


def _split_token(auth_token: str) -> tuple[str, str]:
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", auth_token or ""


def _angel_headers(api_key: str, jwt_token: str) -> dict:
    return {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "CLIENT_LOCAL_IP",
        "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
        "X-MACAddress": "MAC_ADDRESS",
        "X-PrivateKey": api_key,
    }


def calculate_margin_api(positions: list[dict], auth_token: str) -> tuple:
    """Calculate margin requirement for a basket of positions via Angel API."""
    transformed_positions = transform_margin_positions(positions)

    if not transformed_positions:
        return _MockResponse(400), {
            "status": "error",
            "message": "No valid positions to calculate margin. Check if symbols are valid.",
        }

    api_key, jwt_token = _split_token(auth_token)
    headers = _angel_headers(api_key, jwt_token)
    payload = json.dumps({"positions": transformed_positions})
    logger.info("Angel margin payload: %s", payload)

    client = get_httpx_client()
    try:
        response = client.post(
            "https://apiconnect.angelone.in/rest/secure/angelbroking/margin/v1/batch",
            headers=headers,
            content=payload,
        )
        response.status = response.status_code

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.error("Failed to parse Angel margin response: %s", response.text)
            return response, {
                "status": "error",
                "message": "Invalid response from broker API",
            }

        logger.info("Angel margin response: %s", response_data)
        return response, parse_margin_response(response_data)

    except Exception as e:
        logger.error("Error calling Angel margin API: %s", e)
        return _MockResponse(500), {
            "status": "error",
            "message": f"Failed to calculate margin: {e}",
        }
