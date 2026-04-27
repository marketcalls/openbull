"""
Sandbox capital / margin book.

Margin model matches openalgo's sandbox exactly:

* ``block_margin(user, amount)`` — moves cash from *available* to *used* when
  an order is placed. Position-aware reduction (e.g. selling part of a long)
  is decided by the caller; this layer just moves whatever amount it's told.
* ``release_margin(user, amount, realized_pnl=0)`` — reverse of the block, with
  an optional realized-PnL credit. The two updates happen in a single
  transaction so the funds row never goes inconsistent (used_margin without
  the matching available bump, etc).
* ``apply_realized_pnl(user, amount)`` — credit / debit only. Used when
  realized PnL needs to be booked without a margin movement (rare).
* ``set_unrealized_pnl(user, amount)`` — display-only, set by the MTM tick.

Margin required for a fresh order = ``(price * quantity) / leverage[product]``.
Once the order fills, the margin transfers to ``SandboxPosition.margin_blocked``
(see :mod:`backend.sandbox.position_manager`); it is *not* released back to
*available* until the position is reduced or closed.

Per-user ``threading.RLock`` (acquired via the ``_user_lock`` context
manager) guards every fund mutation so two concurrent fills on the same user
can't race past one another's read of *available*. Locks are always released
on exit — including when ``session_scope`` rolls back on exception — because
the context manager pattern uses ``try / finally`` semantics.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.sandbox import SandboxFund
from backend.sandbox._db import session_scope
from backend.sandbox.config import get_leverage, get_leverage_for, get_starting_capital

logger = logging.getLogger(__name__)


# Per-user reentrant locks. Two ticks for the same user (or a tick racing a
# manual cancel) must serialize so available / used_margin updates don't
# clobber one another, but two *different* users have no contention.
#
# RLock instead of Lock: lets a path that already owns the lock call another
# fund-manager helper without self-deadlocking — defensive for future edits
# even though current call sites don't nest. ``with _user_lock(uid):`` always
# releases on exit (success or exception) thanks to the context manager.
_locks_registry_lock = threading.Lock()
_user_locks: dict[int, threading.RLock] = {}


def _get_user_lock(user_id: int) -> threading.RLock:
    lock = _user_locks.get(user_id)
    if lock is not None:
        return lock
    with _locks_registry_lock:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _user_locks[user_id] = lock
        return lock


@contextmanager
def _user_lock(user_id: int) -> Iterator[None]:
    """Acquire-and-release the per-user fund mutation lock. Always released
    on exit (including via exception), so a session_scope rollback inside
    cannot leave the lock held."""
    lock = _get_user_lock(user_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


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
    db: Session,
    price: float,
    quantity: int,
    product: str,
    *,
    exchange: str = "",
    instrument_type: str = "",
    action: str = "",
) -> float:
    """Return margin required to hold this order open.

    When ``exchange``/``instrument_type``/``action`` are provided we use the
    instrument-aware leverage map (option BUY vs SELL, futures, equity MIS vs
    CNC). Without them we fall back to the legacy product-only map — kept so
    older callers (e.g. weekly_reset reconciling capital) keep working with
    no shape change.
    """
    if price <= 0 or quantity <= 0:
        return 0.0
    if exchange or instrument_type or action:
        leverage = get_leverage_for(
            exchange=exchange,
            product=product,
            instrument_type=instrument_type,
            action=action,
            db=db,
        )
    else:
        leverage = get_leverage(product, db)
    if leverage <= 0:
        leverage = 1.0
    return round((price * quantity) / leverage, 2)


def block_margin(user_id: int, amount: float) -> tuple[bool, str]:
    """Move ``amount`` from available → used. Fails if available is insufficient."""
    if amount <= 0:
        return True, ""
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        if row.available < amount:
            return False, (
                f"Insufficient sandbox funds: required INR {amount:,.2f}, "
                f"available INR {row.available:,.2f}"
            )
        row.available -= amount
        row.used_margin += amount
    return True, ""


def release_margin(user_id: int, amount: float, realized_pnl: float = 0.0) -> None:
    """Move ``amount`` from used → available, optionally crediting realized PnL.

    Both mutations happen inside one transaction. Pass ``realized_pnl=0`` for a
    pure release (e.g. order cancel); pass a non-zero value when the release is
    triggered by a position reduce/close so the booked PnL hits the cash row in
    the same step the margin is freed.
    """
    if amount <= 0 and realized_pnl == 0:
        return
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        if amount > 0:
            row.used_margin = max(0.0, row.used_margin - amount)
            row.available += amount
        if realized_pnl != 0:
            row.available += realized_pnl
            row.realized_pnl += realized_pnl
            row.today_realized_pnl += realized_pnl


def transfer_margin_to_holdings(user_id: int, amount: float) -> None:
    """Reduce ``used_margin`` by ``amount`` without crediting *available*.

    Called when a long CNC position settles into holdings: the cash that was
    locked as margin is now embodied in the share itself, so the margin lock
    drops but no buying power is freed. Mirrors openalgo's
    ``transfer_margin_to_holdings`` behaviour (used_margin shrinks, asset
    value moves into the holdings book separately).
    """
    if amount <= 0:
        return
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.used_margin = max(0.0, row.used_margin - amount)


def credit_sale_proceeds(user_id: int, amount: float) -> None:
    """Credit ``amount`` to ``available`` when settled holdings are sold.

    Mirrors openalgo's ``credit_sale_proceeds`` — sale proceeds flow back
    into buying power without touching the realized-PnL bucket (that's
    handled by ``release_margin``/``apply_realized_pnl`` for the trade)."""
    if amount <= 0:
        return
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.available += amount


def apply_realized_pnl(user_id: int, amount: float) -> None:
    """Credit (+) / debit (-) realized PnL into ``available`` and the PnL buckets.

    Use :func:`release_margin` instead when realized PnL accompanies a margin
    release — it does both atomically in one transaction.
    """
    if amount == 0:
        return
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.realized_pnl += amount
        row.today_realized_pnl += amount
        row.available += amount


def set_unrealized_pnl(user_id: int, amount: float) -> None:
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.unrealized_pnl = amount


def reset_today_realized_pnl(user_id: int) -> None:
    """Daily session boundary — clear today's realized PnL bucket. Cumulative
    ``realized_pnl`` is preserved."""
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        row.today_realized_pnl = 0.0


def reset_funds(user_id: int) -> None:
    """Wipe PnL, reset available to starting_capital. Used by the weekly
    full-wipe and the per-user reset button."""
    with _user_lock(user_id), session_scope() as db:
        row = ensure_fund_row(db, user_id)
        capital = get_starting_capital(db)
        row.starting_capital = capital
        row.available = capital
        row.used_margin = 0.0
        row.realized_pnl = 0.0
        row.today_realized_pnl = 0.0
        row.unrealized_pnl = 0.0


def reconcile_margin(user_id: int, auto_fix: bool = True) -> tuple[bool, float, dict]:
    """Compare ``fund.used_margin`` against the sum of every open position's
    ``margin_blocked`` and (optionally) auto-fix any drift.

    Returns ``(consistent, discrepancy, details)`` — ``discrepancy`` is the
    signed amount that ``used_margin`` is *over* the position-side sum (so
    positive means the funds row has more locked than the positions can
    account for).

    With ``auto_fix=True`` and a positive discrepancy we release the orphan
    margin to *available* (matches openalgo's behaviour: stuck margin is
    treated as a leak and freed). A negative discrepancy is *only* logged —
    we don't silently grow ``used_margin`` to match positions, because that
    would hide a real bug somewhere else.
    """
    from backend.models.sandbox import SandboxPosition

    with _user_lock(user_id), session_scope() as db:
        funds = ensure_fund_row(db, user_id)
        positions = (
            db.execute(
                select(SandboxPosition).where(
                    SandboxPosition.user_id == user_id,
                    SandboxPosition.net_quantity != 0,
                )
            )
            .scalars()
            .all()
        )
        position_total = round(
            sum(float(p.margin_blocked or 0.0) for p in positions), 2
        )
        used = round(float(funds.used_margin), 2)
        discrepancy = round(used - position_total, 2)
        details = {
            "user_id": user_id,
            "used_margin": used,
            "position_margin_total": position_total,
            "discrepancy": discrepancy,
            "auto_fixed": False,
        }
        if discrepancy == 0:
            return True, 0.0, details
        if auto_fix and discrepancy > 0:
            funds.used_margin = position_total
            funds.available += discrepancy
            details["auto_fixed"] = True
            logger.warning(
                "sandbox: reconcile released INR %.2f stuck margin for user %d",
                discrepancy, user_id,
            )
            return True, 0.0, details
        return False, discrepancy, details


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
