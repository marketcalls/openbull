"""Repository layer for the strategy module.

Every read/write here filters on ``user_id`` — there is no helper that returns
a strategy without taking a user_id. Cross-tenant access raises ``NotFound``
(404 at the router layer, not 403 — never leak existence).

Higher layers (router, engine) call into this module; they never write SQL
directly.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from datetime import datetime
from typing import Sequence

from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.strategy_events import (
    LegEntryPlacedEvent,
    LegEntryRejectedEvent,
    LegExitPlacedEvent,
    RunStartedEvent,
    RunStoppedEvent,
    StrategyCreatedEvent,
    StrategyDeletedEvent,
    StrategyUpdatedEvent,
    WebhookTokenRotatedEvent,
)
from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyEvent,
    SmStrategyOrder,
    SmStrategyRun,
)
from backend.strategy.security import generate_webhook_token, hash_webhook_token
from backend.strategy.time_utils import now_utc
from backend.utils.event_bus import bus

logger = logging.getLogger(__name__)


class NotFound(Exception):
    """Raised when a record doesn't exist or belongs to another user.

    Routers map this to HTTP 404. Never differentiate "doesn't exist" from
    "not yours" — that prevents cross-tenant enumeration.
    """


class Conflict(Exception):
    """Raised on uniqueness / state-violation conflicts (mapped to 409)."""


# ---------------------------------------------------------------------------
# Strategy CRUD
# ---------------------------------------------------------------------------


async def create_strategy(
    db: AsyncSession,
    *,
    user_id: int,
    payload: dict,
) -> tuple[SmStrategy, str]:
    """Insert a new strategy. Returns (row, plaintext_webhook_token).

    The plaintext token is shown to the caller exactly once. Only the SHA-256
    hash is stored.
    """
    plaintext, token_hash = generate_webhook_token()

    row = SmStrategy(
        user_id=user_id,
        name=payload["name"].strip(),
        strategy_kind=payload.get("strategy_kind", "batch"),
        direction=payload.get("direction", "both"),
        universe_tab=payload["universe_tab"],
        underlying=payload["underlying"].strip().upper(),
        underlying_exchange=payload["underlying_exchange"].strip().upper(),
        strategy_type=payload["strategy_type"],
        entry_time=payload.get("entry_time"),
        exit_time=payload.get("exit_time"),
        product=payload.get("product", "NRML"),
        pricetype=payload.get("pricetype", "MARKET"),
        legs=payload["legs"],
        overall_sl_mtm=payload.get("overall_sl_mtm"),
        overall_target_mtm=payload.get("overall_target_mtm"),
        lock_profit=payload.get("lock_profit"),
        trail_sl_to_entry=payload.get("trail_sl_to_entry", False),
        scheduler=payload.get("scheduler"),
        live_enabled=False,
        webhook_token_hash=token_hash,
        webhook_ip_allowlist=payload.get("webhook_ip_allowlist"),
        daily_loss_limit_inr=payload.get("daily_loss_limit_inr"),
        status="stopped",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Most likely the (user_id, name) unique constraint
        if "uq_sm_strategy_user_name" in str(e.orig):
            raise Conflict("A strategy with this name already exists") from e
        raise
    await db.refresh(row)

    bus.publish(StrategyCreatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Strategy '{row.name}' created",
        payload={"underlying": row.underlying, "legs": len(row.legs)},
    ))
    await _sync_scheduler_jobs(row.id)
    return row, plaintext


async def get_strategy(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> SmStrategy:
    row = (
        await db.execute(
            select(SmStrategy).where(
                SmStrategy.id == strategy_id, SmStrategy.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound()
    return row


async def list_strategies(
    db: AsyncSession,
    *,
    user_id: int,
    status: Optional[str] = None,
    universe_tab: Optional[str] = None,
) -> Sequence[SmStrategy]:
    stmt = select(SmStrategy).where(SmStrategy.user_id == user_id)
    if status:
        stmt = stmt.where(SmStrategy.status == status)
    if universe_tab:
        stmt = stmt.where(SmStrategy.universe_tab == universe_tab)
    stmt = stmt.order_by(SmStrategy.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


async def update_strategy(
    db: AsyncSession,
    *,
    user_id: int,
    strategy_id: int,
    patch: dict[str, Any],
) -> SmStrategy:
    """Partial update. Refused (409) when status != 'stopped'."""
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    if row.status != "stopped":
        raise Conflict(f"Cannot edit a strategy that is currently '{row.status}'")

    if "underlying" in patch and patch["underlying"]:
        patch["underlying"] = patch["underlying"].strip().upper()
    if "underlying_exchange" in patch and patch["underlying_exchange"]:
        patch["underlying_exchange"] = patch["underlying_exchange"].strip().upper()
    if "name" in patch and patch["name"]:
        patch["name"] = patch["name"].strip()

    for k, v in patch.items():
        setattr(row, k, v)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "uq_sm_strategy_user_name" in str(e.orig):
            raise Conflict("A strategy with this name already exists") from e
        raise
    await db.refresh(row)

    bus.publish(StrategyUpdatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Strategy '{row.name}' updated",
        payload={"fields": list(patch.keys())},
    ))
    if "scheduler" in patch:
        await _sync_scheduler_jobs(row.id)
    return row


async def delete_strategy(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> None:
    """Hard delete. Refused (409) when status != 'stopped'."""
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    if row.status != "stopped":
        raise Conflict(f"Cannot delete a strategy that is currently '{row.status}'")
    name = row.name
    await db.delete(row)
    await db.commit()

    # Note: cascading FK deletes the audit-trail rows for this strategy. The
    # delete event below cannot be persisted (FK now invalid) — that's
    # intentional. The deletion itself is recorded in app logs.
    logger.info("user=%d deleted strategy id=%d name=%r", user_id, strategy_id, name)
    _remove_scheduler_jobs(strategy_id)


async def rotate_webhook_token(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> tuple[SmStrategy, str]:
    """Issue a fresh webhook token. Old token is invalidated immediately.

    Returns (row, new_plaintext). Plaintext is shown once and never stored.
    """
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    plaintext, token_hash = generate_webhook_token()
    row.webhook_token_hash = token_hash
    await db.commit()
    await db.refresh(row)
    bus.publish(WebhookTokenRotatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Webhook token rotated for '{row.name}'",
        severity="warn",
    ))
    logger.info(
        "user=%d rotated webhook token for strategy id=%d", user_id, strategy_id
    )
    return row, plaintext


# ---------------------------------------------------------------------------
# Scheduler hooks — keep CRUD ↔ APScheduler in lockstep without a circular
# import. Lazy-import so the scheduler module can sit above the repository
# in the dependency graph (it imports from `repo` for diagnostics later).
# ---------------------------------------------------------------------------


async def _sync_scheduler_jobs(strategy_id: int) -> None:
    """Re-install this strategy's cron jobs from the DB row. Safe no-op
    when the scheduler hasn't started yet (e.g. during test fixtures)."""
    try:
        from backend.strategy import scheduler as strategy_scheduler

        if strategy_scheduler.get_scheduler() is None:
            return
        await strategy_scheduler.sync_jobs_for_strategy(strategy_id)
    except Exception:
        logger.exception(
            "Failed to sync scheduler jobs for strategy %d", strategy_id,
        )


