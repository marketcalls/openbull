"""
Weekly wipe of sandbox state.

If ``sandbox_config.reset_day`` is ``"Never"`` this is disabled. Otherwise on
the configured day (``Sunday`` by default) at ``reset_time`` IST we reset
every user's sandbox to the current ``starting_capital`` — orders, trades,
positions, holdings, daily P&L are all deleted, funds restored.

This is deliberately *per-user* rather than dropping tables: the starting
capital applied to each user is whatever the global config says at the
moment of reset.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select

from backend.models.sandbox import (
    SandboxDailyPnL,
    SandboxFund,
    SandboxHolding,
    SandboxOrder,
    SandboxPosition,
    SandboxTrade,
)
from backend.sandbox._db import session_scope
from backend.sandbox import fund_manager

logger = logging.getLogger(__name__)


def wipe_all_users() -> int:
    """Reset every user that has any sandbox state. Returns users affected."""
    users: set[int] = set()
    with session_scope() as db:
        for model in (SandboxFund, SandboxOrder, SandboxTrade, SandboxPosition, SandboxHolding):
            for uid in db.execute(select(model.user_id).distinct()).scalars().all():
                users.add(int(uid))

        if not users:
            return 0

        # Bulk-delete first; per-user fund reset after the transaction commits
        # (fund_manager opens its own sessions).
        for model in (SandboxOrder, SandboxTrade, SandboxPosition, SandboxHolding, SandboxDailyPnL):
            db.execute(delete(model))

    for uid in users:
        try:
            fund_manager.reset_funds(uid)
        except Exception:
            logger.exception("Weekly reset: funds reset failed for user %d", uid)

    logger.info("sandbox weekly reset: wiped %d users", len(users))
    return len(users)
