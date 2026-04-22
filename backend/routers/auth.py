import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.database import async_session
from backend.dependencies import get_db, get_current_user, invalidate_user_cache
from backend.limiter import limiter
from backend.models.user import User
from backend.models.auth import BrokerAuth, ApiKey
from backend.models.broker_config import BrokerConfig
from backend.models.audit import LoginAttempt, ActiveSession
from backend.schemas.auth import SetupRequest, LoginRequest, AuthResponse, UserInfo
from backend.security import (
    hash_password, verify_password, check_needs_rehash, create_access_token,
    generate_api_key, hash_api_key, encrypt_value, decrypt_value,
)
from backend.utils.plugin_loader import get_broker_module

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

# Pre-computed Argon2 hash over a random throwaway secret, used to equalize
# response time when the submitted username doesn't exist. Without this,
# /auth/login leaks username existence through timing (Argon2 verify takes
# ~100s of ms; short-circuiting leaks the answer).
_TIMING_DUMMY_HASH = hash_password(secrets.token_hex(16))


@router.get("/check-setup")
async def check_setup(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).select_from(User))
    user_count = result.scalar()
    return {"needs_setup": user_count == 0}


@router.post("/setup", response_model=AuthResponse)
@limiter.limit("5 per hour")
async def setup(data: SetupRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Only allow setup if no users exist
    result = await db.execute(select(func.count()).select_from(User))
    if result.scalar() > 0:
        raise HTTPException(status_code=403, detail="Setup already completed. Only one admin user is allowed.")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        is_admin=True,
    )
    db.add(user)
    await db.flush()  # Get user.id before committing

    # Auto-generate API key for the new admin user
    new_api_key = generate_api_key()
    db.add(ApiKey(
        user_id=user.id,
        api_key_hash=hash_api_key(new_api_key),
        api_key_encrypted=encrypt_value(new_api_key),
    ))

    await db.commit()
    logger.info("Admin user '%s' created during setup with API key", data.username)
    return AuthResponse(status="success", message="Admin account created. Please login.")


async def _resume_broker_if_valid(
    db: AsyncSession,
    user: User,
    broker_auth: BrokerAuth | None,
) -> str | None:
    """Validate a stored broker access_token with a lightweight funds call.

    Returns the broker_name to stamp into the JWT if the broker still accepts
    the token. If the token is rejected (expired/revoked upstream), revokes
    the stale BrokerAuth row, clears cached context, and returns None so the
    user is pushed back through /broker/select -> OAuth on the next request.
    """
    if not broker_auth:
        return None

    candidate_broker = broker_auth.broker_name
    try:
        auth_token = decrypt_value(broker_auth.access_token)

        cfg_result = await db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user.id,
                BrokerConfig.broker_name == candidate_broker,
            )
        )
        broker_cfg = cfg_result.scalar_one_or_none()
        broker_config: dict = {}
        if broker_cfg:
            broker_config = {
                "api_key": decrypt_value(broker_cfg.api_key),
                "api_secret": decrypt_value(broker_cfg.api_secret),
                "redirect_url": broker_cfg.redirect_url,
            }

        funds_mod = get_broker_module(candidate_broker, "funds")
        margin = await run_in_threadpool(
            funds_mod.get_margin_data, auth_token, broker_config
        )
    except Exception as exc:
        logger.warning(
            "Broker session resume check failed for user %s (%s): %s",
            user.username, candidate_broker, exc,
        )
        broker_auth.is_revoked = True
        await invalidate_user_cache(user.id)
        return None

    if not margin:
        logger.info(
            "Stored broker token for user %s on %s is no longer valid; revoking",
            user.username, candidate_broker,
        )
        broker_auth.is_revoked = True
        await invalidate_user_cache(user.id)
        return None

    logger.info(
        "Resumed broker session for user %s on %s",
        user.username, candidate_broker,
    )
    return candidate_broker


