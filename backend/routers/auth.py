import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.dependencies import get_db, get_current_user
from backend.models.user import User
from backend.models.auth import BrokerAuth, ApiKey
from backend.models.broker_config import BrokerConfig
from backend.models.audit import LoginAttempt, ActiveSession
from backend.schemas.auth import SetupRequest, LoginRequest, AuthResponse, UserInfo
from backend.security import (
    hash_password, verify_password, check_needs_rehash, create_access_token,
    generate_api_key, hash_api_key, encrypt_value,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/check-setup")
async def check_setup(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).select_from(User))
    user_count = result.scalar()
    return {"needs_setup": user_count == 0}


@router.post("/setup", response_model=AuthResponse)
async def setup(data: SetupRequest, db: AsyncSession = Depends(get_db)):
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


@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip_address = request.client.host if request.client else "unknown"
    device_info = request.headers.get("User-Agent", "")[:500]

    result = await db.execute(select(User).where(User.username == data.username.strip().lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
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

    # Check if there's an active broker auth (session resume)
    result = await db.execute(
        select(BrokerAuth).where(
            BrokerAuth.user_id == user.id,
            BrokerAuth.is_revoked == False,
        )
    )
    broker_auth = result.scalar_one_or_none()
    broker_name = broker_auth.broker_name if broker_auth else None

    # Create JWT
    token = create_access_token(data={
        "sub": str(user.id),
        "username": user.username,
        "broker": broker_name,
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
    session_token = secrets.token_hex(32)
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
        secure=False,  # Set True in production with HTTPS
        path="/",
    )

    return AuthResponse(status="success", message="Login successful")


@router.post("/logout", response_model=AuthResponse)
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

    response.delete_cookie("access_token", path="/")
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
