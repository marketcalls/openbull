"""
Sandbox order CRUD + fill bookkeeping.

Light on business logic — it's just a typed layer over the ``sandbox_orders``
and ``sandbox_trades`` tables. Price conditions, margin checks and fund
movement live in :mod:`backend.services.sandbox_service` and
:mod:`backend.sandbox.fund_manager`. The execution engine
(:mod:`backend.sandbox.execution_engine`) calls :func:`fill` when a pending
order's trigger condition is met.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.models.sandbox import SandboxOrder, SandboxTrade
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


def new_orderid() -> str:
    """Sandbox orderid in openalgo's format: ``YYMMDD`` prefix + 8-digit
    sequence (6-digit microseconds + 2-digit random). Sortable by date and
    free of vendor prefixes — matches openalgo's
    ``OrderManager._generate_order_id`` exactly so reports / logs / tooling
    that splits on the date prefix work the same way."""
    now = datetime.now()
    return f"{now.strftime('%y%m%d')}{now.microsecond:06d}{random.randint(0, 99):02d}"


def new_tradeid() -> str:
    """Trade id matching openalgo: ``TRADE-YYYYMMDD-HHMMSS-<8 hex>``."""
    now = datetime.now()
    return f"TRADE-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4).upper()}"


def create_order(
    *,
    user_id: int,
    symbol: str,
    exchange: str,
    action: str,
    quantity: int,
    pricetype: str,
    product: str,
    price: float = 0.0,
    trigger_price: float = 0.0,
    strategy: str | None = None,
    margin_blocked: float = 0.0,
    initial_status: str = "open",
    rejection_reason: str | None = None,
) -> SandboxOrder:
    orderid = new_orderid()
    with session_scope() as db:
        row = SandboxOrder(
            user_id=user_id,
            orderid=orderid,
            symbol=symbol,
            exchange=exchange,
            action=action.upper(),
            quantity=int(quantity),
            pricetype=pricetype.upper(),
            product=product.upper(),
            price=float(price),
            trigger_price=float(trigger_price),
            strategy=strategy,
            margin_blocked=float(margin_blocked),
            status=initial_status,
            rejection_reason=rejection_reason,
        )
        db.add(row)
        db.flush()
        # Force a read after flush so the caller gets the server-assigned id.
        db.refresh(row)
        db.expunge(row)
    return row


def get_order(user_id: int, orderid: str) -> SandboxOrder | None:
    with session_scope() as db:
        row = db.execute(
            select(SandboxOrder).where(
                SandboxOrder.user_id == user_id, SandboxOrder.orderid == orderid
            )
        ).scalar_one_or_none()
        if row is not None:
            db.expunge(row)
        return row


def list_orders(user_id: int) -> list[SandboxOrder]:
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxOrder)
                .where(SandboxOrder.user_id == user_id)
                .order_by(SandboxOrder.order_timestamp.desc())
            )
            .scalars()
            .all()
        )
        for r in rows:
            db.expunge(r)
        return list(rows)


def list_trades(user_id: int) -> list[SandboxTrade]:
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxTrade)
                .where(SandboxTrade.user_id == user_id)
                .order_by(SandboxTrade.timestamp.desc())
            )
            .scalars()
            .all()
        )
        for r in rows:
            db.expunge(r)
        return list(rows)


def list_pending_orders() -> list[SandboxOrder]:
    """For the execution engine. Returns open / trigger_pending across all users."""
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxOrder).where(
                    SandboxOrder.status.in_(("open", "trigger_pending"))
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            db.expunge(r)
        return list(rows)


def count_all_orders() -> int:
    """Used by /api/v1/analyzerstatus `total_logs` field."""
    from sqlalchemy import func

    with session_scope() as db:
        return db.execute(
            select(func.count()).select_from(SandboxOrder)
        ).scalar() or 0


def modify_order(
    user_id: int,
    orderid: str,
    *,
    quantity: int | None = None,
    price: float | None = None,
    trigger_price: float | None = None,
    pricetype: str | None = None,
) -> SandboxOrder | None:
    with session_scope() as db:
        row = db.execute(
            select(SandboxOrder).where(
                SandboxOrder.user_id == user_id, SandboxOrder.orderid == orderid
            )
        ).scalar_one_or_none()
        if row is None or row.status not in ("open", "trigger_pending"):
            return None
        if quantity is not None:
            row.quantity = int(quantity)
        if price is not None:
            row.price = float(price)
        if trigger_price is not None:
            row.trigger_price = float(trigger_price)
        if pricetype is not None:
            row.pricetype = pricetype.upper()
        db.flush()
        db.refresh(row)
        db.expunge(row)
        return row


def cancel_order(user_id: int, orderid: str) -> SandboxOrder | None:
    """Mark the order cancelled. Returns the row (with margin_blocked) so the
    caller can release funds. Returns ``None`` if the order isn't cancellable."""
    with session_scope() as db:
        row = db.execute(
            select(SandboxOrder).where(
                SandboxOrder.user_id == user_id, SandboxOrder.orderid == orderid
            )
        ).scalar_one_or_none()
        if row is None or row.status not in ("open", "trigger_pending"):
            return None
        row.status = "cancelled"
        db.flush()
        db.refresh(row)
        db.expunge(row)
        return row


