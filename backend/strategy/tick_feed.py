"""Tick feed for the strategy engine.

Wraps the existing :mod:`backend.services.market_data_cache` priority pub/sub
so the engine consumes LTPs the same way every other internal consumer does
(plan Section 5.5 — "event-driven, never polling").

Single process-wide subscriber. A symbol-to-runs index lets a CRITICAL-
priority callback fan ticks out to every interested run in O(1) per tick.
The callback runs on the cache's thread; tick processing itself is async,
so we hand off via ``asyncio.Queue.put_nowait`` from a thread-safe loop
reference captured on ``init()``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from backend.services.market_data_cache import (
    SubscriberPriority,
    get_market_data_cache,
)

logger = logging.getLogger(__name__)

# (exchange, symbol) -> set of run_ids interested in this tick
_index: dict[tuple[str, str], set[int]] = {}
_index_lock = threading.Lock()

# Process-wide subscriber id on the cache; None if not yet initialized
_cache_sub_id: Optional[int] = None

# Asyncio queue draining hand-off from sync callback to async processor
_tick_queue: Optional[asyncio.Queue] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_initialized = False


# ---------------------------------------------------------------------------
# Initialization (called from FastAPI lifespan)
# ---------------------------------------------------------------------------


def init(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    """Bind the async loop and queue; subscribe to the cache.

    The processor task should be running before this is called so no early
    ticks get dropped.
    """
    global _cache_sub_id, _tick_queue, _loop, _initialized
    if _initialized:
        return
    _loop = loop
    _tick_queue = queue
    cache = get_market_data_cache()
    _cache_sub_id = cache.subscribe_critical(
        _on_tick, filter_symbols=None, name="strategy_engine",
    )
    _initialized = True
    logger.info("Strategy tick feed: subscribed to MarketDataCache (id=%d)", _cache_sub_id)


def shutdown() -> None:
    """Unsubscribe from the cache. Called from FastAPI lifespan teardown."""
    global _cache_sub_id, _initialized
    if not _initialized:
        return
    cache = get_market_data_cache()
    if _cache_sub_id is not None:
        cache.unsubscribe(_cache_sub_id)
        _cache_sub_id = None
    _initialized = False
    logger.info("Strategy tick feed: unsubscribed")


# ---------------------------------------------------------------------------
# Subscription registry (engine.start_run / stop_run wire these)
# ---------------------------------------------------------------------------


def add_run_subscriptions(run_id: int, symbols: list[tuple[str, str]]) -> None:
    """Tell the tick feed which (exchange, symbol) pairs a run cares about.

    Called from engine.start_run after entry orders are placed.
    """
    if not symbols:
        return
    with _index_lock:
        for sym in symbols:
            _index.setdefault(sym, set()).add(run_id)
    logger.debug("Tick feed: run %d subscribed to %d symbols", run_id, len(symbols))


def remove_run_subscriptions(run_id: int) -> None:
    """Clean up after a run stops."""
    with _index_lock:
        empty = []
        for sym, runs in _index.items():
            runs.discard(run_id)
            if not runs:
                empty.append(sym)
        for sym in empty:
            _index.pop(sym, None)
    logger.debug("Tick feed: run %d unsubscribed", run_id)


# ---------------------------------------------------------------------------
# Cache callback (runs on the cache's thread)
# ---------------------------------------------------------------------------


def _on_tick(tick: dict[str, Any]) -> None:
    """Hand off a tick to the async processor. Must not block.

    The cache fires CRITICAL subscribers synchronously in priority order;
    if we block here we slow every other strategy's risk eval. Pushing onto
    a bounded asyncio queue is constant-time and drops on overflow (which
    indicates the processor is stuck — the next tick on this symbol heals).
    """
    if not _initialized or _loop is None or _tick_queue is None:
        return
    symbol = tick.get("symbol")
    exchange = tick.get("exchange")
    if not symbol or not exchange:
        return
    key = (exchange, symbol)
    with _index_lock:
        run_ids = list(_index.get(key, ()))
    if not run_ids:
        return
    data = tick.get("data") or {}
    ltp_field = data.get("ltp")
    if ltp_field is None:
        return
    # LTP from the cache can be a nested dict (mode=LTP) or a top-level
    # number depending on the broker adapter. Normalize.
    if isinstance(ltp_field, dict):
        ltp_value = ltp_field.get("value")
    else:
        ltp_value = ltp_field
    try:
        ltp = float(ltp_value)
    except (TypeError, ValueError):
        return

    payload = {"exchange": exchange, "symbol": symbol, "ltp": ltp,
               "run_ids": run_ids}
    try:
        _loop.call_soon_threadsafe(_tick_queue.put_nowait, payload)
    except RuntimeError:
        # Loop is shutting down — drop silently.
        pass
    except asyncio.QueueFull:
        logger.warning("Strategy tick queue full — dropped tick for %s:%s", exchange, symbol)


# ---------------------------------------------------------------------------
# Manual tick injection (testing / Phase 6 verification without market hours)
# ---------------------------------------------------------------------------


async def inject_tick(*, exchange: str, symbol: str, ltp: float) -> None:
    """Test-only entry point — bypasses the broker WS, used by unit tests."""
    if not _initialized or _tick_queue is None:
        return
    with _index_lock:
        run_ids = list(_index.get((exchange, symbol), ()))
    if not run_ids:
        return
    try:
        _tick_queue.put_nowait({
            "exchange": exchange, "symbol": symbol, "ltp": ltp,
            "run_ids": run_ids,
        })
    except asyncio.QueueFull:
        logger.warning("inject_tick: queue full")
