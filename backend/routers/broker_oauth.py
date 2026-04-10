import logging
from threading import Thread
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

    # Pass JWT as OAuth state so callback can identify user
    # even when cookie is on a different domain (e.g. Vite proxy)
    from backend.security import create_access_token
    state_token = create_access_token(data={
        "sub": str(user.id),
        "username": user.username,
    })

    auth_url = auth_url_template.format(
        api_key=quote(api_key, safe=""),
        redirect_url=quote(redirect_url, safe=""),
    )
    auth_url += f"&state={quote(state_token, safe='')}"

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

    state = request.query_params.get("state")
    return await _handle_oauth_callback("upstox", code, request, response, db, state=state)


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

    state = request.query_params.get("state")
    return await _handle_oauth_callback("zerodha", request_token, request, response, db, state=state)


def _start_master_contract_download(broker_name: str, auth_token: str):
    """Start master contract download in a background thread after broker login."""
    from backend.services.symbol_service import download_master_contracts
    from backend.services.master_contract_status import set_downloading, set_success, set_error

    def _background():
        try:
            set_downloading(broker_name)
            result = download_master_contracts(broker_name, auth_token=auth_token)
            logger.info("Master contract download result for %s: %s", broker_name, result)
            if result.get("status") == "success":
                set_success(broker_name, result.get("count", 0))
            else:
                set_error(broker_name, result.get("message", "Unknown error"))
        except Exception as e:
            logger.error("Background master contract download failed for %s: %s", broker_name, e)
            set_error(broker_name, str(e))

    thread = Thread(target=_background, daemon=True)
    thread.start()


async def _handle_oauth_callback(
    broker_name: str,
    code_or_token: str,
    request: Request,
    response: Response,
    db: AsyncSession,
    state: str | None = None,
) -> RedirectResponse:
    """Common OAuth callback handler for all brokers."""
    from backend.security import decode_access_token

    logger.info("=== OAuth callback START for %s ===", broker_name)
    logger.info("code_or_token present: %s", bool(code_or_token))
    logger.info("state present: %s", bool(state))
    logger.info("all query params: %s", dict(request.query_params))
    logger.info("cookies: %s", list(request.cookies.keys()))

    # Identify user from JWT cookie, or from OAuth state parameter as fallback
    token_cookie = request.cookies.get("access_token")
    payload = decode_access_token(token_cookie) if token_cookie else None
    logger.info("cookie payload: %s", "found" if payload else "none")

    if not payload and state:
        payload = decode_access_token(state)
        logger.info("state payload: %s", "found" if payload else "none")

    if not payload:
        logger.error("NO user identity found - redirecting to login")
        return RedirectResponse(url=f"{settings.frontend_url}/login", status_code=302)

    user_id = int(payload["sub"])
    username = payload.get("username", "unknown")
    logger.info("identified user: id=%s username=%s", user_id, username)

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

    logger.info("broker config found, redirect_url=%s", broker_cfg.redirect_url)

    config = {
        "api_key": decrypt_value(broker_cfg.api_key),
        "api_secret": decrypt_value(broker_cfg.api_secret),
        "redirect_url": broker_cfg.redirect_url,
    }

    # Authenticate with broker
    try:
        broker_module = get_broker_module(broker_name, "auth_api")
        logger.info("calling authenticate_broker for %s", broker_name)
        access_token, error = broker_module.authenticate_broker(code_or_token, config)
        logger.info("authenticate_broker result: token=%s, error=%s", bool(access_token), error)
    except Exception as e:
        logger.exception("Failed to import/run broker module for %s: %s", broker_name, e)
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

    # Trigger master contract download in background
    _start_master_contract_download(broker_name, access_token)

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

    logger.info("=== OAuth callback SUCCESS for %s, redirecting to dashboard ===", broker_name)
    return redirect
