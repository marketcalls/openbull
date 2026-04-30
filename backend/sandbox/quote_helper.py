"""
LTP lookup with broker-quote fallback.

Used by the sandbox order service and execution engine to obtain a usable
last-traded price for *any* symbol — including ones the market-data cache
hasn't seen yet (a fresh app start, an after-hours order, or a thinly traded
contract that hasn't ticked recently).

Resolution order:

1. ``MarketDataCache`` LTP — populated by the WebSocket tick stream. Fast,
   no network call. The default path during market hours.
2. Broker quote API — uses the calling user's stored broker auth to fetch
   a single-symbol quote and reads its ``ltp`` field. Necessary outside
   market hours and for symbols that haven't streamed yet.

Result is cached in the same ``MarketDataCache`` for ``BROKER_QUOTE_TTL`` so
repeated lookups for the same symbol within a few seconds don't hammer the
broker. After-hours, the broker LTP is the previous-day close and doesn't
change, so the TTL trade-off is harmless.

Sync only — sits alongside the rest of the sandbox layer which uses sync
SQLAlchemy.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from typing import Optional

from sqlalchemy import select

from backend.models.auth import BrokerAuth
from backend.models.broker_config import BrokerConfig
from backend.sandbox._db import session_scope
from backend.security import decrypt_value
from backend.services.market_data_cache import get_market_data_cache

logger = logging.getLogger(__name__)


# How long a broker-fetched LTP stays usable before we fetch again.
BROKER_QUOTE_TTL = 5.0  # seconds

# How long to remember that a symbol has no broker quote (expired option,
# unlisted contract, etc.) before retrying. Without negative caching the
# MTM updater would call the broker for the same dead symbol every cycle.
BROKER_QUOTE_FAIL_TTL = 300.0  # seconds

# Per-symbol broker-quote cache: (symbol, exchange) -> (ts, ltp). A stored
# ``ltp`` of ``None`` means "broker has no quote for this symbol" — treat
# as a soft skip until the entry expires.
_broker_quote_cache: dict[tuple[str, str], tuple[float, Optional[float]]] = {}
_broker_quote_lock = threading.Lock()

# Sentinel returned by ``_cache_get`` to distinguish "negative-cached
# miss" (skip the broker call) from "no entry / expired" (try the broker).
_NEG_CACHE_SENTINEL = object()


def _cache_get(symbol: str, exchange: str):
    """Return the cached LTP, ``_NEG_CACHE_SENTINEL`` for a known-bad symbol,
    or ``None`` if there is no fresh entry.
    """
    with _broker_quote_lock:
        entry = _broker_quote_cache.get((symbol, exchange))
        if entry is None:
            return None
        ts, ltp = entry
        if ltp is None:
            if (time.monotonic() - ts) > BROKER_QUOTE_FAIL_TTL:
                return None
            return _NEG_CACHE_SENTINEL
        if (time.monotonic() - ts) > BROKER_QUOTE_TTL:
            return None
        return ltp


def _cache_put(symbol: str, exchange: str, ltp: float) -> None:
    with _broker_quote_lock:
        _broker_quote_cache[(symbol, exchange)] = (time.monotonic(), float(ltp))


def _cache_put_miss(symbol: str, exchange: str) -> None:
    """Remember that the broker has no quote for this symbol so we don't
    keep retrying every MTM tick."""
    with _broker_quote_lock:
        _broker_quote_cache[(symbol, exchange)] = (time.monotonic(), None)


def _resolve_broker_for_user(user_id: int) -> tuple[str, str, dict] | None:
    """Find a usable (broker, auth_token, config) for this user.

    Picks the first non-revoked ``broker_auth`` row. If multiple brokers are
    linked we prefer the one whose ``broker_configs.is_active`` is true; this
    matches the precedence ``get_broker_context`` uses for live order routing.
    Returns ``None`` if the user has no linked broker — caller falls back to
    cached LTP only.
    """
    with session_scope() as db:
        # Active broker first
        active_cfg = db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user_id,
                BrokerConfig.is_active.is_(True),
            )
        ).scalar_one_or_none()

        broker_name: str | None = active_cfg.broker_name if active_cfg else None

        if broker_name is None:
            any_auth = db.execute(
                select(BrokerAuth).where(
                    BrokerAuth.user_id == user_id,
                    BrokerAuth.is_revoked.is_(False),
                ).limit(1)
            ).scalar_one_or_none()
            if any_auth is None:
                return None
            broker_name = any_auth.broker_name

        auth_row = db.execute(
            select(BrokerAuth).where(
                BrokerAuth.user_id == user_id,
                BrokerAuth.broker_name == broker_name,
                BrokerAuth.is_revoked.is_(False),
            )
        ).scalar_one_or_none()
        if auth_row is None:
            return None

        cfg_row = db.execute(
            select(BrokerConfig).where(
                BrokerConfig.user_id == user_id,
                BrokerConfig.broker_name == broker_name,
            )
        ).scalar_one_or_none()

        try:
            auth_token = decrypt_value(auth_row.access_token)
        except Exception:
            logger.exception("quote_helper: failed to decrypt auth_token for user %d", user_id)
            return None

        config: dict = {}
        if cfg_row is not None:
            try:
                config = {
                    "api_key": decrypt_value(cfg_row.api_key),
                    "api_secret": decrypt_value(cfg_row.api_secret),
                    "redirect_url": cfg_row.redirect_url,
                }
            except Exception:
                logger.exception("quote_helper: failed to decrypt broker_config for user %d", user_id)

    return broker_name, auth_token, config


def _broker_quote_ltp(user_id: int, symbol: str, exchange: str) -> Optional[float]:
    """Fetch a single-symbol quote via the user's broker and extract the LTP.

    Returns ``None`` on any failure — never raises. The sandbox can always
    fall back to the cached LTP (or refuse to fill until a tick arrives) if
    this path is unavailable.
    """
    cached = _cache_get(symbol, exchange)
    if cached is _NEG_CACHE_SENTINEL:
        # Symbol is known-bad (e.g. expired option) — skip without hitting
        # the broker. Re-checked after BROKER_QUOTE_FAIL_TTL.
        return None
    if isinstance(cached, (int, float)) and cached > 0:
        return cached

    resolved = _resolve_broker_for_user(user_id)
    if resolved is None:
        return None
    broker_name, auth_token, config = resolved

    try:
        data_module = importlib.import_module(f"backend.broker.{broker_name}.api.data")
    except ImportError:
        logger.debug("quote_helper: broker module %s not available", broker_name)
        return None

    try:
        result = data_module.get_quotes(symbol, exchange, auth_token, config)
    except Exception as e:
        # Don't keep retrying — most likely an expired/delisted contract that
        # will never come back. Negative-cache and move on.
        logger.debug("quote_helper: get_quotes failed for %s/%s via %s: %s",
                     symbol, exchange, broker_name, e)
        _cache_put_miss(symbol, exchange)
        return None

    ltp_val: Optional[float] = None
    if isinstance(result, dict):
        for key in ("ltp", "last_price", "lastPrice"):
            v = result.get(key)
            if isinstance(v, (int, float)) and v > 0:
                ltp_val = float(v)
                break
            if isinstance(v, str):
                try:
                    fv = float(v)
                    if fv > 0:
                        ltp_val = fv
                        break
                except ValueError:
                    continue

    if ltp_val is None or ltp_val <= 0:
        _cache_put_miss(symbol, exchange)
        return None

    _cache_put(symbol, exchange, ltp_val)
    return ltp_val


def get_ltp(user_id: int, symbol: str, exchange: str) -> Optional[float]:
    """Cache-first LTP lookup with broker fallback.

    Always tries the in-memory ``MarketDataCache`` first because that's
    populated by the live WebSocket tick stream and updates on every tick.
    Only falls back to a broker quote when the cache has nothing — typical
    when the sandbox is used outside market hours, on a freshly started
    server, or for an unsubscribed symbol.
    """
    try:
        cached = get_market_data_cache().get_ltp_value(symbol, exchange)
        if cached is not None and cached > 0:
            return float(cached)
    except Exception:
        logger.debug("quote_helper: market_data_cache LTP lookup failed", exc_info=True)

    return _broker_quote_ltp(user_id, symbol, exchange)
