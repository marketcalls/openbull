import json
import logging

import httpx

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def authenticate_broker(code: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange OAuth authorization code for an Upstox access token.
    config: {"api_key", "api_secret", "redirect_url"}
    Returns: (access_token, error_message)
    """
    try:
        api_key = config.get("api_key")
        api_secret = config.get("api_secret")
        redirect_url = config.get("redirect_url")

        if not all([api_key, api_secret, redirect_url]):
            return None, "Missing broker API credentials in configuration."

        url = "https://api.upstox.com/v2/login/authorization/token"
        data = {
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
        }

        client = get_httpx_client()
        response = client.post(url, data=data)

        if response.status_code == 200:
            response_data = response.json()
            access_token = response_data.get("access_token")
            if access_token:
                logger.info("Successfully authenticated with Upstox")
                return access_token, None
            else:
                return None, "Authentication succeeded but no access token returned."
        else:
            try:
                error_detail = response.json()
                errors = error_detail.get("errors", [])
                detailed_message = "; ".join(
                    [err.get("message", "Unknown error") for err in errors]
                )
                return None, f"Upstox API Error: {detailed_message}"
            except json.JSONDecodeError:
                return None, f"Upstox authentication failed (HTTP {response.status_code})"

    except httpx.RequestError as e:
        logger.exception("HTTP request error during Upstox authentication")
        return None, f"HTTP request error: {e}"
    except Exception:
        logger.exception("Unexpected error during Upstox authentication")
        return None, "Unexpected error during authentication."
