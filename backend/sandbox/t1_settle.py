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
from datetime import datetime

from sqlalchemy import delete, select

from backend.models.sandbox import SandboxHolding, SandboxPosition
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def settle_cnc_to_holdings() -> int:
    """Move long CNC positions into holdings. Returns the number of rows moved."""
    moved = 0
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

            if existing is None:
                db.add(
                    SandboxHolding(
                        user_id=pos.user_id,
                        symbol=pos.symbol,
                        exchange=pos.exchange,
                        quantity=int(pos.net_quantity),
                        average_price=round(pos.average_price, 4),
                        ltp=round(pos.ltp, 4),
                        pnl=0.0,
                        pnlpercent=0.0,
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
                    existing.average_price = round(total_cost / total_qty, 4)
                    existing.quantity = total_qty
                    existing.ltp = round(pos.ltp, 4)

            # Clear the intraday CNC position — it has now been "delivered".
            db.execute(
                delete(SandboxPosition).where(SandboxPosition.id == pos.id)
            )
            moved += 1

    if moved:
        logger.info("T+1 settlement: moved %d CNC positions to holdings", moved)
    return moved