def list_cancellable_orders(user_id: int) -> list[SandboxOrder]:
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxOrder).where(
                    SandboxOrder.user_id == user_id,
                    SandboxOrder.status.in_(("open", "trigger_pending")),
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            db.expunge(r)
        return list(rows)


def fill(
    user_id: int,
    orderid: str,
    fill_price: float,
    fill_quantity: int | None = None,
) -> tuple[SandboxOrder | None, SandboxTrade | None]:
    """Mark an order complete at ``fill_price``. Creates a SandboxTrade.

    Partial fills are supported by passing ``fill_quantity`` < remaining; the
    order stays ``open`` with ``filled_quantity`` updated. For phase 2a the
    engine always fills fully at LTP.
    """
    with session_scope() as db:
        row = db.execute(
            select(SandboxOrder).where(
                SandboxOrder.user_id == user_id, SandboxOrder.orderid == orderid
            )
        ).scalar_one_or_none()
        if row is None or row.status not in ("open", "trigger_pending"):
            return None, None

        qty = fill_quantity if fill_quantity is not None else (row.quantity - row.filled_quantity)
        if qty <= 0:
            return None, None

        # Running-average fill price (we don't currently do partial fills but
        # keeping the math correct for future).
        prev_qty = row.filled_quantity
        prev_avg = row.average_price
        new_total_qty = prev_qty + qty
        new_avg = (
            (prev_avg * prev_qty + fill_price * qty) / new_total_qty
            if new_total_qty > 0
            else fill_price
        )

        row.filled_quantity = new_total_qty
        row.average_price = round(new_avg, 4)
        if row.filled_quantity >= row.quantity:
            row.status = "complete"

        trade = SandboxTrade(
            user_id=user_id,
            orderid=orderid,
            tradeid=new_tradeid(),
            symbol=row.symbol,
            exchange=row.exchange,
            action=row.action,
            quantity=int(qty),
            average_price=round(fill_price, 4),
            product=row.product,
            strategy=row.strategy,
        )
        db.add(trade)
        db.flush()
        db.refresh(row)
        db.refresh(trade)
        db.expunge(row)
        db.expunge(trade)
        return row, trade


def reject_order(user_id: int, orderid: str, reason: str) -> None:
    with session_scope() as db:
        db.execute(
            update(SandboxOrder)
            .where(SandboxOrder.user_id == user_id, SandboxOrder.orderid == orderid)
            .values(status="rejected", rejection_reason=reason[:500])
        )


def to_dict(order: SandboxOrder) -> dict[str, Any]:
    """Broker-compatible orderbook row shape."""
    return {
        "orderid": order.orderid,
        "symbol": order.symbol,
        "exchange": order.exchange,
        "action": order.action,
        "order_status": order.status,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "pending_quantity": max(0, order.quantity - order.filled_quantity),
        "pricetype": order.pricetype,
        "product": order.product,
        "price": round(order.price, 2),
        "trigger_price": round(order.trigger_price, 2),
        "average_price": round(order.average_price, 2),
        "order_timestamp": (
            order.order_timestamp.strftime("%d-%b-%Y %H:%M:%S")
            if isinstance(order.order_timestamp, datetime)
            else None
        ),
        "strategy": order.strategy or "",
        "rejection_reason": order.rejection_reason or "",
    }


def trade_to_dict(trade: SandboxTrade) -> dict[str, Any]:
    return {
        "tradeid": trade.tradeid,
        "orderid": trade.orderid,
        "symbol": trade.symbol,
        "exchange": trade.exchange,
        "action": trade.action,
        "quantity": trade.quantity,
        "average_price": round(trade.average_price, 2),
        "product": trade.product,
        "strategy": trade.strategy or "",
        "trade_timestamp": (
            trade.timestamp.strftime("%d-%b-%Y %H:%M:%S")
            if isinstance(trade.timestamp, datetime)
            else None
        ),
    }
