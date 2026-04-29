"""
Angel One funds / margin (RMS) API.
Adapted from OpenAlgo's angel funds.py. Key change: accepts config dict and
splits the combined ``api_key:jwt_token:feed_token`` auth_token issued by
``angel.api.auth_api``.
"""

import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def _split_token(auth_token: str, config: dict | None) -> tuple[str, str]:
    """Split combined token into (api_key, jwt_token).

    The combined form is ``"api_key:jwt_token:feed_token"`` (see auth_api).
    Falls back to ``config["api_key"]`` if the token is just the JWT.
    """
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 2:
        return parts[0], parts[1]
    api_key = (config or {}).get("api_key", "")
    return api_key, auth_token or ""


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


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin / RMS data from Angel One.

    Returns dict with stringified values (matches openbull funds convention).
    """
    api_key, jwt_token = _split_token(auth_token, config)

    if not api_key or not jwt_token:
        logger.error("Missing api_key or jwt_token for Angel margin call")
        return {}

    try:
        client = get_httpx_client()
        response = client.get(
            "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/getRMS",
            headers=_angel_headers(api_key, jwt_token),
        )
        margin_data = response.json()
    except Exception as e:
        logger.error("Error fetching Angel margin data: %s", e)
        return {}

    if not margin_data or not margin_data.get("data"):
        logger.error(
            "Angel margin response missing data (status=%s, message=%s)",
            margin_data.get("status") if isinstance(margin_data, dict) else None,
            margin_data.get("message") if isinstance(margin_data, dict) else None,
        )
        return {}

    data = margin_data["data"]

    # Collateral is computed as availablecash - utilisedpayout (matches openalgo).
    availablecash = 0.0
    calculated_collateral = 0.0
    try:
        availablecash = float(data.get("availablecash", 0) or 0)
        utilisedpayout = float(data.get("utilisedpayout", 0) or 0)
        calculated_collateral = availablecash - utilisedpayout
    except (ValueError, TypeError):
        pass

    return {
        "availablecash": f"{availablecash:.2f}",
        "collateral": f"{calculated_collateral:.2f}",
        "m2mrealized": f"{float(data.get('m2mrealized', 0) or 0):.2f}",
        "m2munrealized": f"{float(data.get('m2munrealized', 0) or 0):.2f}",
        "utiliseddebits": f"{float(data.get('utiliseddebits', 0) or 0):.2f}",
    }
