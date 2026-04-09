import hashlib
import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def authenticate_broker(request_token: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange Zerodha request_token for an access token via checksum verification.
    config: {"api_key", "api_secret"}
    Returns: (access_token, error_message)
    """
    try:
        api_key = config.get("api_key")
        api_secret = config.get("api_secret")

        if not all([api_key, api_secret]):
            return None, "Missing broker API credentials in configuration."

        url = "https://api.kite.trade/session/token"

        # SHA-256 checksum of api_key + request_token + api_secret
        checksum_input = f"{api_key}{request_token}{api_secret}"
        checksum = hashlib.sha256(checksum_input.encode()).hexdigest()

        data = {
            "api_key": api_key,
            "request_token": request_token,
            "checksum": checksum,
        }

        client = get_httpx_client()
        headers = {"X-Kite-Version": "3"}

        response = client.post(url, headers=headers, data=data)
        response.raise_for_status()

        response_data = response.json()
        if "data" in response_data and "access_token" in response_data["data"]:
            logger.info("Successfully authenticated with Zerodha")
            return response_data["data"]["access_token"], None
        else:
            return None, "Authentication succeeded but no access token returned."

    except Exception as e:
        error_message = str(e)
        try:
            if hasattr(e, "response") and e.response is not None:
                error_detail = e.response.json()
                error_message = error_detail.get("message", str(e))
        except Exception:
            pass
        logger.exception("Error during Zerodha authentication")
        return None, f"Zerodha API error: {error_message}"
