"""Quote-driven tick pump for the strategy module.

The strategy tick pipeline (``tick_feed`` -> ``tick_processor`` ->
``broadcast.push_delta``) is wired off the centralized ``MarketDataCache``.
The cache itself is populated by the broker WebSocket adapter — fine when
the user has the OptionChain / WebSocketTest page open and the broker WS
has explicitly subscribed to the strategy's contracts, but in sandbox
mode (or any time nobody else is streaming those symbols) the cache
stays empty for the strategy and live MTM is stuck.

This module closes the gap: while a strategy has at least one open
WS subscriber AND has an active run, a background task polls the
broker's quote REST API every ``_PUMP_INTERVAL_SEC`` for the strategy's
open-position symbols and hand-feeds each LTP into
``MarketDataCache.process_market_data`` as if a broker tick had
arrived. The existing tick processor then drives the per-leg MTM
recompute and ``broadcast.push_delta`` exactly as it does for live ticks.

Lifecycle:

    ws.py:strategy_ws on connect    -> live_quotes.start(strategy_id)
    ws.py:strategy_ws on disconnect -> live_quotes.stop(strategy_id)

The pump is reference-counted internally: two clients on the same
strategy reuse one task; the task only exits when the last client
unsubscribes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyOrder,
    SmStrategyRun,
)
from backend.services.market_data_cache import process_market_data
from backend.services.quotes_service import get_multi_quotes_with_auth
from backend.strategy import live_auth
from backend.strategy.scheduler import _resolve_user_broker

logger = logging.getLogger(__name__)


# Per-strategy task table + refcount. Concurrent subscribers reuse one task.
_pumps: dict[int, asyncio.Task] = {}
_refcounts: dict[int, int] = {}
_lock = asyncio.Lock()

# How often to fetch quotes. 2s is a sane default — fast enough that the
# UI feels live, slow enough that we don't hammer the broker API or burn
# rate-limit quota for an unattended page.
_PUMP_INTERVAL_SEC = 2.0


async def _resolve_open_symbols(
    db: AsyncSession, strategy: SmStrategy,
) -> list[tuple[str, str]]:
    """Distinct (exchange, symbol) pairs currently held by the strategy.

    Pulled from the order book: a symbol whose BUY total != SELL total is
    open. Closed (net=0) symbols are dropped — no point pumping ticks for
    a flat contract since unrealized is 0 either way. Caller filters by
    the current run only; ticks for older runs would just churn for
    rows the UI won't display.
    """
    if strategy.current_run_id is None:
        return []
    rows = (
        await db.execute(
            select(SmStrategyOrder.symbol, SmStrategyOrder.exchange,
                   SmStrategyOrder.action, SmStrategyOrder.filled_qty,
                   SmStrategyOrder.qty, SmStrategyOrder.status)
            .where(SmStrategyOrder.run_id == strategy.current_run_id)
        )
    ).all()
    nets: dict[tuple[str, str], int] = {}
    for symbol, exchange, action, filled_qty, qty, status in rows:
        if (status or "").lower() != "complete":
            continue
        delta = int(filled_qty or qty or 0)
        if delta <= 0:
            continue
        if (action or "").upper() != "BUY":
            delta = -delta
        nets[(exchange, symbol)] = nets.get((exchange, symbol), 0) + delta
    return [k for k, v in nets.items() if v != 0]


async def _pump_loop(strategy_id: int) -> None:
    """Background task that fetches quotes + injects ticks until refcount
    drops to 0.

    Stays defensive: if the strategy stops, the run is None, or the broker
    auth fails, we still sleep and retry — the operator may resume the
    strategy without disconnecting the page.
    """
    logger.info("live_quotes pump started for strategy=%d", strategy_id)
    try:
        while True:
            async with _lock:
                if _refcounts.get(strategy_id, 0) <= 0:
                    break
            try:
                await _one_tick(strategy_id)
            except Exception:
                logger.exception(
                    "live_quotes pump iteration failed for strategy=%d", strategy_id,
                )
            await asyncio.sleep(_PUMP_INTERVAL_SEC)
    finally:
        async with _lock:
            _pumps.pop(strategy_id, None)
        logger.info("live_quotes pump stopped for strategy=%d", strategy_id)


async def _one_tick(strategy_id: int) -> None:
    """One fetch + inject cycle."""
    async with async_session() as db:
        strategy = await db.get(SmStrategy, strategy_id)
        if strategy is None or strategy.status != "running":
            return
        symbols = await _resolve_open_symbols(db, strategy)
        if not symbols:
            return
        # Best-effort broker auth — sandbox sessions still want a live
        # broker session to source quotes from. Mirrors the engine's
        # webhook-start asymmetry.
        broker = await _resolve_user_broker(db, strategy.user_id)
        if not broker or broker == "webhook-sandbox":
            return
        ctx = await live_auth.resolve_live_auth(
            db, user_id=strategy.user_id, broker=broker,
        )
        if ctx is None:
            return
        auth_token = ctx.auth_token
        cfg = ctx.config

    # The quote API is sync — run in default executor so we don't pin
    # the event loop. Off-loop with no shared state is the cheapest path.
    symbols_list = [{"symbol": s, "exchange": e} for e, s in symbols]
    loop = asyncio.get_running_loop()
    ok, payload, _ = await loop.run_in_executor(
        None,
        lambda: get_multi_quotes_with_auth(
            symbols_list=symbols_list,
            auth_token=auth_token,
            broker=broker,
            config=cfg,
        ),
    )
    if not ok or not isinstance(payload, dict):
        return
    results = payload.get("results") or []
    now = time.time()
    for q in results:
        if not isinstance(q, dict):
            continue
        sym = q.get("symbol")
        exch = q.get("exchange")
        if not sym or not exch:
            continue
        # Broker plugins return LTP flat or nested under "data".
        inner = q.get("data") if isinstance(q.get("data"), dict) else q
        ltp = inner.get("ltp") if isinstance(inner, dict) else None
        if ltp is None:
            continue
        try:
            ltp_f = float(ltp)
        except (TypeError, ValueError):
            continue
        # Feed the cache. mode=1 (LTP) matches what the broker WS adapter
        # writes for LTP-mode subscriptions; tick_feed routes this to
        # the strategy run via its symbol-to-runs index.
        process_market_data({
            "symbol": sym,
            "exchange": exch,
            "mode": 1,
            "data": {
                "ltp": ltp_f,
                "timestamp": now,
                "volume": inner.get("volume", 0),
            },
        })


async def start(strategy_id: int) -> None:
    """Increment the refcount; spin up the pump if it isn't already running.

    Idempotent — safe to call once per WS connection. Pair every ``start``
    with exactly one ``stop`` on disconnect.
    """
    async with _lock:
        _refcounts[strategy_id] = _refcounts.get(strategy_id, 0) + 1
        if strategy_id not in _pumps:
            _pumps[strategy_id] = asyncio.create_task(_pump_loop(strategy_id))


async def stop(strategy_id: int) -> None:
    """Decrement the refcount; the pump shuts itself down when it reaches 0.

    Doesn't cancel the task synchronously — the loop checks refcount on
    every iteration so the next tick (within ``_PUMP_INTERVAL_SEC``)
    exits cleanly.
    """
    async with _lock:
        if strategy_id in _refcounts:
            _refcounts[strategy_id] -= 1
            if _refcounts[strategy_id] <= 0:
                _refcounts.pop(strategy_id, None)


def is_running(strategy_id: int) -> bool:
    """Debug helper — used by tests / future health endpoints."""
    return strategy_id in _pumps
