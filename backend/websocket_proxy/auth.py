"""
Standalone API-key verification for the WebSocket proxy.
Mirrors backend.dependencies.get_api_user but without FastAPI Request/Depends.
"""

import hashlib
import logging

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings
from backend.models.auth import ApiKey, BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.security import verify_api_key, decrypt_value

logger = logging.getLogger(__name__)

_verified_cache: TTLCache = TTLCache(maxsize=64, ttl=900)  # 15 min (not 10h)
_invalid_cache: TTLCache = TTLCache(maxsize=64, ttl=300)

# Singleton engine — created once, reused across all verify calls
_engine = None
_session_factory = None


def _get_session_factory():
    global _engine, _session_factory
    if _session_factory is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False)
        _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def verify_api_key_standalone(
    api_key: str,
) -> tuple[int, str, str, dict]:
    """Verify an API key and return (user_id, auth_token, broker_name, broker_config).

    Raises ValueError on failure.
    """
    cache_key = hashlib.sha256(api_key.encode()).hexdigest()

    if cache_key in _invalid_cache:
        raise ValueError("Invalid API key")

    session_factory = _get_session_factory()

    async with session_factory() as db:
            # Resolve user_id
            if cache_key in _verified_cache:
                user_id = _verified_cache[cache_key]
            else:
                result = await db.execute(select(ApiKey))
                api_keys = result.scalars().all()
                user_id = None
                for ak in api_keys:
                    if verify_api_key(api_key, ak.api_key_hash):
                        user_id = ak.user_id
                        _verified_cache[cache_key] = user_id
                        break
                if user_id is None:
                    _invalid_cache[cache_key] = True
                    raise ValueError("Invalid API key")

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

            return (user_id, auth_token, broker_name, config)
