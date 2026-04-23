"""
Sandbox capital / margin book.

Every user gets one ``sandbox_funds`` row (auto-created on first access). The
margin model is deliberately simple:

* ``block_margin(user, amount)`` moves money from *available* to *used* when
  an order is placed.
* ``release_margin(user, amount)`` does the reverse on cancel / partial fill.
* ``apply_realized_pnl(user, amount)`` credits / debits the realized PnL
  bucket and ``available``.

Margin required for an order = ``(price * quantity) / leverage[product]``.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.sandbox import SandboxFund
from backend.sandbox._db import session_scope
from backend.sandbox.config import get_leverage, get_starting_capital

logger = logging.getLogger(__name__)


def ensure_fund_row(db: Session, user_id: int) -> SandboxFund:
    row = db.execute(
        select(SandboxFund).where(SandboxFund.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        capital = get_starting_capital(db)
        row = SandboxFund(
            user_id=user_id,
            starting_capital=capital,
            available=capital,
        )
        db.add(row)
        db.flush()
    return row


def compute_required_margin(
    db: Session, price: float, quantity: int, product: str
) -> float:
    """Return margin required to hold this order open. Uses leverage table."""
    if price <= 0 or quantity <= 0:
        return 0.0
    leverage = get_leverage(product, db)
    if leverage <= 0:
        leverage = 1.0
    return round((price * quantity) / leverage, 2)


def block_margin(user_id: int, amount: float) -> tuple[bool, str]:
    """Move ``amount`` from available → used. Fails if available is insufficient."""
    if amount <= 0:
        return True, ""
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        if row.available < amount:
            return False, (
                f"Insufficient sandbox funds: required ₹{amount:,.2f}, "
                f"available ₹{row.available:,.2f}"
            )
        row.available -= amount
        row.used_margin += amount
    return True, ""


def release_margin(user_id: int, amount: float) -> None:
    """Reverse of block_margin. Safe to call with ``0``."""
    if amount <= 0:
        return
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.used_margin = max(0.0, row.used_margin - amount)
        row.available += amount


def apply_realized_pnl(user_id: int, amount: float) -> None:
    """Credit (+) / debit (-) the realized-PnL bucket and ``available``."""
    if amount == 0:
        return
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.realized_pnl += amount
        row.available += amount


def set_unrealized_pnl(user_id: int, amount: float) -> None:
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.unrealized_pnl = amount


def reset_funds(user_id: int) -> None:
    """Wipe PnL, reset available to starting_capital. For the Reset button (phase 2b)."""
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        capital = get_starting_capital(db)
        row.starting_capital = capital
        row.available = capital
        row.used_margin = 0.0
        row.realized_pnl = 0.0
        row.unrealized_pnl = 0.0


def get_funds_snapshot(user_id: int) -> dict:
    """Broker-compatible funds payload — shape matches Zerodha/Upstox mapping."""
    with session_scope() as db:
        row = ensure_fund_row(db, user_id)
        return {
            "availablecash": round(row.available, 2),
            "utiliseddebits": round(row.used_margin, 2),
            "collateral": 0.0,
            "m2munrealized": round(row.unrealized_pnl, 2),
            "m2mrealized": round(row.realized_pnl, 2),
        }
