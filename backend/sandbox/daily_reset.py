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

from sqlalchemy import text

from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def reset_today_pnl() -> int:
    """Zero today_realized_pnl + day_* counters. Returns rows touched.

    Uses raw SQL so the ``onupdate=func.now()`` default on ``updated_at``
    isn't fired. If we let it fire, every row's ``updated_at`` would jump
    to the reset time even though no trading activity happened — and
    ``get_positions`` then can't tell yesterday's closed flatten apart
    from today's. Mirrors openalgo's ``daily_reset_today_pnl`` raw-SQL
    approach for the same reason.

    Idempotent — safe to call multiple times in the same minute.
    """
    with session_scope() as db:
        f = db.execute(
            text(
                "UPDATE sandbox_funds SET today_realized_pnl = 0 "
                "WHERE today_realized_pnl != 0"
            )
        )
        p = db.execute(
            text(
                "UPDATE sandbox_positions SET "
                "today_realized_pnl = 0, "
                "day_buy_quantity = 0, day_buy_value = 0, "
                "day_sell_quantity = 0, day_sell_value = 0 "
                "WHERE today_realized_pnl != 0 "
                "OR day_buy_quantity != 0 OR day_buy_value != 0 "
                "OR day_sell_quantity != 0 OR day_sell_value != 0"
            )
        )
    total = (f.rowcount or 0) + (p.rowcount or 0)
    if total:
        logger.info(
            "sandbox daily reset: zeroed today buckets on %d funds, %d positions",
            f.rowcount or 0, p.rowcount or 0,
        )
    return total
