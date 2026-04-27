"""
End-of-day T+1 settlement for CNC positions.

Any CNC position with ``net_quantity > 0`` is moved into ``sandbox_holdings``
after the trading day closes. In real markets this takes T+1 to settle; in
the sandbox we do it in one step at EOD so the holdings page has something
to show on subsequent sessions.

Short CNC positions are ignored (you can't hold a negative equity position).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from backend.models.sandbox import SandboxHolding, SandboxPosition
from backend.sandbox import fund_manager
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _compute_pnl_percent(avg_price: float, ltp: float) -> float:
    if avg_price <= 0:
        return 0.0
    return round(((ltp - avg_price) / avg_price) * 100.0, 4)


def settle_cnc_to_holdings() -> int:
    """Move long CNC positions into holdings. Returns the number of rows moved.

    Margin bookkeeping for the move follows openalgo:
    ``fund.used_margin`` decreases by the position's blocked margin, but
    ``available`` stays the same — the cash that backed the margin is now
    "in" the asset (visible as ``totalholdingvalue`` on the holdings book).
    Without this step the ``used_margin`` field would drift over time as
    settled positions vanished without releasing their margin lock.
    """
    moved = 0
    settle_iso = datetime.now(tz=IST).date().isoformat()
    # Collected per-user margin transfers — applied after the position
    # transaction commits so the funds row update stays in its own scope
    # under the per-user lock.
    margin_transfers: dict[int, float] = {}
    with session_scope() as db:
        cnc_positions = (
            db.execute(
                select(SandboxPosition).where(
                    SandboxPosition.product == "CNC",
                    SandboxPosition.net_quantity > 0,
                )
            )
            .scalars()
            .all()
        )

        for pos in cnc_positions:
            existing = db.execute(
                select(SandboxHolding).where(
                    SandboxHolding.user_id == pos.user_id,
                    SandboxHolding.symbol == pos.symbol,
                    SandboxHolding.exchange == pos.exchange,
                )
            ).scalar_one_or_none()

            avg_price = round(pos.average_price, 4)
            ltp = round(pos.ltp, 4) if pos.ltp else avg_price
            pnl = round((ltp - avg_price) * pos.net_quantity, 2)

            if existing is None:
                db.add(
                    SandboxHolding(
                        user_id=pos.user_id,
                        symbol=pos.symbol,
                        exchange=pos.exchange,
                        quantity=int(pos.net_quantity),
                        average_price=avg_price,
                        ltp=ltp,
                        pnl=pnl,
                        pnlpercent=_compute_pnl_percent(avg_price, ltp),
                        settlement_date=settle_iso,
                    )
                )
            else:
                # Running-average merge so a second day of accumulation does
                # the right thing rather than overwriting.
                total_qty = existing.quantity + int(pos.net_quantity)
                if total_qty > 0:
                    total_cost = (
                        existing.average_price * existing.quantity
                        + pos.average_price * pos.net_quantity
                    )
                    new_avg = round(total_cost / total_qty, 4)
                    existing.average_price = new_avg
                    existing.quantity = total_qty
                    existing.ltp = ltp
                    existing.pnl = round((ltp - new_avg) * total_qty, 2)
                    existing.pnlpercent = _compute_pnl_percent(new_avg, ltp)
                    existing.settlement_date = settle_iso

            # Margin that was locked against this position is now backing
            # the holding row instead. Accumulate per-user so we apply the
            # transfer in one pass after the position writes commit.
            pos_margin = float(pos.margin_blocked or 0.0)
            if pos_margin > 0:
                margin_transfers[pos.user_id] = (
                    margin_transfers.get(pos.user_id, 0.0) + pos_margin
                )

            # Clear the intraday CNC position — it has now been "delivered".
            db.execute(
                delete(SandboxPosition).where(SandboxPosition.id == pos.id)
            )
            moved += 1

    for uid, amount in margin_transfers.items():
        try:
            fund_manager.transfer_margin_to_holdings(uid, amount)
        except Exception:
            logger.exception(
                "T+1 settlement: transfer_margin_to_holdings failed user=%d", uid
            )

    if moved:
        logger.info("T+1 settlement: moved %d CNC positions to holdings", moved)
    return moved
