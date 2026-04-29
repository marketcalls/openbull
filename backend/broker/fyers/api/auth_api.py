"""
Fyers authentication API - exchange auth code for access token.
Adapted from OpenAlgo's fyers auth_api.py. Key change: accepts config dict.
"""

import hashlib
import json
import logging

import httpx

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def authenticate_broker(code: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange Fyers auth code for an access token.

    config: {"api_key", "api_secret"}
    Returns: (combined_token, error_message)

    Fyers expects every authenticated REST call to carry an Authorization
    header of the form ``{api_key}:{access_token}``. To make downstream
    helpers (order_api, funds, data, margin_api) self-contained we return
    that combined string here as the auth_token — same convention Zerodha
    uses in this codebase.
    """
    try:
        api_key = config.get("api_key")
        api_secret = config.get("api_secret")

        if not all([api_key, api_secret]):
            return None, "Missing broker API credentials in configuration."

        if not code:
            return None, "No auth code provided."

        url = "https://api-t1.fyers.in/api/v3/validate-authcode"

        # Fyers checksum: SHA-256 of "api_key:api_secret"
        checksum_input = f"{api_key}:{api_secret}"
        app_id_hash = hashlib.sha256(checksum_input.encode("utf-8")).hexdigest()

        payload = {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": code,
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        client = get_httpx_client()
        response = client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()

        auth_data = response.json()
        if auth_data.get("s") == "ok":
            access_token = auth_data.get("access_token")
            if not access_token:
                return None, "Authentication succeeded but no access token returned."
            logger.info("Successfully authenticated with Fyers")
            # Store as combined "api_key:access_token" so downstream API helpers
            # can build the Fyers Authorization header without re-reading config.
            # Mirrors the Zerodha integration's token layout.
            combined_token = f"{api_key}:{access_token}"
            return combined_token, None
        else:
            error_msg = auth_data.get("message", "Authentication failed")
            return None, f"Fyers API Error: {error_msg}"

    except httpx.RequestError as e:
        logger.exception("HTTP request error during Fyers authentication")
        return None, f"HTTP request error: {e}"
    except json.JSONDecodeError as e:
        logger.exception("JSON decode error during Fyers authentication")
        return None, f"Invalid JSON response: {e}"
    except Exception:
        logger.exception("Unexpected error during Fyers authentication")
        return None, "Unexpected error during authentication."
