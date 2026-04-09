import hashlib
import logging
from typing import AsyncGenerator

from cachetools import TTLCache
from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.user import User
from backend.models.auth import BrokerAuth, ApiKey
from backend.models.broker_config import BrokerConfig
from backend.security import decode_access_token, verify_api_key, decrypt_value

logger = logging.getLogger(__name__)

# -- Caches --
_verified_api_key_cache: TTLCache = TTLCache(maxsize=64, ttl=36000)  # 10 hours
_invalid_api_key_cache: TTLCache = TTLCache(maxsize=64, ttl=300)  # 5 minutes
_auth_token_cache: TTLCache = TTLCache(maxsize=64, ttl=3600)  # 1 hour


def invalidate_all_caches():
    _verified_api_key_cache.clear()
    _invalid_api_key_cache.clear()
    _auth_token_cache.clear()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    # Attach broker info from JWT to user object for convenience
    user._broker = payload.get("broker")
    return user


class BrokerContext:
    def __init__(self, user: User, auth_token: str, broker_name: str, broker_config: dict):
        self.user = user
        self.auth_token = auth_token
        self.broker_name = broker_name
        self.broker_config = broker_config


async def get_broker_context(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrokerContext:
    broker_name = getattr(user, "_broker", None)
    if not broker_name:
        raise HTTPException(status_code=403, detail="Broker not authenticated. Please complete broker login.")

    # Get broker auth token
    result = await db.execute(
        select(BrokerAuth).where(
            BrokerAuth.user_id == user.id,
            BrokerAuth.broker_name == broker_name,
            BrokerAuth.is_revoked == False,
        )
    )
    broker_auth = result.scalar_one_or_none()
    if not broker_auth:
        raise HTTPException(status_code=403, detail="Broker session expired or revoked")

    auth_token = decrypt_value(broker_auth.access_token)

    # Get broker config (API key, secret for some broker calls)
    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user.id,
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

    return BrokerContext(user=user, auth_token=auth_token, broker_name=broker_name, broker_config=config)


async def get_api_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> tuple[int, str, str, dict]:
    """Resolve API key to (user_id, auth_token, broker_name, broker_config).
    Used by external /api/v1/* endpoints.
    """
    body = await request.json()
    provided_key = body.get("apikey") or request.headers.get("X-API-KEY")
    if not provided_key:
        raise HTTPException(status_code=401, detail="API key required")

    cache_key = hashlib.sha256(provided_key.encode()).hexdigest()

    # Fast reject
    if cache_key in _invalid_api_key_cache:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check verified cache
    if cache_key in _verified_api_key_cache:
        user_id = _verified_api_key_cache[cache_key]
    else:
        # Expensive: verify against all stored keys
        result = await db.execute(select(ApiKey))
        api_keys = result.scalars().all()

        user_id = None
        for ak in api_keys:
            if verify_api_key(provided_key, ak.api_key_hash):
                user_id = ak.user_id
                _verified_api_key_cache[cache_key] = user_id
                break

        if user_id is None:
            _invalid_api_key_cache[cache_key] = True
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Get broker auth
    result = await db.execute(
        select(BrokerAuth).where(
            BrokerAuth.user_id == user_id,
            BrokerAuth.is_revoked == False,
        )
    )
    broker_auth = result.scalar_one_or_none()
    if not broker_auth:
        raise HTTPException(status_code=403, detail="No active broker session")

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
