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


def has_subscribers(strategy_id: int) -> bool:
    return bool(_subscribers.get(strategy_id))
