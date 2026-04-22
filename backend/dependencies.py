import hashlib
import logging
from typing import AsyncGenerator

from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.user import User
from backend.models.auth import BrokerAuth, ApiKey
from backend.models.audit import ActiveSession
from backend.models.broker_config import BrokerConfig
from backend.security import decode_access_token, verify_api_key, decrypt_value
from backend.utils.redis_client import (
    cache_delete,
    cache_delete_pattern,
    cache_get_json,
    cache_set_json,
)

logger = logging.getLogger(__name__)

# -- Cache TTLs (seconds) --
API_KEY_TTL = 900         # 15 min
INVALID_KEY_TTL = 300     # 5 min
BROKER_CTX_TTL = 3600     # 1 hour
API_CTX_TTL = 3600        # 1 hour


def _key_api_valid(key_hash: str) -> str:
    return f"api_key:valid:{key_hash}"


def _key_api_invalid(key_hash: str) -> str:
    return f"api_key:invalid:{key_hash}"


def _key_broker_ctx(user_id: int) -> str:
    return f"broker_ctx:{user_id}"


def _key_api_ctx(user_id: int) -> str:
    return f"api_ctx:{user_id}"


async def invalidate_all_caches() -> None:
    """Clear all API key / broker context caches. Called on API key rotation or logout."""
    await cache_delete_pattern("api_key:*")
    await cache_delete_pattern("broker_ctx:*")
    await cache_delete_pattern("api_ctx:*")


async def invalidate_user_cache(user_id: int) -> None:
    """Clear cached broker context for a single user. Call on logout / re-auth."""
    await cache_delete(_key_broker_ctx(user_id), _key_api_ctx(user_id))


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

    # Enforce server-side session revocation: the JWT's jti must match a
    # live ActiveSession row for this user. Logout (and "log out all
    # devices") deletes those rows, invalidating any outstanding cookies
    # even though the JWT itself is still cryptographically valid.
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    session_result = await db.execute(
        select(ActiveSession).where(
            ActiveSession.user_id == int(user_id),
            ActiveSession.session_token == jti,
        )
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="Session revoked")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    # Attach broker info from JWT to user object for convenience
    user._broker = payload.get("broker")
    user._session_token = jti
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
    # Try Redis first — stores decrypted broker_name, auth_token, config
    cached = await cache_get_json(_key_broker_ctx(user.id))
    if cached:
        return BrokerContext(
            user=user,
            auth_token=cached["auth_token"],
            broker_name=cached["broker_name"],
            broker_config=cached["broker_config"],
        )

    broker_name = getattr(user, "_broker", None)

    # If JWT doesn't have broker claim (cookie domain mismatch after OAuth),
    # check DB for the active broker config
    if not broker_name:
        result = await db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user.id,
                BrokerConfig.is_active == True,
            )
        )
        active_config = result.scalar_one_or_none()
        if active_config:
            broker_name = active_config.broker_name

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

    await cache_set_json(
        _key_broker_ctx(user.id),
        {"broker_name": broker_name, "auth_token": auth_token, "broker_config": config},
        BROKER_CTX_TTL,
    )

    return BrokerContext(user=user, auth_token=auth_token, broker_name=broker_name, broker_config=config)


async def get_api_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> tuple[int, str, str, dict]:
    """Resolve API key to (user_id, auth_token, broker_name, broker_config).
    Used by external /api/v1/* endpoints.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    provided_key = body.get("apikey") or request.headers.get("X-API-KEY")
    if not provided_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hashlib.sha256(provided_key.encode()).hexdigest()

    # Fast reject from negative cache
    if await cache_get_json(_key_api_invalid(key_hash)) is not None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Positive cache: key_hash -> user_id
    user_id = await cache_get_json(_key_api_valid(key_hash))

    if user_id is None:
        # Expensive: verify against all stored keys
        result = await db.execute(select(ApiKey))
        api_keys = result.scalars().all()

        for ak in api_keys:
            if verify_api_key(provided_key, ak.api_key_hash):
                user_id = ak.user_id
                await cache_set_json(_key_api_valid(key_hash), user_id, API_KEY_TTL)
                break

        if user_id is None:
            await cache_set_json(_key_api_invalid(key_hash), 1, INVALID_KEY_TTL)
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Try full api-context cache (saves 2 more DB hits + 3 decrypts)
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

    await cache_set_json(
        _key_api_ctx(user_id),
        {"auth_token": auth_token, "broker_name": broker_name, "broker_config": config},
        API_CTX_TTL,
    )

    return (user_id, auth_token, broker_name, config)
