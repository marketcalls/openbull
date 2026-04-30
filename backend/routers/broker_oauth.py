import logging
from threading import Thread
from urllib.parse import quote, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.dependencies import get_db, get_current_user
from backend.models.user import User
from backend.models.auth import BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.models.audit import LoginAttempt, ActiveSession
from backend.schemas.broker import AngelLoginPayload
from backend.security import encrypt_value, decrypt_value, create_access_token
from backend.utils.plugin_loader import get_plugin_info, get_broker_module

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["broker-oauth"])

# In-memory store for pending OAuth flows (broker -> {user_id, username})
# Used when broker doesn't echo back state param (e.g. Zerodha, Dhan).
_pending_oauth: dict[str, dict] = {}


def _frontend_url_for_request(request: Request) -> str:
    """Build the frontend base URL using the request's actual hostname.

    Browsers scope cookies by hostname, and treat 127.0.0.1 and localhost as
    distinct hosts. The OAuth callback sets the access_token cookie on
    whatever host the browser used to reach the backend (e.g. 127.0.0.1 when
    the user's broker redirect_url is http://127.0.0.1:8000/<broker>/callback).
    If we then redirect to a hardcoded settings.frontend_url with a different
    hostname (e.g. http://localhost:5173), the browser drops the new cookie
    and the user lands at /dashboard unauthenticated, getting bounced to
    /login. This was the "first login fails, second succeeds" bug.

    Strategy: keep scheme + port from FRONTEND_URL, but substitute the
    hostname the request actually arrived on so the cookie survives the
    redirect. Falls back to settings.frontend_url when the request has no
    parseable hostname.
    """
    parsed = urlparse(settings.frontend_url)
    request_hostname = request.url.hostname
    if not request_hostname:
        return settings.frontend_url
    netloc = f"{request_hostname}:{parsed.port}" if parsed.port else request_hostname
    return urlunparse((parsed.scheme, netloc, "", "", "", "")).rstrip("/")


