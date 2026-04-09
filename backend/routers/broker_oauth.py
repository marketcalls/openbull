import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.dependencies import get_db, get_current_user
from backend.models.user import User
from backend.models.auth import BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.models.audit import LoginAttempt
from backend.security import encrypt_value, decrypt_value, create_access_token
from backend.utils.plugin_loader import get_plugin_info, get_broker_module

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["broker-oauth"])


@router.get("/auth/broker-redirect")
async def broker_redirect(
    broker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Construct broker OAuth URL and return it for frontend redirect."""
    plugin = get_plugin_info(broker)
    if not plugin:
        raise HTTPException(status_code=400, detail=f"Unknown broker: {broker}")

    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user.id,
            BrokerConfig.broker_name == broker,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=400, detail="Broker not configured. Please add credentials first.")

    api_key = decrypt_value(config.api_key)
    redirect_url = config.redirect_url

    auth_url_template = plugin.get("auth_url_template", "")
    if not auth_url_template:
        raise HTTPException(status_code=500, detail="Broker plugin missing auth_url_template")

    auth_url = auth_url_template.format(
        api_key=quote(api_key, safe=""),
        redirect_url=quote(redirect_url, safe=""),
    )

    return {"url": auth_url}


@router.get("/upstox/callback")
async def upstox_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Handle Upstox OAuth callback."""
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    return await _handle_oauth_callback("upstox", code, request, response, db)


@router.get("/zerodha/callback")
async def zerodha_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Handle Zerodha OAuth callback."""
    request_token = request.query_params.get("request_token")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request_token")

    return await _handle_oauth_callback("zerodha", request_token, request, response, db)


async def _handle_oauth_callback(
    broker_name: str,
    code_or_token: str,
    request: Request,
    response: Response,
    db: AsyncSession,
) -> RedirectResponse:
    """Common OAuth callback handler for all brokers."""
    # Identify user from JWT cookie
    token_cookie = request.cookies.get("access_token")
    if not token_cookie:
        return RedirectResponse(url=f"{settings.frontend_url}/login", status_code=302)

    from backend.security import decode_access_token
    payload = decode_access_token(token_cookie)
    if not payload:
        return RedirectResponse(url=f"{settings.frontend_url}/login", status_code=302)

    user_id = int(payload["sub"])
    username = payload.get("username", "unknown")

    # Get broker config from DB
    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user_id,
            BrokerConfig.broker_name == broker_name,
        )
    )
    broker_cfg = result.scalar_one_or_none()
    if not broker_cfg:
        logger.error("No broker config found for user %s, broker %s", user_id, broker_name)
        return RedirectResponse(url=f"{settings.frontend_url}/broker/config?error=not_configured", status_code=302)

    config = {
        "api_key": decrypt_value(broker_cfg.api_key),
        "api_secret": decrypt_value(broker_cfg.api_secret),
        "redirect_url": broker_cfg.redirect_url,
    }

    # Authenticate with broker
    try:
        broker_module = get_broker_module(broker_name, "auth_api")
        access_token, error = broker_module.authenticate_broker(code_or_token, config)
    except Exception as e:
        logger.exception("Failed to import broker module for %s", broker_name)
        return RedirectResponse(url=f"{settings.frontend_url}/broker/select?error=module_error", status_code=302)

    if not access_token:
        logger.error("Broker auth failed for %s: %s", broker_name, error)
        db.add(LoginAttempt(
            username=username,
            ip_address=request.client.host if request.client else "unknown",
            status="failed",
            login_type="oauth",
            broker=broker_name,
            failure_reason=error,
        ))
        await db.commit()
        return RedirectResponse(url=f"{settings.frontend_url}/broker/select?error=auth_failed", status_code=302)

    # Store encrypted token - upsert
    result = await db.execute(
        select(BrokerAuth).where(
            BrokerAuth.user_id == user_id,
            BrokerAuth.broker_name == broker_name,
        )
    )
    existing_auth = result.scalar_one_or_none()

    encrypted_token = encrypt_value(access_token)

    if existing_auth:
        existing_auth.access_token = encrypted_token
        existing_auth.is_revoked = False
    else:
        db.add(BrokerAuth(
            user_id=user_id,
            broker_name=broker_name,
            access_token=encrypted_token,
        ))

    # Mark this broker as active, deactivate others
    result = await db.execute(
        select(BrokerConfig).where(BrokerConfig.user_id == user_id)
    )
    for cfg in result.scalars().all():
        cfg.is_active = (cfg.broker_name == broker_name)

    # Log success
    db.add(LoginAttempt(
        username=username,
        ip_address=request.client.host if request.client else "unknown",
        status="success",
        login_type="oauth",
        broker=broker_name,
    ))
    await db.commit()

    # Issue new JWT with broker claim
    new_token = create_access_token(data={
        "sub": str(user_id),
        "username": username,
        "broker": broker_name,
    })

    redirect = RedirectResponse(url=f"{settings.frontend_url}/dashboard", status_code=302)
    redirect.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )

    logger.info("Broker %s authenticated for user %s", broker_name, username)
    return redirect
