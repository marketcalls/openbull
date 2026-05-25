"""Per-strategy WebSocket broadcaster.

The tick processor and the audit subscriber publish messages here; the
WS endpoint (``/ws/strategy/{id}``) reads from a per-client queue.

Why a custom registry instead of Redis pub/sub: Phase 6 is single-process.
A future multi-worker deployment will add a Redis pub/sub fan-out behind
the same API (each worker subscribes to the channel for its connected
clients), so the WS endpoint's interface stays unchanged.

Throttle: deltas are coalesced to at most ``DELTA_THROTTLE_MS`` per
strategy. Events (state transitions) are never throttled — operators
must see ``leg_sl_hit`` the moment it fires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DELTA_THROTTLE_MS = 100  # 10 messages/sec/strategy ceiling

# strategy_id -> list of subscriber queues
_subscribers: dict[int, list[asyncio.Queue]] = {}
# strategy_id -> last delta dispatch timestamp (epoch ms) for throttling
_last_delta_ts: dict[int, float] = {}
_lock = asyncio.Lock()


async def register(strategy_id: int) -> asyncio.Queue:
    """Called from the WS endpoint when a client connects."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    async with _lock:
        _subscribers.setdefault(strategy_id, []).append(q)
    logger.debug("WS subscriber registered: strategy_id=%d", strategy_id)
    return q


async def unregister(strategy_id: int, q: asyncio.Queue) -> None:
    """Called on disconnect or error."""
    async with _lock:
        lst = _subscribers.get(strategy_id, [])
        if q in lst:
            lst.remove(q)
        if not lst:
            _subscribers.pop(strategy_id, None)


def _broadcast_nowait(strategy_id: int, message: dict[str, Any]) -> None:
    """Fan out one message to every connected subscriber. Drops on full queue.

    Sync-safe: callable from the tick callback thread without an event loop.
    """
    subs = _subscribers.get(strategy_id, [])
    if not subs:
        return
    for q in list(subs):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the message rather than block; the snapshot on reconnect
            # will repair any divergence. Better than slowing the publisher.
            logger.debug("WS subscriber queue full — dropping for strategy_id=%d", strategy_id)


def push_event(strategy_id: int, event_message: dict[str, Any]) -> None:
    """Fire-and-forget broadcast of a strategy_event WS frame.

    Always sent (no throttling). The audit subscriber calls this from
    its thread-pool worker.
    """
    _broadcast_nowait(strategy_id, event_message)


def push_delta(strategy_id: int, delta_message: dict[str, Any]) -> None:
    """Throttled broadcast of a tick-driven delta.

    Coalesces to ``DELTA_THROTTLE_MS`` per strategy. The tick processor
    can call this on every tick; only ~10/sec actually reach clients.
    """
    now_ms = time.monotonic() * 1000.0
    last = _last_delta_ts.get(strategy_id, 0.0)
    if now_ms - last < DELTA_THROTTLE_MS:
        return
    _last_delta_ts[strategy_id] = now_ms
    _broadcast_nowait(strategy_id, delta_message)


def push_terminal(strategy_id: int, message: dict[str, Any]) -> None:
    """Run-stopped frame — always sent, drops the throttle reservation."""
    _last_delta_ts.pop(strategy_id, None)
    _broadcast_nowait(strategy_id, message)


# ---------------------------------------------------------------------------
# Data-update frames (real-time replacement for REST polling)
# ---------------------------------------------------------------------------
#
# These are never throttled — they fire at the moment a row is persisted
# or its status changes. Volume per strategy is naturally low (one
# frame per order/exit/reconcile, ~10 frames per run total), nothing
# like the tick-driven delta firehose. Each frame triggers exactly one
# React Query cache update on the client, replacing the 5s timer poll.
# ---------------------------------------------------------------------------


def push_order_update(strategy_id: int, order_payload: dict[str, Any]) -> None:
    """Fan out a single order row update.

    Fires twice per order lifecycle:
      1. Right after ``repo.record_order`` returns (status=open or rejected).
      2. After ``reconcile_order_fill`` overwrites status/avg_fill_price.

    The frame carries the same shape as the REST ``/orders`` endpoint's
    row format so the client can substitute it straight into its cache.
    """
    _broadcast_nowait(strategy_id, {
        "type": "order_update",
        "ts_ms_utc": int(time.time() * 1000),
        "order": order_payload,
    })


def push_strategy_update(strategy_id: int, strategy_payload: dict[str, Any]) -> None:
    """Fan out a strategy-header update — status / current_run_id flip /
    live_enabled toggle / webhook_locked transition.

    Carries a partial payload (only the fields that may have changed). The
    client merges into its strategy detail cache.
    """
    _broadcast_nowait(strategy_id, {
        "type": "strategy_update",
        "ts_ms_utc": int(time.time() * 1000),
        "strategy": strategy_payload,
    })


def push_run_update(strategy_id: int, run_payload: dict[str, Any]) -> None:
    """Fan out a run row update — start, stop, P&L snapshot.

    The /runs tab and the strategy header both need to know when a new
    run row appears or an existing one finalizes. Client invalidates the
    /runs query on this frame.
    """
    _broadcast_nowait(strategy_id, {
        "type": "run_update",
        "ts_ms_utc": int(time.time() * 1000),
        "run": run_payload,
    })


def has_subscribers(strategy_id: int) -> bool:
    return bool(_subscribers.get(strategy_id))


# ---------------------------------------------------------------------------
# Row -> WS frame formatters (shared between engine + reconciler).
# Mirrors backend/routers/strategy_module.py:_format_order so the client
# can paste the frame straight into its React Query cache.
# ---------------------------------------------------------------------------


def format_order_row(order_row: Any) -> dict[str, Any]:
    """Same shape as the REST /orders row. Lives here so the engine can
    publish a WS frame at the same touchpoint that the REST endpoint
    would have served on a poll."""
    from backend.strategy.time_utils import format_ist
    return {
        "id": order_row.id,
        "leg_id": order_row.leg_id,
        "kind": order_row.kind,
        "broker_order_id": order_row.broker_order_id,
        "symbol": order_row.symbol,
        "exchange": order_row.exchange,
        "action": order_row.action,
        "qty": order_row.qty,
        "pricetype": order_row.pricetype,
        "price": float(order_row.price) if order_row.price is not None else 0.0,
        "trigger_price": (
            float(order_row.trigger_price) if order_row.trigger_price is not None else 0.0
        ),
        "status": order_row.status,
        "placed_at": format_ist(order_row.placed_at),
        "filled_at": format_ist(order_row.filled_at),
        "avg_fill_price": (
            float(order_row.avg_fill_price) if order_row.avg_fill_price is not None else None
        ),
        "filled_qty": order_row.filled_qty,
        "reject_reason": order_row.reject_reason,
    }


def format_run_row(run_row: Any) -> dict[str, Any]:
    """Same shape as the REST /runs row."""
    from backend.strategy.time_utils import format_ist
    return {
        "id": run_row.id,
        "strategy_id": run_row.strategy_id,
        "mode": run_row.mode,
        "broker": run_row.broker,
        "started_at": format_ist(run_row.started_at),
        "stopped_at": format_ist(run_row.stopped_at),
        "stop_reason": run_row.stop_reason,
        "pnl_realized": float(run_row.pnl_realized) if run_row.pnl_realized is not None else 0.0,
        "pnl_peak": float(run_row.pnl_peak) if run_row.pnl_peak is not None else 0.0,
        "pnl_trough": float(run_row.pnl_trough) if run_row.pnl_trough is not None else 0.0,
        "trigger_source": run_row.trigger_source,
    }
