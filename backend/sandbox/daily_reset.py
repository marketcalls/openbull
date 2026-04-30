"""
Daily session-boundary reset.

Runs once at 00:00 IST via the sandbox scheduler (and once at startup
via the catch-up processor). Zeroes the *today*-bucketed counters on
``SandboxFund`` and ``SandboxPosition`` so the new session starts at 0
even if the app stayed up overnight.

Touched fields:

* ``SandboxFund.today_realized_pnl``
* ``SandboxPosition.today_realized_pnl``
* ``SandboxPosition.day_buy_quantity`` / ``day_buy_value``
* ``SandboxPosition.day_sell_quantity`` / ``day_sell_value``

Cumulative ``realized_pnl`` and ``net_quantity`` are preserved — only
the per-day bookkeeping resets. Without the day-counter reset the
``buyqty`` / ``sellqty`` values returned by ``get_positions`` would
accumulate across every session forever, exactly the way openalgo's
``daily_reset_today_pnl`` was authored to avoid.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, update

from backend.models.sandbox import SandboxFund, SandboxPosition
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def reset_today_pnl() -> int:
    """Zero today_realized_pnl + day_* counters. Returns rows touched.

    Idempotent — safe to call multiple times in the same minute.
    """
    with session_scope() as db:
        f = db.execute(
            update(SandboxFund)
            .where(SandboxFund.today_realized_pnl != 0)
            .values(today_realized_pnl=0.0)
        )
        p = db.execute(
            update(SandboxPosition)
            .where(
                or_(
                    SandboxPosition.today_realized_pnl != 0,
                    SandboxPosition.day_buy_quantity != 0,
                    SandboxPosition.day_buy_value != 0,
                    SandboxPosition.day_sell_quantity != 0,
                    SandboxPosition.day_sell_value != 0,
                )
            )
            .values(
                today_realized_pnl=0.0,
                day_buy_quantity=0,
                day_buy_value=0.0,
                day_sell_quantity=0,
                day_sell_value=0.0,
            )
        )
    total = (f.rowcount or 0) + (p.rowcount or 0)
    if total:
        logger.info(
            "sandbox daily reset: zeroed today buckets on %d funds, %d positions",
            f.rowcount or 0, p.rowcount or 0,
        )
    return total
