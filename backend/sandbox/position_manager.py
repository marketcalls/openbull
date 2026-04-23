"""
Sandbox position book.

Positions are updated atomically on every fill (:func:`apply_fill`). We track
realized and unrealized PnL separately so the dashboard can show both; the
*running average* cost is used for BUY accumulation and FIFO-style offset on
SELL (same as most brokers report).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.sandbox import SandboxPosition
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def _get_or_create(
    db: Session, user_id: int, symbol: str, exchange: str, product: str
) -> SandboxPosition:
    row = db.execute(
        select(SandboxPosition).where(
            SandboxPosition.user_id == user_id,
            SandboxPosition.symbol == symbol,
            SandboxPosition.exchange == exchange,
            SandboxPosition.product == product,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SandboxPosition(
            user_id=user_id, symbol=symbol, exchange=exchange, product=product
        )
        db.add(row)
        db.flush()
    return row


def apply_fill(
    user_id: int,
    symbol: str,
    exchange: str,
    product: str,
    action: str,
    quantity: int,
    price: float,
) -> float:
    """Update the position for a single fill. Returns the realized PnL produced."""
    action = action.upper()
    signed_qty = quantity if action == "BUY" else -quantity

    realized_delta = 0.0

    with session_scope() as db:
        pos = _get_or_create(db, user_id, symbol, exchange, product)
        old_net = pos.net_quantity
        old_avg = pos.average_price

        # Same-direction accumulation → new weighted average
        if (old_net >= 0 and signed_qty > 0) or (old_net <= 0 and signed_qty < 0):
            new_net = old_net + signed_qty
            new_total_cost = old_avg * abs(old_net) + price * abs(signed_qty)
            new_avg = new_total_cost / abs(new_net) if new_net != 0 else 0.0
            pos.net_quantity = new_net
            pos.average_price = round(new_avg, 4)
        else:
            # Opposite direction → realize PnL on the offset portion
            offset_qty = min(abs(old_net), abs(signed_qty))
            if old_net > 0:
                # was long, now selling → gain = (sell_price - old_avg) * qty
                realized_delta = (price - old_avg) * offset_qty
            else:
                # was short, now buying → gain = (old_avg - buy_price) * qty
                realized_delta = (old_avg - price) * offset_qty

            remaining = abs(signed_qty) - offset_qty
            new_net = old_net + signed_qty
            pos.net_quantity = new_net
            if new_net == 0:
                pos.average_price = 0.0
            elif remaining > 0:
                # Flipped direction with leftover — leftover opens at fill price
                pos.average_price = round(price, 4)
            # else: same-direction remainder means old_avg unchanged

            pos.realized_pnl = round(pos.realized_pnl + realized_delta, 4)

        # Intraday bucket
        if action == "BUY":
            pos.day_buy_quantity += quantity
            pos.day_buy_value = round(pos.day_buy_value + price * quantity, 2)
        else:
            pos.day_sell_quantity += quantity
            pos.day_sell_value = round(pos.day_sell_value + price * quantity, 2)

        # LTP/PnL refresh will happen in mark_to_market; keep pos.pnl consistent for now
        pos.pnl = round(pos.realized_pnl + pos.unrealized_pnl, 4)

    return round(realized_delta, 4)


def mark_to_market(
    user_id: int, symbol: str, exchange: str, product: str, ltp: float
) -> None:
    """Refresh ``ltp`` and unrealized PnL for a single position."""
    if ltp <= 0:
        return
    with session_scope() as db:
        pos = db.execute(
            select(SandboxPosition).where(
                SandboxPosition.user_id == user_id,
                SandboxPosition.symbol == symbol,
                SandboxPosition.exchange == exchange,
                SandboxPosition.product == product,
            )
        ).scalar_one_or_none()
        if pos is None or pos.net_quantity == 0:
            return
        pos.ltp = round(ltp, 4)
        # Long: (ltp - avg) * qty. Short: (avg - ltp) * qty.
        if pos.net_quantity > 0:
            pos.unrealized_pnl = round((ltp - pos.average_price) * pos.net_quantity, 4)
        else:
            pos.unrealized_pnl = round((pos.average_price - ltp) * abs(pos.net_quantity), 4)
        pos.pnl = round(pos.realized_pnl + pos.unrealized_pnl, 4)


def get_positions(user_id: int) -> list[dict]:
    """Broker-compatible position payload — shape matches the transform layer."""
    with session_scope() as db:
        rows = db.execute(
            select(SandboxPosition).where(SandboxPosition.user_id == user_id)
        ).scalars().all()
        return [
            {
                "symbol": r.symbol,
                "exchange": r.exchange,
                "product": r.product,
                "quantity": r.net_quantity,
                "netqty": r.net_quantity,
                "buyqty": r.day_buy_quantity,
                "sellqty": r.day_sell_quantity,
                "average_price": round(r.average_price, 2),
                "averageprice": round(r.average_price, 2),
                "ltp": round(r.ltp, 2),
                "pnl": round(r.pnl, 2),
                "unrealized_pnl": round(r.unrealized_pnl, 2),
                "realized_pnl": round(r.realized_pnl, 2),
            }
            for r in rows
        ]