@router.get("/auth/broker-redirect")
async def broker_redirect(
    broker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Construct broker login URL and return it for frontend redirect.

    For brokers that don't OAuth (Angel), the response carries
    ``kind: "internal"`` and a path the frontend should navigate to with
    react-router instead of a full page redirect.
    """
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

    # Angel: no OAuth. Frontend renders a credentials/TOTP form and POSTs
    # to /angel/login.
    if broker == "angel":
        return {"url": "/broker/angel/totp", "kind": "internal"}

    # Dhan: 2-step. Generate consent server-side, then redirect the browser
    # to consentApp-login. The plugin's auth_url_template is informational
    # only; we build the URL here from the live consent_app_id.
    if broker == "dhan":
        extra = config.extra_config or {}
        client_id = extra.get("client_id")
        if not client_id:
            raise HTTPException(
                status_code=400,
                detail="Dhan Client ID is missing. Please configure it on the Broker Configuration page.",
            )
        api_key = decrypt_value(config.api_key)
        api_secret = decrypt_value(config.api_secret) if config.api_secret else ""

        from backend.broker.dhan.api.auth_api import generate_consent
        consent_app_id, error = generate_consent(client_id, api_key, api_secret)
        if not consent_app_id:
            raise HTTPException(status_code=502, detail=f"Dhan consent generation failed: {error}")

        login_url = f"https://auth.dhan.co/login/consentApp-login?consentAppId={quote(consent_app_id, safe='')}"
        # Dhan's callback receives tokenId without state. Stash user identity
        # so the callback can find them when no state echo arrives.
        _pending_oauth["dhan"] = {"user_id": user.id, "username": user.username}
        return {"url": login_url, "kind": "external"}

    # Default OAuth flow (upstox, zerodha, fyers): substitute api_key + redirect_url
    # into the plugin's auth_url_template and add JWT state for the callback.
    api_key = decrypt_value(config.api_key)
    redirect_url = config.redirect_url

    auth_url_template = plugin.get("auth_url_template", "")
    if not auth_url_template:
        raise HTTPException(status_code=500, detail="Broker plugin missing auth_url_template")

    state_token = create_access_token(data={
        "sub": str(user.id),
        "username": user.username,
    })

    auth_url = auth_url_template.format(
        api_key=quote(api_key, safe=""),
        redirect_url=quote(redirect_url, safe=""),
    )
    auth_url += f"&state={quote(state_token, safe='')}"

    # Store pending OAuth for brokers that don't echo state (e.g. Zerodha)
    _pending_oauth[broker] = {"user_id": user.id, "username": user.username}

    return {"url": auth_url, "kind": "external"}


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


@router.get("/fyers/callback")
async def fyers_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Handle Fyers OAuth callback. Fyers v3 returns auth_code, not code."""
    auth_code = request.query_params.get("auth_code") or request.query_params.get("code")
    if not auth_code:
        raise HTTPException(status_code=400, detail="Missing auth_code")

    state = request.query_params.get("state")
    return await _handle_oauth_callback("fyers", auth_code, request, response, db, state=state)


@router.get("/dhan/callback")
async def dhan_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Handle Dhan OAuth callback. Dhan returns tokenId."""
    token_id = (
        request.query_params.get("tokenId")
        or request.query_params.get("token_id")
        or request.query_params.get("token")
    )
    if not token_id:
        raise HTTPException(status_code=400, detail="Missing tokenId")

    state = request.query_params.get("state")
    return await _handle_oauth_callback("dhan", token_id, request, response, db, state=state)


@router.post("/angel/login")
async def angel_login(
    payload: AngelLoginPayload,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with Angel One using clientcode + MPIN + TOTP.

    Angel doesn't OAuth; this endpoint is the credentials-form equivalent
    of an OAuth callback.
    """
    creds = f"{payload.clientcode.strip()}:{payload.broker_pin.strip()}:{payload.totp_code.strip()}"
    new_token, error = await _finalize_broker_auth(
        "angel", creds, user.id, user.username, request, db
    )
    if not new_token:
        raise HTTPException(status_code=401, detail=error or "Angel authentication failed")

    response.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return {"status": "success", "broker": "angel"}


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
                # Reload symbol cache after successful download
                import asyncio
                from backend.broker.upstox.mapping.order_data import _load_symbol_cache
                asyncio.run(_load_symbol_cache())
            else:
                set_error(broker_name, result.get("message", "Unknown error"))
        except Exception as e:
            logger.error("Background master contract download failed for %s: %s", broker_name, e)
            set_error(broker_name, str(e))

    thread = Thread(target=_background, daemon=True)
    thread.start()


async def _finalize_broker_auth(
    broker_name: str,
    code_or_token: str,
    user_id: int,
    username: str,
    request: Request,
    db: AsyncSession,
) -> tuple[str | None, str | None]:
    """Run broker authenticate, persist token, mark active, kick off master
    contract download, and mint a new JWT carrying the broker claim.

    Returns ``(new_jwt, error)``. Caller is responsible for setting the
    cookie / building the redirect or JSON response.
    """
    result = await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user_id,
            BrokerConfig.broker_name == broker_name,
        )
    )
    broker_cfg = result.scalar_one_or_none()
    if not broker_cfg:
        return None, "broker_not_configured"

    extra = broker_cfg.extra_config or {}
    config = {
        "api_key": decrypt_value(broker_cfg.api_key),
        "api_secret": decrypt_value(broker_cfg.api_secret) if broker_cfg.api_secret else "",
        "redirect_url": broker_cfg.redirect_url,
        "client_id": extra.get("client_id", ""),
    }

    try:
        broker_module = get_broker_module(broker_name, "auth_api")
        logger.info("calling authenticate_broker for %s", broker_name)
        access_token, error = broker_module.authenticate_broker(code_or_token, config)
        logger.info(
            "authenticate_broker result for %s: token=%s, error=%s",
            broker_name, bool(access_token), error,
        )
    except Exception as e:
        logger.exception("Failed to import/run broker module for %s: %s", broker_name, e)
        return None, f"module_error: {e}"

    if not access_token:
        db.add(LoginAttempt(
            username=username,
            ip_address=request.client.host if request.client else "unknown",
            status="failed",
            login_type="oauth",
            broker=broker_name,
            failure_reason=error,
        ))
        await db.commit()
        return None, error or "auth_failed"

    # Upsert BrokerAuth
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

    # Mark this broker active, deactivate others
    result = await db.execute(
        select(BrokerConfig).where(BrokerConfig.user_id == user_id)
    )
    for cfg in result.scalars().all():
        cfg.is_active = (cfg.broker_name == broker_name)

    db.add(LoginAttempt(
        username=username,
        ip_address=request.client.host if request.client else "unknown",
        status="success",
        login_type="oauth",
        broker=broker_name,
    ))
    await db.commit()

    _start_master_contract_download(broker_name, access_token)

    # Re-issue the JWT with the broker claim, preserving existing
    # ActiveSession.session_token as `jti` so the user's server-side
    # session stays valid.
    session_result = await db.execute(
        select(ActiveSession)
        .where(ActiveSession.user_id == user_id)
        .order_by(ActiveSession.login_time.desc())
    )
    active_session = session_result.scalars().first()
    if active_session is None:
        import secrets as _secrets
        session_token = _secrets.token_hex(32)
        db.add(ActiveSession(
            user_id=user_id,
            session_token=session_token,
            device_info=request.headers.get("User-Agent", "")[:500],
            ip_address=request.client.host if request.client else "unknown",
            broker=broker_name,
        ))
        await db.commit()
    else:
        session_token = active_session.session_token
        active_session.broker = broker_name
        await db.commit()

    new_token = create_access_token(data={
        "sub": str(user_id),
        "username": username,
        "broker": broker_name,
        "jti": session_token,
    })
    return new_token, None


