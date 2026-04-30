"""
Dhan auth API.
Adapted from OpenAlgo's dhan auth_api.py. Key change: accepts config dict.

Dhan supports two auth flows:
  1. Direct access token (long JWT pasted by user) -- preferred for openbull.
  2. OAuth-style consent flow: generate consent -> user logs in -> consume consent.

For openbull's contract `authenticate_broker(code_or_token, config)` we
accept either a long-form access token OR a tokenId from the consent
flow. config may contain {"api_key": app_id, "api_secret": app_secret,
"client_id": dhan_client_id}.
"""

import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

AUTH_BASE_URL = "https://auth.dhan.co"


def generate_consent(client_id: str, app_id: str, app_secret: str) -> tuple[str | None, str | None]:
    """Step 1 of Dhan OAuth: generate a consent session for the given Dhan ClientID.

    Returns (consent_app_id, error). The caller redirects the user to
    https://auth.dhan.co/login/consentApp-login?consentAppId={consent_app_id}.
    """
    try:
        if not client_id or not app_id or not app_secret:
            return None, "Missing client_id / app_id / app_secret for Dhan consent."

        client = get_httpx_client()
        headers = {"app_id": app_id, "app_secret": app_secret}
        url = f"{AUTH_BASE_URL}/app/generate-consent?client_id={client_id}"

        response = client.post(url, headers=headers)
        if response.status_code != 200:
            return None, f"generate-consent HTTP {response.status_code}: {response.text}"

        data = response.json()
        if data.get("status") != "success":
            return None, f"generate-consent failed: {data}"

        consent_app_id = data.get("consentAppId")
        if not consent_app_id:
            return None, "consentAppId missing in generate-consent response."

        logger.info("Dhan consent generated for client_id %s", client_id)
        return consent_app_id, None

    except Exception as e:
        logger.exception("Error generating Dhan consent")
        return None, f"Exception generating consent: {e}"


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

    config: {"api_key": app_id, "api_secret": app_secret, "client_id": ...}
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
