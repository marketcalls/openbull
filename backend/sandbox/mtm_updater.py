"""
Periodic mark-to-market updater.

Every ``mtm_update_interval_seconds`` (default 5s) a daemon thread refreshes
LTP and unrealized P&L on every open ``SandboxPosition`` row, then updates
``SandboxFund.unrealized_pnl`` per user with the sum of position unrealized
P&L. This is the openalgo-equivalent of the MTM background loop — without it
the dashboards would only refresh on fills.

Cheap operation: one query for open positions, one cache lookup per symbol
(no broker round trip), one UPDATE per non-flat position. Skipped silently
if the market data cache hasn't received a tick yet for a given symbol.

The thread holds NO long-lived locks. Each row update opens its own
``session_scope`` transaction and commits immediately, so a slow row can't
block fills or order placement. The fund-level write goes through
``fund_manager.set_unrealized_pnl`` which acquires the per-user lock for the
duration of the single field assignment.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Optional

from sqlalchemy import select

from backend.models.sandbox import SandboxPosition
from backend.sandbox import fund_manager
from backend.sandbox._db import session_scope
from backend.sandbox.config import get_mtm_update_interval

logger = logging.getLogger(__name__)


_running = False
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _refresh_once() -> int:
    """Run one MTM pass. Returns the number of position rows touched.

    Reads positions, looks up LTP for each (live tick cache → broker quote
    fallback), recomputes unrealized P&L, then rolls per-user unrealized
    P&L into the funds row. Two separate scopes (positions then funds) so
    we don't hold the position write transaction open while taking the
    per-user fund lock — keeps lock-acquisition order one-way (positions →
    funds) and prevents deadlocks.

    Positions whose symbol has no real LTP available (no live tick *and*
    no broker quote — typically on a fresh start before any auth is set)
    are left untouched: their last *real* unrealized P&L stays on the row
    and is included in the funds total. We never invent a price.
    """
    from backend.sandbox.quote_helper import get_ltp as get_ltp_with_fallback

    user_unrealized: dict[int, float] = defaultdict(float)
    touched = 0

    # Pass 1: refresh per-position LTP + unrealized PnL
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxPosition).where(SandboxPosition.net_quantity != 0)
            )
            .scalars()
            .all()
        )
        for pos in rows:
            ltp = get_ltp_with_fallback(pos.user_id, pos.symbol, pos.exchange)
            if ltp is None or ltp <= 0:
                # No real LTP obtainable — preserve the previously-computed
                # unrealized PnL on the funds total (don't zero it, don't
                # invent a number). The position row itself is left alone.
                user_unrealized[pos.user_id] += float(pos.unrealized_pnl or 0.0)
                continue
            pos.ltp = round(float(ltp), 4)
            if pos.net_quantity > 0:
                u = (float(ltp) - pos.average_price) * pos.net_quantity
            else:
                u = (pos.average_price - float(ltp)) * abs(pos.net_quantity)
            pos.unrealized_pnl = round(u, 4)
            pos.pnl = round((pos.realized_pnl or 0.0) + pos.unrealized_pnl, 4)
            user_unrealized[pos.user_id] += pos.unrealized_pnl
            touched += 1

    # Pass 2: roll per-user unrealized PnL up to the funds row
    for user_id, total in user_unrealized.items():
        try:
            fund_manager.set_unrealized_pnl(user_id, round(float(total), 2))
        except Exception:
            logger.exception("mtm: set_unrealized_pnl failed for user %d", user_id)

    return touched


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            interval = max(1, get_mtm_update_interval())
        except Exception:
            interval = 5
        try:
            _refresh_once()
        except Exception:
            logger.exception("mtm: refresh pass raised")
        _stop_event.wait(interval)


def start() -> None:
    """Launch the MTM daemon. Idempotent."""
    global _running, _thread
    with _lock:
        if _running:
            return
        _running = True
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, name="sandbox-mtm", daemon=True)
        _thread.start()
        logger.info("sandbox MTM updater started")


def stop(timeout: float = 2.0) -> None:
    global _running, _thread
    with _lock:
        if not _running:
            return
        _running = False
        _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=timeout)
    _thread = None