def _remove_scheduler_jobs(strategy_id: int) -> None:
    """Drop this strategy's cron jobs. Used on delete."""
    try:
        from backend.strategy import scheduler as strategy_scheduler

        if strategy_scheduler.get_scheduler() is None:
            return
        strategy_scheduler.remove_jobs_for_strategy(strategy_id)
    except Exception:
        logger.exception(
            "Failed to remove scheduler jobs for strategy %d", strategy_id,
        )


# Helper used by future webhook handler (Phase 9). Lives here so all
# webhook-token-aware queries go through one place.
async def find_strategy_by_webhook_token(
    db: AsyncSession, *, plaintext_token: str
) -> Optional[SmStrategy]:
    """Resolve an incoming webhook URL token to a strategy. None on miss."""
    token_hash = hash_webhook_token(plaintext_token)
    return (
        await db.execute(
            select(SmStrategy).where(SmStrategy.webhook_token_hash == token_hash)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Phase 4 — runs and orders
# ---------------------------------------------------------------------------


async def start_run(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    mode: str,
    broker: str,
    trigger_source: str,
) -> SmStrategyRun:
    """Open a new run row and flip the strategy's status to 'running'.

    Engine then places entry orders and writes ``sm_strategy_order`` rows
    via :func:`record_order` for each leg. If any of those fail, the engine
    calls :func:`finalize_run` with ``stop_reason='error'``.
    """
    run = SmStrategyRun(
        strategy_id=strategy.id,
        mode=mode,
        broker=broker,
        trigger_source=trigger_source,
    )
    db.add(run)
    await db.flush()  # populate run.id without committing yet

    strategy.status = "running"
    strategy.current_run_id = run.id
    await db.commit()
    await db.refresh(run)
    await db.refresh(strategy)

    bus.publish(RunStartedEvent(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id,
        message=f"Run started in {mode} mode (trigger: {trigger_source})",
        payload={"mode": mode, "broker": broker, "trigger_source": trigger_source},
    ))
    return run


async def finalize_run(
    db: AsyncSession,
    *,
    run: SmStrategyRun,
    strategy: SmStrategy,
    stop_reason: str,
    pnl_realized: float = 0.0,
    pnl_peak: float = 0.0,
    pnl_trough: float = 0.0,
) -> SmStrategyRun:
    """Close the run, flip the strategy back to 'stopped'."""
    run.stopped_at = now_utc()
    run.stop_reason = stop_reason
    run.pnl_realized = pnl_realized
    run.pnl_peak = pnl_peak
    run.pnl_trough = pnl_trough

    strategy.status = "stopped"
    strategy.current_run_id = None

    await db.commit()
    await db.refresh(run)
    await db.refresh(strategy)

    bus.publish(RunStoppedEvent(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id,
        severity="info" if stop_reason in ("manual", "scheduler", "eod", "expiry") else "warn",
        message=f"Run stopped — reason: {stop_reason}, realized P&L: ₹{pnl_realized:.2f}",
        payload={
            "stop_reason": stop_reason,
            "pnl_realized": pnl_realized,
        },
    ))
    return run


async def get_run(
    db: AsyncSession, *, user_id: int, run_id: int
) -> SmStrategyRun:
    """Load a run with cross-tenant ownership enforcement."""
    row = (
        await db.execute(
            select(SmStrategyRun)
            .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(SmStrategyRun.id == run_id, SmStrategy.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound()
    return row


async def list_runs(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> Sequence[SmStrategyRun]:
    rows = (
        await db.execute(
            select(SmStrategyRun)
            .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(
                SmStrategy.id == strategy_id,
                SmStrategy.user_id == user_id,
            )
            .order_by(desc(SmStrategyRun.started_at))
        )
    ).scalars().all()
    return rows


async def sum_strategy_realized(
    db: AsyncSession,
    *,
    user_id: int,
    strategy_id: int,
    exclude_run_id: Optional[int] = None,
) -> float:
    """Sum ``SmStrategyRun.pnl_realized`` across all runs of a strategy.

    Used by the positions endpoint to compute lifetime cumulative realized
    P&L. ``exclude_run_id`` lets callers leave the currently-running run
    out so they can add its in-flight order-derived realized separately —
    ``run.pnl_realized`` only gets written by :func:`finalize_run` on stop,
    so the live row's DB value is stale until the run is closed.

    Filters by ``user_id`` (same tenancy guarantee as every other reader
    in this module). Returns 0.0 when the strategy has never run.
    """
    from sqlalchemy import func
    stmt = (
        select(func.coalesce(func.sum(SmStrategyRun.pnl_realized), 0))
        .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
        .where(
            SmStrategy.id == strategy_id,
            SmStrategy.user_id == user_id,
        )
    )
    if exclude_run_id is not None:
        stmt = stmt.where(SmStrategyRun.id != exclude_run_id)
    return float((await db.execute(stmt)).scalar() or 0)


async def sum_realized_per_strategy(
    db: AsyncSession, *, user_id: int, strategy_ids: Sequence[int],
) -> dict[int, float]:
    """Batched per-strategy ``SUM(pnl_realized)`` for the list page.

    Returns ``{strategy_id: cumulative_realized}`` for every strategy_id
    in the input list (zero-filled when no runs exist). Single query,
    grouped — avoids the N+1 you'd get from calling
    :func:`sum_strategy_realized` per row.
    """
    if not strategy_ids:
        return {}
    from sqlalchemy import func
    rows = (
        await db.execute(
            select(
                SmStrategyRun.strategy_id,
                func.coalesce(func.sum(SmStrategyRun.pnl_realized), 0),
            )
            .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(
                SmStrategy.user_id == user_id,
                SmStrategyRun.strategy_id.in_(list(strategy_ids)),
            )
            .group_by(SmStrategyRun.strategy_id)
        )
    ).all()
    by_strategy = {sid: float(total or 0) for sid, total in rows}
    # Zero-fill so every strategy_id has an entry even with no runs.
    return {sid: by_strategy.get(sid, 0.0) for sid in strategy_ids}


async def record_order(
    db: AsyncSession,
    *,
    run_id: int,
    leg_id: int,
    kind: str,
    symbol: str,
    exchange: str,
    action: str,
    qty: int,
    pricetype: str,
    price: float = 0.0,
    trigger_price: float = 0.0,
    broker_order_id: Optional[str] = None,
    status: str = "pending",
    reject_reason: Optional[str] = None,
) -> SmStrategyOrder:
    """Audit-grade write of one order placed by the engine."""
    row = SmStrategyOrder(
        run_id=run_id,
        leg_id=leg_id,
        kind=kind,
        broker_order_id=broker_order_id,
        symbol=symbol,
        exchange=exchange,
        action=action,
        qty=qty,
        pricetype=pricetype,
        price=price,
        trigger_price=trigger_price,
        status=status,
        reject_reason=reject_reason,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_orders_for_run(
    db: AsyncSession, *, user_id: int, run_id: int
) -> Sequence[SmStrategyOrder]:
    """All orders for a run, ownership-filtered, oldest first."""
    rows = (
        await db.execute(
            select(SmStrategyOrder)
            .join(SmStrategyRun, SmStrategyOrder.run_id == SmStrategyRun.id)
            .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(
                SmStrategyOrder.run_id == run_id,
                SmStrategy.user_id == user_id,
            )
            .order_by(SmStrategyOrder.placed_at)
        )
    ).scalars().all()
    return rows


async def list_orders_for_strategy(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> Sequence[SmStrategyOrder]:
    """Every order across every run for a strategy, ownership-filtered."""
    rows = (
        await db.execute(
            select(SmStrategyOrder)
            .join(SmStrategyRun, SmStrategyOrder.run_id == SmStrategyRun.id)
            .join(SmStrategy, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(
                SmStrategy.id == strategy_id,
                SmStrategy.user_id == user_id,
            )
            .order_by(desc(SmStrategyOrder.placed_at))
        )
    ).scalars().all()
    return rows


async def list_events_for_strategy(
    db: AsyncSession,
    *,
    user_id: int,
    strategy_id: int,
    run_id: Optional[int] = None,
    limit: int = 200,
) -> Sequence[SmStrategyEvent]:
    """Audit trail rows, newest first. Used by the Events tab."""
    stmt = (
        select(SmStrategyEvent)
        .where(
            SmStrategyEvent.strategy_id == strategy_id,
            SmStrategyEvent.user_id == user_id,
        )
        .order_by(desc(SmStrategyEvent.ts))
        .limit(limit)
    )
    if run_id is not None:
        stmt = stmt.where(SmStrategyEvent.run_id == run_id)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


def emit_leg_entry_placed(
    *, user_id: int, strategy_id: int, run_id: int, leg_id: int,
    symbol: str, action: str, qty: int, broker_order_id: Optional[str],
) -> None:
    bus.publish(LegEntryPlacedEvent(
        user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
        message=f"Entry placed: {action} {qty} {symbol}",
        payload={
            "symbol": symbol, "action": action, "qty": qty,
            "broker_order_id": broker_order_id,
        },
    ))


def emit_leg_exit_placed(
    *, user_id: int, strategy_id: int, run_id: int, leg_id: int,
    symbol: str, action: str, qty: int, kind: str,
    broker_order_id: Optional[str],
) -> None:
    bus.publish(LegExitPlacedEvent(
        user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
        message=f"Exit placed ({kind}): {action} {qty} {symbol}",
        payload={
            "symbol": symbol, "action": action, "qty": qty,
            "exit_kind": kind, "broker_order_id": broker_order_id,
        },
    ))


def emit_leg_entry_rejected(
    *, user_id: int, strategy_id: int, run_id: Optional[int], leg_id: int,
    reason: str, payload: Optional[dict] = None,
) -> None:
    """Publish a leg_entry_rejected event - used by the engine when a
    direction filter or other pre-dispatch gate refuses a signal.

    run_id is optional because direction-blocked signals may arrive
    before any run exists for the strategy that day.
    """
    bus.publish(LegEntryRejectedEvent(
        user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
        severity="warn",
        message=f"Leg {leg_id} entry rejected: {reason}",
        payload={"reason": reason, **(payload or {})},
    ))
