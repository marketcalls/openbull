"""
Dhan auth API.
Adapted from OpenAlgo's dhan auth_api.py. Key change: accepts config dict.

Dhan supports two auth flows:
  1. Direct access token (long JWT pasted by user) — preferred for openbull.
  2. OAuth-style consent flow: generate consent -> user logs in -> consume consent.

For openbull's contract `authenticate_broker(code_or_token, config)` we
accept either a long-form access token OR a tokenId from the consent
flow. config may contain {"api_key": app_id, "api_secret": app_secret}.
"""

import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

AUTH_BASE_URL = "https://auth.dhan.co"


def _consume_consent(token_id: str, app_id: str, app_secret: str) -> tuple[str | None, str | None]:
    """Exchange a Dhan tokenId for an access token via consume-consent."""
    try:
        client = get_httpx_client()
        headers = {
            "app_id": app_id,
            "app_secret": app_secret,
            "Content-Type": "application/json",
        }
        url = f"{AUTH_BASE_URL}/app/consumeApp-consent"
        params = {"tokenId": token_id}
        response = client.post(url, headers=headers, params=params)

        if response.status_code != 200:
            return None, f"Failed to consume consent: HTTP {response.status_code}"

        data = response.json()
        access_token = data.get("accessToken")
        if not access_token:
            return None, "Access token not found in consume-consent response"
        logger.info("Dhan access token obtained via consume-consent flow")
        return access_token, None
    except Exception as e:
        logger.exception("Error consuming Dhan consent")
        return None, f"Exception consuming consent: {e}"


def authenticate_broker(code_or_token: str, config: dict) -> tuple[str | None, str | None]:
    """Authenticate with Dhan.

    Accepts either:
      - a long-form access token (JWT pasted by user from Dhan web)
      - a tokenId from Dhan's consent OAuth flow

    config: {"api_key": app_id, "api_secret": app_secret}
    Returns: (access_token, error_message)
    """
    try:
        if not code_or_token:
            return None, "No token provided for Dhan authentication."

        # Direct access tokens are long JWTs (typically > 100 chars).
        if len(code_or_token) > 100:
            logger.info("Using direct Dhan access token from input")
            return code_or_token, None

        # Otherwise, treat as a tokenId from the consent flow.
        app_id = config.get("api_key") if config else None
        app_secret = config.get("api_secret") if config else None
        if not app_id or not app_secret:
            return None, "Missing Dhan app_id / app_secret in configuration."

        return _consume_consent(code_or_token, app_id, app_secret)

    except Exception as e:
        logger.exception("Unexpected error during Dhan authentication")
        return None, f"Unexpected error during authentication: {e}"
