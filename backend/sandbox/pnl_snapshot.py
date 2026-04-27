"""
Daily P&L snapshot.

Runs once per trading day near EOD. For every user with any sandbox activity
we compute a single aggregate row in ``sandbox_daily_pnl`` so the MyPnL page
can draw a history chart and the user can see day-over-day realized /
unrealized swings.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select

from backend.models.sandbox import (
    SandboxDailyPnL,
    SandboxFund,
    SandboxHolding,
    SandboxPosition,
    SandboxTrade,
)
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def snapshot_for_date(snapshot_date: date | None = None) -> int:
    """Write one ``sandbox_daily_pnl`` row per user. Idempotent per (user, date).

    Returns the number of rows written. Skips users whose snapshot for the
    given date already exists so re-runs are safe.
    """
    # The snapshot table's ``snapshot_date`` column is VARCHAR(10) (an
    # ISO-formatted string), while ``func.date(timestamp)`` in Postgres
    # returns a real DATE. We need both forms — the string for the table
    # comparison, the date object for the trade-count subquery — otherwise
    # Postgres errors out with "operator does not exist: date = varchar".
    target_date_obj = snapshot_date or date.today()
    target_iso = target_date_obj.isoformat()

    written = 0
    with session_scope() as db:
        # Every user that has any sandbox state = union(funds, positions, holdings, trades).
        user_rows = set()
        for model in (SandboxFund, SandboxPosition, SandboxHolding, SandboxTrade):
            for uid in db.execute(select(model.user_id).distinct()).scalars().all():
                user_rows.add(int(uid))

        for user_id in user_rows:
            exists = db.execute(
                select(SandboxDailyPnL.id).where(
                    SandboxDailyPnL.user_id == user_id,
                    SandboxDailyPnL.snapshot_date == target_iso,
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue

            fund = db.execute(
                select(SandboxFund).where(SandboxFund.user_id == user_id)
            ).scalar_one_or_none()

            positions_pnl = (
                db.execute(
                    select(func.coalesce(func.sum(SandboxPosition.pnl), 0.0)).where(
                        SandboxPosition.user_id == user_id
                    )
                ).scalar()
                or 0.0
            )
            holdings_pnl = (
                db.execute(
                    select(func.coalesce(func.sum(SandboxHolding.pnl), 0.0)).where(
                        SandboxHolding.user_id == user_id
                    )
                ).scalar()
                or 0.0
            )
            trades_today = (
                db.execute(
                    select(func.count()).select_from(SandboxTrade).where(
                        SandboxTrade.user_id == user_id,
                        func.date(SandboxTrade.timestamp) == target_date_obj,
                    )
                ).scalar()
                or 0
            )

            realized = fund.realized_pnl if fund else 0.0
            unrealized = fund.unrealized_pnl if fund else 0.0
            available = fund.available if fund else 0.0
            used = fund.used_margin if fund else 0.0
            start = fund.starting_capital if fund else 0.0

            db.add(
                SandboxDailyPnL(
                    user_id=user_id,
                    snapshot_date=target_iso,
                    starting_capital=round(start, 2),
                    available=round(available, 2),
                    used_margin=round(used, 2),
                    realized_pnl=round(realized, 2),
                    unrealized_pnl=round(unrealized, 2),
                    total_pnl=round(realized + unrealized, 2),
                    positions_pnl=round(float(positions_pnl), 2),
                    holdings_pnl=round(float(holdings_pnl), 2),
                    trades_count=int(trades_today),
                )
            )
            written += 1

    if written:
        logger.info("sandbox daily P&L: wrote %d snapshots for %s", written, target_iso)
    return written
