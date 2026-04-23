"""
Trading mode — global "live" vs "sandbox" flag.

Stored as a single row in ``app_settings`` (key = ``trading_mode``). A small
in-memory cache avoids a DB hit on every order-path call; the cache is
invalidated whenever the mode is written, so all workers inside this process
see the change on the next request.

Read path is sync-friendly (async function but pure DB lookup + cache). The
dispatch helper :func:`dispatch_by_mode` is the one all order services will
call to branch between live broker API and sandbox simulated engine.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.settings import (
    TRADING_MODE_KEY,
    TRADING_MODE_LIVE,
    TRADING_MODE_SANDBOX,
    VALID_TRADING_MODES,
    AppSettings,
)

logger = logging.getLogger(__name__)

# Cache TTL is short — on mode change the write path invalidates immediately;
# the TTL only matters if another process (future multi-worker deploy) changed
# the row without our knowledge.
_CACHE_TTL_SECONDS = 10.0

_cache_lock = threading.Lock()
_cached_mode: str | None = None
_cache_expiry: float = 0.0


def _set_cache(mode: str) -> None:
    global _cached_mode, _cache_expiry
    with _cache_lock:
        _cached_mode = mode
        _cache_expiry = time.monotonic() + _CACHE_TTL_SECONDS


def invalidate_cache() -> None:
    global _cached_mode, _cache_expiry
    with _cache_lock:
        _cached_mode = None
        _cache_expiry = 0.0


def get_trading_mode_sync() -> str:
    """Sync counterpart of :func:`get_trading_mode` for use from sync service
    code (existing place_order_service etc.). Reads cache only — if the cache
    is cold it falls back to ``"live"`` rather than opening a DB connection
    from sync context. Acceptable because the cache is populated on the first
    async read (dashboard load, ``GET /web/trading-mode`` from the UI) and
    stays warm for 10 s.

    If you need an authoritative read from sync context, use the sync
    SQLAlchemy engine in :mod:`backend.sandbox._db` — but for dispatch
    decisions the cache is fine.
    """
    now = time.monotonic()
    with _cache_lock:
        if _cached_mode is not None and now < _cache_expiry:
            return _cached_mode
    # Fall back to sync DB read — used on the first call before any async
    # consumer primed the cache. Uses the same sync engine as the sandbox layer.
    try:
        from sqlalchemy import select as _select

        from backend.sandbox._db import session_scope

        with session_scope() as db:
            row = db.execute(
                _select(AppSettings).where(AppSettings.key == TRADING_MODE_KEY)
            ).scalar_one_or_none()
            value = row.value if row else TRADING_MODE_LIVE
            if value not in VALID_TRADING_MODES:
                value = TRADING_MODE_LIVE
            _set_cache(value)
            return value
    except Exception:
        return TRADING_MODE_LIVE


async def get_trading_mode(db: AsyncSession | None = None) -> str:
    """Return the current mode (``"live"`` or ``"sandbox"``).

    Falls back to ``"live"`` if the row is missing or the DB is unreachable —
    "live" is the safe default because the caller will reach the broker API
    which will reject unauthenticated requests anyway.
    """
    now = time.monotonic()
    with _cache_lock:
        if _cached_mode is not None and now < _cache_expiry:
            return _cached_mode

    async def _load(session: AsyncSession) -> str:
        row = (
            await session.execute(
                select(AppSettings).where(AppSettings.key == TRADING_MODE_KEY)
            )
        ).scalar_one_or_none()
        value = row.value if row else TRADING_MODE_LIVE
        if value not in VALID_TRADING_MODES:
            logger.warning("Invalid trading_mode %r in DB — treating as live", value)
            value = TRADING_MODE_LIVE
        return value

    try:
        if db is not None:
            mode = await _load(db)
        else:
            async with async_session() as s:
                mode = await _load(s)
    except Exception:
        logger.exception("Failed to read trading_mode; defaulting to live")
        return TRADING_MODE_LIVE

    _set_cache(mode)
    return mode


async def set_trading_mode(db: AsyncSession, mode: str) -> str:
    """Write-through update. Raises ``ValueError`` on invalid mode."""
    if mode not in VALID_TRADING_MODES:
        raise ValueError(f"Invalid trading mode: {mode!r}")
    row = (
        await db.execute(
            select(AppSettings).where(AppSettings.key == TRADING_MODE_KEY)
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(AppSettings(key=TRADING_MODE_KEY, value=mode))
    else:
        row.value = mode
    await db.commit()
    _set_cache(mode)
    logger.info("Trading mode set to %s", mode)
    return mode


def is_sandbox(mode: str) -> bool:
    return mode == TRADING_MODE_SANDBOX


def is_live(mode: str) -> bool:
    return mode == TRADING_MODE_LIVE


# ---- Dispatch helper -------------------------------------------------------

T = TypeVar("T")


async def dispatch_by_mode(
    live_fn: Callable[..., Awaitable[T]],
    sandbox_fn: Callable[..., Awaitable[T]] | None,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Call ``live_fn`` or ``sandbox_fn`` based on the current trading mode.

    While Phase 2 (the sandbox engine) is not yet implemented, passing
    ``sandbox_fn=None`` is accepted — in sandbox mode the helper falls back
    to ``live_fn`` so the app stays functional. Once the sandbox services
    exist, every order service will pass both.
    """
    mode = await get_trading_mode()
    if mode == TRADING_MODE_SANDBOX and sandbox_fn is not None:
        return await sandbox_fn(*args, **kwargs)
    return await live_fn(*args, **kwargs)
