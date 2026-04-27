"""
Sandbox position book.

Positions are updated atomically on every fill (:func:`apply_fill`). We track
realized and unrealized PnL separately so the dashboard can show both; the
*running average* cost is used for BUY accumulation and FIFO-style offset on
SELL (same as most brokers report).

Margin lifecycle (matches openalgo's sandbox):

* New position / accumulation: the order's blocked margin transfers from
  ``SandboxOrder.margin_blocked`` into ``SandboxPosition.margin_blocked``.
  No fund movement — the cash is already locked, only its bookkeeping bucket
  changes.
* Reduce: ``offset_qty / abs(old_qty)`` of the position's margin is released
  back to *available* together with the realized PnL on the offset, in a
  single :func:`fund_manager.release_margin` call (atomic transaction).
* Full close: the entire position margin is released along with realized PnL.
* Reverse with leftover: offset portion releases pro-rata, the residual
  position picks up whatever margin the order was carrying for the excess
  (see ``place_order`` for the position-aware sizing).

The per-user lock is held by the caller (the execution engine) for the
duration of fill processing — see :mod:`backend.sandbox.execution_engine`.
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


def get_position_snapshot(
    user_id: int, symbol: str, exchange: str, product: str
) -> tuple[int, float] | None:
    """Read-only snapshot used by ``place_order`` for position-aware margin
    sizing. Returns ``(net_quantity, margin_blocked)`` or ``None`` if the
    position doesn't exist / is flat."""
    with session_scope() as db:
        row = db.execute(
            select(SandboxPosition.net_quantity, SandboxPosition.margin_blocked).where(
                SandboxPosition.user_id == user_id,
                SandboxPosition.symbol == symbol,
                SandboxPosition.exchange == exchange,
                SandboxPosition.product == product,
            )
        ).first()
        if row is None or int(row[0]) == 0:
            return None
        return int(row[0]), float(row[1] or 0.0)


def apply_fill(
    user_id: int,
    symbol: str,
    exchange: str,
    product: str,
    action: str,
    quantity: int,
    price: float,
    order_margin: float = 0.0,
) -> tuple[float, float]:
    """Update the position for a single fill.

    Returns ``(realized_pnl, margin_to_release)``:
      * ``realized_pnl`` — PnL booked on the offset portion of this fill (0
        for new / accumulating positions).
      * ``margin_to_release`` — margin that must be released from
        ``used_margin`` back to *available*. The caller pairs this with the
        realized PnL in a single :func:`fund_manager.release_margin` call so
        cash and PnL move together.

    For the same-direction (new / accumulate) path, the order's margin is
    moved from the order row into the position row inside this function — the
    return tuple is ``(0, 0)`` because nothing leaves *used_margin*.
    """
    action = action.upper()
    signed_qty = quantity if action == "BUY" else -quantity

    realized_delta = 0.0
    margin_to_release = 0.0

    with session_scope() as db:
        pos = _get_or_create(db, user_id, symbol, exchange, product)
        old_net = pos.net_quantity
        old_avg = pos.average_price
        old_margin = float(pos.margin_blocked or 0.0)

        # ---------------------------------------------------------------
        # Same direction (or fresh position) — accumulate, transfer margin
        # ---------------------------------------------------------------
        if (old_net >= 0 and signed_qty > 0) or (old_net <= 0 and signed_qty < 0):
            new_net = old_net + signed_qty
            new_total_cost = old_avg * abs(old_net) + price * abs(signed_qty)
            new_avg = new_total_cost / abs(new_net) if new_net != 0 else 0.0
            pos.net_quantity = new_net
            pos.average_price = round(new_avg, 4)
            # Transfer the order's blocked margin onto the position. No fund
            # movement — `used_margin` already reflects this amount; only its
            # bookkeeping bucket (order → position) changes.
            pos.margin_blocked = round(old_margin + float(order_margin or 0.0), 2)

        # ---------------------------------------------------------------
        # Opposite direction — realize PnL, release margin pro-rata
        # ---------------------------------------------------------------
        else:
            offset_qty = min(abs(old_net), abs(signed_qty))
            if old_net > 0:
                # was long, now selling → gain = (sell_price - old_avg) * qty
                realized_delta = (price - old_avg) * offset_qty
            else:
                # was short, now buying → gain = (old_avg - buy_price) * qty
                realized_delta = (old_avg - price) * offset_qty

            remaining = abs(signed_qty) - offset_qty
            new_net = old_net + signed_qty

            # Pro-rata margin release for the offset portion.
            if abs(old_net) > 0 and old_margin > 0:
                proportion = offset_qty / float(abs(old_net))
                margin_to_release = round(old_margin * proportion, 2)
            else:
                margin_to_release = 0.0

            pos.net_quantity = new_net
            if new_net == 0:
                # Fully closed — drop average + clear the rest of the margin
                # in case rounding left a sliver behind.
                pos.average_price = 0.0
                margin_to_release = round(old_margin, 2)
                pos.margin_blocked = 0.0
            elif remaining > 0:
                # Reversed direction with leftover — the new (opposite-side)
                # position is opened at ``price``. The order was sized to
                # block margin only for the leftover (see ``place_order``
                # position-aware logic), so order_margin == margin for the
                # new residual position.
                pos.average_price = round(price, 4)
                # Old position is fully closed → release ALL of its margin.
                margin_to_release = round(old_margin, 2)
                pos.margin_blocked = round(float(order_margin or 0.0), 2)
            else:
                # Reduced but not closed — keep the residual margin on the
                # position (= old_margin minus what we released).
                pos.margin_blocked = round(max(0.0, old_margin - margin_to_release), 2)
                # average_price unchanged

            pos.realized_pnl = round(pos.realized_pnl + realized_delta, 4)
            pos.today_realized_pnl = round(
                float(pos.today_realized_pnl or 0.0) + realized_delta, 4
            )

        # ---------------------------------------------------------------
        # Intraday + display fields
        # ---------------------------------------------------------------
        if action == "BUY":
            pos.day_buy_quantity += quantity
            pos.day_buy_value = round(pos.day_buy_value + price * quantity, 2)
        else:
            pos.day_sell_quantity += quantity
            pos.day_sell_value = round(pos.day_sell_value + price * quantity, 2)

        # LTP/PnL refresh will happen in mark_to_market; keep pos.pnl
        # consistent for now so the orderbook UI shows something sensible.
        pos.pnl = round(pos.realized_pnl + pos.unrealized_pnl, 4)

    return round(realized_delta, 4), round(margin_to_release, 2)


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
                "today_realized_pnl": round(float(r.today_realized_pnl or 0.0), 2),
                "margin_blocked": round(float(r.margin_blocked or 0.0), 2),
            }
            for r in rows
        ]