async def _handle_oauth_callback(
    broker_name: str,
    code_or_token: str,
    request: Request,
    response: Response,
    db: AsyncSession,
    state: str | None = None,
) -> RedirectResponse:
    """Common OAuth callback handler for redirect-style brokers."""
    from backend.security import decode_access_token

    frontend_base = _frontend_url_for_request(request)

    logger.info("=== OAuth callback START for %s ===", broker_name)
    logger.info("code_or_token present: %s", bool(code_or_token))
    logger.info("state present: %s", bool(state))
    logger.info("all query params: %s", dict(request.query_params))
    logger.info("cookies: %s", list(request.cookies.keys()))
    logger.info("redirect base resolved to %s (request host=%s)", frontend_base, request.url.hostname)

    # Identify user from JWT cookie, OAuth state, or pending-oauth fallback
    token_cookie = request.cookies.get("access_token")
    payload = decode_access_token(token_cookie) if token_cookie else None
    logger.info("cookie payload: %s", "found" if payload else "none")

    if not payload and state:
        payload = decode_access_token(state)
        logger.info("state payload: %s", "found" if payload else "none")

    if not payload and broker_name in _pending_oauth:
        pending = _pending_oauth.pop(broker_name)
        payload = {"sub": str(pending["user_id"]), "username": pending["username"]}
        logger.info("pending oauth payload: found for user %s", pending["username"])

    if not payload:
        logger.error("NO user identity found - redirecting to login")
        return RedirectResponse(url=f"{frontend_base}/login", status_code=302)

    user_id = int(payload["sub"])
    username = payload.get("username", "unknown")
    logger.info("identified user: id=%s username=%s", user_id, username)

    new_token, error = await _finalize_broker_auth(
        broker_name, code_or_token, user_id, username, request, db
    )

    if not new_token:
        if error == "broker_not_configured":
            return RedirectResponse(
                url=f"{frontend_base}/broker/config?error=not_configured", status_code=302
            )
        if error and error.startswith("module_error"):
            return RedirectResponse(
                url=f"{frontend_base}/broker/select?error=module_error", status_code=302
            )
        logger.error("Broker auth failed for %s: %s", broker_name, error)
        return RedirectResponse(
            url=f"{frontend_base}/broker/select?error=auth_failed", status_code=302
        )

    redirect = RedirectResponse(url=f"{frontend_base}/dashboard", status_code=302)
    redirect.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )

    logger.info("=== OAuth callback SUCCESS for %s, redirecting to dashboard ===", broker_name)
    return redirect