@router.post("/login", response_model=AuthResponse)
@limiter.limit(settings.login_rate_limit_min)
@limiter.limit(settings.login_rate_limit_hour)
async def login(data: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip_address = request.client.host if request.client else "unknown"
    device_info = request.headers.get("User-Agent", "")[:500]

    result = await db.execute(select(User).where(User.username == data.username.strip().lower()))
    user = result.scalar_one_or_none()

    # Always run Argon2 verify — even when the user is absent — so the
    # response time does not reveal whether the username exists.
    password_ok = verify_password(
        data.password,
        user.password_hash if user else _TIMING_DUMMY_HASH,
    )

    if not user or not password_ok:
        # Log failed attempt
        db.add(LoginAttempt(
            username=data.username,
            ip_address=ip_address,
            device_info=device_info,
            status="failed",
            login_type="password",
            failure_reason="invalid_credentials",
        ))
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Rehash if needed
    if check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)

    # Attempt session resume: keep the broker claim only if the stored
    # access token still works at the broker. Broker tokens (Zerodha/Upstox)
    # expire daily on the broker's side, so is_revoked alone is not a safe
    # signal — a stale row from yesterday would otherwise send the user
    # straight to /dashboard with a token that fails on first API call.
    result = await db.execute(
        select(BrokerAuth).where(
            BrokerAuth.user_id == user.id,
            BrokerAuth.is_revoked == False,
        )
    )
    broker_auth = result.scalar_one_or_none()
    broker_name = await _resume_broker_if_valid(db, user, broker_auth)

    # Generate an ActiveSession row up front; the JWT carries its token as
    # `jti`, and get_current_user requires that the row still exist. This
    # turns logout (which deletes the row) into real server-side revocation
    # and makes "log out all devices" actually invalidate stolen cookies.
    session_token = secrets.token_hex(32)

    token = create_access_token(data={
        "sub": str(user.id),
        "username": user.username,
        "broker": broker_name,
        "jti": session_token,
    })

    # Log success
    db.add(LoginAttempt(
        username=user.username,
        ip_address=ip_address,
        device_info=device_info,
        status="success",
        login_type="password",
        broker=broker_name,
    ))

    # Track session
    db.add(ActiveSession(
        user_id=user.id,
        session_token=session_token,
        device_info=device_info,
        ip_address=ip_address,
        broker=broker_name,
    ))
    await db.commit()

    # Set httpOnly cookie
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )

    return AuthResponse(status="success", message="Login successful")


@router.post("/logout", response_model=AuthResponse)
@limiter.limit("30 per minute")
async def logout(response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("access_token")
    if token:
        from backend.security import decode_access_token
        payload = decode_access_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                # Revoke broker auth
                result = await db.execute(
                    select(BrokerAuth).where(
                        BrokerAuth.user_id == int(user_id),
                        BrokerAuth.is_revoked == False,
                    )
                )
                broker_auth = result.scalar_one_or_none()
                if broker_auth:
                    broker_auth.is_revoked = True

                # Clear sessions
                result = await db.execute(
                    select(ActiveSession).where(ActiveSession.user_id == int(user_id))
                )
                for session in result.scalars().all():
                    await db.delete(session)
                await db.commit()

                # Clear cached broker context for this user
                from backend.dependencies import invalidate_user_cache
                await invalidate_user_cache(int(user_id))

    response.delete_cookie(
        "access_token",
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return AuthResponse(status="success", message="Logged out")


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    broker_name = getattr(user, "_broker", None)
    broker_authenticated = False

    # If JWT doesn't have broker claim (e.g. cookie domain mismatch after OAuth),
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

    if broker_name:
        result = await db.execute(
            select(BrokerAuth).where(
                BrokerAuth.user_id == user.id,
                BrokerAuth.broker_name == broker_name,
                BrokerAuth.is_revoked == False,
            )
        )
        broker_authenticated = result.scalar_one_or_none() is not None

    return UserInfo(
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        broker=broker_name,
        broker_authenticated=broker_authenticated,
    )
