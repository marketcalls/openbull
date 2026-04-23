"""
WebSocket support endpoints — session-auth'd, used by the React `/websocket/test`
page to bootstrap a client connection and inspect the MarketDataCache.

These are *not* the WebSocket endpoint itself (that lives on the proxy port
configured via ``settings.websocket_port``). They're HTTP helpers:

- GET /api/websocket/config       → ws:// URL (upgraded to wss:// under HTTPS)
- GET /api/websocket/apikey       → the caller's API key (for ws authenticate)
- GET /api/websocket/health       → MarketDataCache health snapshot
- GET /api/websocket/trade-safe   → quick RMS gate (for internal consumers)
- GET /api/websocket/metrics      → cache hit-rate, update counts
- GET /api/websocket/market-data  → cached LTP/quote/depth for one symbol
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.dependencies import get_current_user, get_db
from backend.models.auth import ApiKey
from backend.models.user import User
from backend.security import decrypt_value
from backend.services.market_data_cache import (
    get_health_status,
    get_market_data_cache,
    is_data_fresh,
    is_trade_management_safe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/websocket", tags=["websocket"])


@router.get("/config")
async def websocket_config(request: Request, user: User = Depends(get_current_user)):
    """Return the broker WebSocket proxy URL.

    Upgrades ``ws://`` to ``wss://`` when the caller's page is itself served
    over HTTPS, so mixed-content errors don't break the browser client.
    """
    settings = get_settings()
    url = settings.websocket_url
    is_secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    if is_secure and url.startswith("ws://"):
        url = "wss://" + url[len("ws://") :]
    return {
        "status": "success",
        "websocket_url": url,
        "original_url": settings.websocket_url,
        "is_secure": is_secure,
    }


@router.get("/apikey")
async def websocket_apikey(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the caller's API key for WebSocket authentication."""
    result = await db.execute(select(ApiKey).where(ApiKey.user_id == user.id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(
            status_code=404,
            detail="No API key found. Generate one at /apikey before using WebSockets.",
        )
    try:
        key = decrypt_value(record.api_key_encrypted)
    except Exception:
        logger.error("Failed to decrypt API key for user %d", user.id)
        raise HTTPException(status_code=500, detail="Failed to decrypt API key")
    return {"status": "success", "api_key": key}


@router.get("/health")
async def websocket_health(user: User = Depends(get_current_user)):
    """MarketDataCache health: connection, data freshness, subscriber counts."""
    health = get_health_status()
    safe, reason = is_trade_management_safe()
    payload = asdict(health)
    payload.update(
        {
            "status": health.status,
            "healthy": health.status == "healthy",
            "trade_management_safe": safe,
            "trade_management_reason": reason,
        }
    )
    return payload


@router.get("/trade-safe")
async def websocket_trade_safe(user: User = Depends(get_current_user)):
    """Lightweight RMS gate: is it safe to fire trade-management orders now?"""
    safe, reason = is_trade_management_safe()
    fresh = is_data_fresh(max_age_seconds=30)
    return {"safe": safe, "reason": reason, "data_fresh": fresh}


@router.get("/metrics")
async def websocket_metrics(user: User = Depends(get_current_user)):
    """Cache hit-rate and tick counts."""
    return {"status": "success", "metrics": get_market_data_cache().get_metrics()}


@router.get("/market-data")
async def websocket_market_data(
    symbol: str,
    exchange: str,
    user: User = Depends(get_current_user),
):
    """Read-only peek at the cached LTP/quote/depth for one symbol."""
    cache = get_market_data_cache()
    data = cache.get_all(symbol, exchange)
    if not data:
        return {"status": "error", "message": "No cached data for symbol", "data": None}
    return {"status": "success", "data": data}
