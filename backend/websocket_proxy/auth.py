"""
Standalone API-key verification for the WebSocket proxy.

Shares the Redis cache namespace with backend.dependencies so HTTP auth and
WebSocket auth populate each other's caches — no duplicate DB lookups.
"""

import hashlib
import logging

from sqlalchemy import select

from backend.database import async_session
from backend.models.auth import ApiKey, BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.security import verify_api_key, decrypt_value
from backend.utils.redis_client import cache_get_json, cache_set_json

logger = logging.getLogger(__name__)

# Shared TTLs with backend.dependencies — keep aligned
API_KEY_TTL = 900       # 15 min
INVALID_KEY_TTL = 300   # 5 min
API_CTX_TTL = 3600      # 1 hour


def _key_api_valid(key_hash: str) -> str:
    return f"api_key:valid:{key_hash}"


def _key_api_invalid(key_hash: str) -> str:
    return f"api_key:invalid:{key_hash}"


def _key_api_ctx(user_id: int) -> str:
    return f"api_ctx:{user_id}"


async def verify_api_key_standalone(
    api_key: str,
) -> tuple[int, str, str, dict]:
    """Verify an API key and return (user_id, auth_token, broker_name, broker_config).

    Raises ValueError on failure.
    """
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    if await cache_get_json(_key_api_invalid(key_hash)) is not None:
        raise ValueError("Invalid API key")

    # Resolve user_id from Redis or DB
    user_id = await cache_get_json(_key_api_valid(key_hash))

    async with async_session() as db:
        if user_id is None:
            result = await db.execute(select(ApiKey))
            api_keys = result.scalars().all()
            for ak in api_keys:
                if verify_api_key(api_key, ak.api_key_hash):
                    user_id = ak.user_id
                    await cache_set_json(_key_api_valid(key_hash), user_id, API_KEY_TTL)
                    break
            if user_id is None:
                await cache_set_json(_key_api_invalid(key_hash), 1, INVALID_KEY_TTL)
                raise ValueError("Invalid API key")

        # Full API context (shared with HTTP /api/v1/*)
        cached_ctx = await cache_get_json(_key_api_ctx(user_id))
        if cached_ctx:
            return (
                user_id,
                cached_ctx["auth_token"],
                cached_ctx["broker_name"],
                cached_ctx["broker_config"],
            )

        # Get broker auth
        result = await db.execute(
            select(BrokerAuth).where(
                BrokerAuth.user_id == user_id,
                BrokerAuth.is_revoked == False,
            )
        )
        broker_auth = result.scalar_one_or_none()
        if not broker_auth:
            raise ValueError("No active broker session")

        auth_token = decrypt_value(broker_auth.access_token)
        broker_name = broker_auth.broker_name

        # Get broker config
        result = await db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user_id,
                BrokerConfig.broker_name == broker_name,
            )
        )
        broker_cfg = result.scalar_one_or_none()
        config = {}
        if broker_cfg:
            config = {
                "api_key": decrypt_value(broker_cfg.api_key),
                "api_secret": decrypt_value(broker_cfg.api_secret),
                "redirect_url": broker_cfg.redirect_url,
            }

        await cache_set_json(
            _key_api_ctx(user_id),
            {"auth_token": auth_token, "broker_name": broker_name, "broker_config": config},
            API_CTX_TTL,
        )

        return (user_id, auth_token, broker_name, config)
