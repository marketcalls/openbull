"""
Daily today_realized_pnl reset.

Runs once at 00:00 IST via the sandbox scheduler. Zeroes out the *today*
realized-PnL bucket on both ``SandboxFund`` and ``SandboxPosition`` rows so
the new session starts at 0 even if the app stayed up overnight.

Cumulative ``realized_pnl`` is preserved — only the today bucket resets,
matching openalgo's session-boundary logic in its position_manager.
"""

from __future__ import annotations

import logging

from sqlalchemy import update

from backend.models.sandbox import SandboxFund, SandboxPosition
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def reset_today_pnl() -> int:
    """Zero ``today_realized_pnl`` on every fund + position row.

    Returns the total number of rows touched. Idempotent — safe to call
    multiple times in the same minute.
    """
    with session_scope() as db:
        f = db.execute(
            update(SandboxFund)
            .where(SandboxFund.today_realized_pnl != 0)
            .values(today_realized_pnl=0.0)
        )
        p = db.execute(
            update(SandboxPosition)
            .where(SandboxPosition.today_realized_pnl != 0)
            .values(today_realized_pnl=0.0)
        )
    total = (f.rowcount or 0) + (p.rowcount or 0)
    if total:
        logger.info(
            "sandbox daily reset: zeroed today_realized_pnl on %d funds, %d positions",
            f.rowcount or 0, p.rowcount or 0,
        )
    return total
