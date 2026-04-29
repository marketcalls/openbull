"""
Angel One SmartAPI authentication.

Angel does not use OAuth — it requires clientcode + broker PIN + TOTP submitted
to /loginByPassword in exchange for a JWT auth token (and a feed token used by
the WS streamer).

To match the openbull contract `authenticate_broker(code_or_token, config)`,
the first argument is treated as a credentials payload in the form
``"clientcode:broker_pin:totp_code"``. The returned access token is the
combined ``"api_key:jwt_token:feed_token"`` so downstream consumers (REST
+ streaming) can recover everything they need.
"""

import json
import logging

import httpx

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def authenticate_broker(code_or_token: str, config: dict) -> tuple[str | None, str | None]:
    """Authenticate with Angel One SmartAPI.

    Args:
        code_or_token: ``"clientcode:broker_pin:totp_code"`` — the credentials
            the user submitted via the login form.
        config: Broker config dict with at least ``api_key``.

    Returns:
        ``(combined_token, error_message)`` where ``combined_token`` is
        ``"api_key:jwt_token:feed_token"``. Both REST and WS callers split
        on ':' to get the parts they need.
    """
    try:
        api_key = config.get("api_key")
        if not api_key:
            return None, "Missing api_key in broker configuration."

        if not code_or_token or code_or_token.count(":") < 2:
            return None, (
                "Angel credentials must be in the form "
                "'clientcode:broker_pin:totp_code'."
            )

        clientcode, broker_pin, totp_code = code_or_token.split(":", 2)
        clientcode = clientcode.strip()
        broker_pin = broker_pin.strip()
        totp_code = totp_code.strip()

        if not all([clientcode, broker_pin, totp_code]):
            return None, "clientcode, broker_pin and totp_code are all required."

        url = (
            "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
        )
        payload = json.dumps(
            {"clientcode": clientcode, "password": broker_pin, "totp": totp_code}
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "CLIENT_LOCAL_IP",
            "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
            "X-MACAddress": "MAC_ADDRESS",
            "X-PrivateKey": api_key,
        }

        client = get_httpx_client()
        response = client.post(url, headers=headers, content=payload)

        try:
            data_dict = response.json()
        except json.JSONDecodeError:
            return None, f"Angel authentication failed (HTTP {response.status_code})"

        if (
            isinstance(data_dict, dict)
            and "data" in data_dict
            and data_dict["data"]
            and "jwtToken" in data_dict["data"]
        ):
            jwt_token = data_dict["data"]["jwtToken"]
            feed_token = data_dict["data"].get("feedToken", "") or ""
            combined = f"{api_key}:{jwt_token}:{feed_token}"
            logger.info("Successfully authenticated with Angel One")
            return combined, None

        message = "Authentication failed. Please try again."
        if isinstance(data_dict, dict):
            message = data_dict.get("message") or message
        return None, message

    except httpx.RequestError as e:
        logger.exception("HTTP request error during Angel authentication")
        return None, f"HTTP request error: {e}"
    except Exception as e:
        logger.exception("Unexpected error during Angel authentication")
        return None, f"Unexpected error during authentication: {e}"
