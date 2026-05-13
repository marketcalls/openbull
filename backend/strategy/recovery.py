"""Boot-time recovery for active strategy runs.

Called once from the FastAPI lifespan after ``Base.metadata.create_all``.

For each run with ``stopped_at IS NULL`` we:

1. Load the strategy + leg config.
2. Load the run's order audit trail (``sm_strategy_order``).
3. Reconcile order statuses against the broker / sandbox so the engine
   sees the broker's truth, not whatever was recorded just before the
   crash.
4. Load the latest ``sm_strategy_checkpoint`` (if any) — restores
   peak / trough / lock_floor / trail_to_entry_active.
5. Rebuild Redis state in one write.
6. (Phase 6+) Re-subscribe to ticks and resume the tick loop. Phase 5
   stops here because the engine has no tick consumer yet.

If any step fails for a given run, the run is marked
``stop_reason='recovery_failed'``, ``strategy.status='stopped'`` so the
operator can investigate without the run wedging the engine on every boot.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyCheckpoint,
    SmStrategyOrder,
    SmStrategyRun,
)
from backend.strategy import repository as repo, state as state_module, tick_feed
from backend.strategy.time_utils import now_utc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Order reconciliation
# ---------------------------------------------------------------------------


def _reconcile_sandbox_order(
    user_id: int, db_order: SmStrategyOrder
) -> dict[str, Any] | None:
    """Look up the sandbox order by orderid and return its current truth.

    Returns ``None`` if the order doesn't exist in the sandbox book (which
    typically means the crash happened *before* the sandbox row was
    persisted — leave the DB row as-is).
    """
    if not db_order.broker_order_id:
        return None
    try:
        from backend.sandbox import order_manager
    except ImportError:
        return None

    try:
        sandbox_row = order_manager.get_order(user_id, db_order.broker_order_id)
    except Exception:
        logger.exception(
            "Sandbox reconciliation lookup failed for order %s", db_order.broker_order_id,
        )
        return None
    if sandbox_row is None:
        return None
    return {
        "status": getattr(sandbox_row, "order_status", None)
            or getattr(sandbox_row, "status", None),
        "avg_fill_price": getattr(sandbox_row, "average_price", None)
            or getattr(sandbox_row, "avg_fill_price", None),
        "filled_qty": getattr(sandbox_row, "filled_quantity", None)
            or getattr(sandbox_row, "filled_qty", None),
    }


def _reconcile_live_order(
    db_order: SmStrategyOrder, broker: str, auth_token: str,
) -> dict[str, Any] | None:
    """Live-mode reconciliation — query broker orderbook for our orderid.

    Phase 5 ships a best-effort lookup that's wired but not exercised
    until Phase 10. The broker-side endpoint name varies; we use the
    generic ``get_order_book`` helper that every adapter exposes.
    """
    if not db_order.broker_order_id:
        return None
    try:
        import importlib
        api = importlib.import_module(f"backend.broker.{broker}.api.order_api")
        get_book = getattr(api, "get_order_book", None)
        if get_book is None:
            return None
        ok, response = get_book(auth_token)
        if not ok:
            return None
        rows = response.get("data") if isinstance(response, dict) else None
        if not rows:
            return None
        for row in rows:
            if str(row.get("orderid")) == str(db_order.broker_order_id):
                return {
                    "status": row.get("status") or row.get("order_status"),
                    "avg_fill_price": row.get("average_price")
                        or row.get("avg_fill_price"),
                    "filled_qty": row.get("filled_quantity")
                        or row.get("filled_qty"),
                }
        return None
    except Exception:
        logger.exception("Live reconciliation failed for order %s on %s",
                         db_order.broker_order_id, broker)
        return None


def _normalize_status(broker_status: str | None) -> str | None:
    """Map broker-flavored statuses to our enum (`open`/`complete`/`rejected`/`cancelled`)."""
    if not broker_status:
        return None
    s = broker_status.strip().lower()
    if s in ("complete", "completed", "filled", "executed"):
        return "complete"
    if s in ("rejected", "reject"):
        return "rejected"
    if s in ("cancelled", "canceled"):
        return "cancelled"
    if s in ("pending", "open", "trigger pending", "trigger_pending", "modified"):
        return "open"
    return None  # unknown — leave the DB column alone


async def _reconcile_run_orders(
    db: AsyncSession, *, run: SmStrategyRun, strategy: SmStrategy,
) -> None:
    """Walk the run's orders and reconcile each one with the broker / sandbox."""
    orders = (await db.execute(
        select(SmStrategyOrder).where(SmStrategyOrder.run_id == run.id)
    )).scalars().all()

    for order in orders:
        if order.status in ("complete", "rejected", "cancelled"):
            # Terminal states — nothing to reconcile.
            continue
        if run.mode == "sandbox":
            truth = _reconcile_sandbox_order(strategy.user_id, order)
        elif run.mode == "live":
            # Live mode needs auth context; recovery doesn't have a session
            # token. We skip live reconciliation at boot — the engine will
            # re-fetch when the user opens the strategy detail page.
            truth = None
        else:
            truth = None

        if not truth:
            continue
        new_status = _normalize_status(truth.get("status"))
        if new_status and new_status != order.status:
            order.status = new_status
        if truth.get("avg_fill_price") is not None:
            order.avg_fill_price = float(truth["avg_fill_price"])
        if truth.get("filled_qty") is not None:
            try:
                order.filled_qty = int(truth["filled_qty"])
            except (TypeError, ValueError):
                pass
        if order.status == "complete" and order.filled_at is None:
            order.filled_at = now_utc()

    await db.commit()


# ---------------------------------------------------------------------------
# State rebuild from DB + checkpoint
# ---------------------------------------------------------------------------


async def _load_latest_checkpoint(
    db: AsyncSession, run_id: int,
) -> SmStrategyCheckpoint | None:
    return (await db.execute(
        select(SmStrategyCheckpoint)
        .where(SmStrategyCheckpoint.run_id == run_id)
        .order_by(desc(SmStrategyCheckpoint.ts))
        .limit(1)
    )).scalar_one_or_none()


def _state_from_db_and_checkpoint(
    *, strategy: SmStrategy, run: SmStrategyRun,
    orders: list[SmStrategyOrder],
    checkpoint: SmStrategyCheckpoint | None,
) -> dict[str, Any]:
    """Reconstruct Redis state from the post-reconcile DB rows + checkpoint."""
    entry_by_leg: dict[int, SmStrategyOrder] = {}
    last_exit_by_leg: dict[int, SmStrategyOrder] = {}
    for o in orders:
        if o.kind == "entry":
            entry_by_leg.setdefault(o.leg_id, o)
        elif o.status != "rejected":
            last_exit_by_leg[o.leg_id] = o

    legs: dict[str, dict[str, Any]] = {}
    for leg in (strategy.legs or []):
        leg_id = int(leg["id"])
        entry = entry_by_leg.get(leg_id)
        exit_o = last_exit_by_leg.get(leg_id)
        status = (
            "rejected" if entry and entry.status == "rejected"
            else "closed" if exit_o
            else "open" if entry
            else "configured"
        )
        legs[str(leg_id)] = {
            "leg_id": leg_id,
            "position": leg.get("position"),
            "lots": leg.get("lots"),
            "symbol": entry.symbol if entry else None,
            "exchange": entry.exchange if entry else None,
            "qty": entry.qty if entry else None,
            "entry_order_id": entry.id if entry else None,
            "entry_status": entry.status if entry else "configured",
            "entry_avg": float(entry.avg_fill_price) if entry and entry.avg_fill_price is not None else None,
            "ltp": None,
            "mtm": 0.0,
            "status": status,
            "exit_order_id": exit_o.id if exit_o else None,
            "exit_kind": exit_o.kind if exit_o else None,
            "effective_sl": None,
            "effective_target": None,
            "trail_active": False,
            "favorable_peak": 0.0,
        }

    return {
        "run_id": run.id,
        "strategy_id": strategy.id,
        "pnl_realized": float(checkpoint.pnl_realized) if checkpoint else 0.0,
        "pnl_unrealized": float(checkpoint.pnl_unrealized) if checkpoint else 0.0,
        "pnl_total": float(checkpoint.pnl_total) if checkpoint else 0.0,
        "pnl_peak": float(checkpoint.pnl_peak) if checkpoint else 0.0,
        "pnl_trough": float(checkpoint.pnl_trough) if checkpoint else 0.0,
        "lock_armed": bool(checkpoint and checkpoint.lock_floor is not None),
        "lock_floor": float(checkpoint.lock_floor) if checkpoint and checkpoint.lock_floor is not None else None,
        "trail_to_entry_active": bool(checkpoint.trail_to_entry_active) if checkpoint else False,
        "legs": legs,
    }


# ---------------------------------------------------------------------------
# Recovery loop
# ---------------------------------------------------------------------------


async def _recover_run(db: AsyncSession, run: SmStrategyRun) -> None:
    strategy = (await db.execute(
        select(SmStrategy).where(SmStrategy.id == run.strategy_id)
    )).scalar_one_or_none()
    if strategy is None:
        raise RuntimeError(f"Run {run.id}'s strategy is missing")

    await _reconcile_run_orders(db, run=run, strategy=strategy)

    orders = list((await db.execute(
        select(SmStrategyOrder).where(SmStrategyOrder.run_id == run.id)
    )).scalars().all())
    checkpoint = await _load_latest_checkpoint(db, run.id)

    state = _state_from_db_and_checkpoint(
        strategy=strategy, run=run, orders=orders, checkpoint=checkpoint,
    )
    await state_module.hydrate_run_state(run.id, state)

    # Plan section 5.4 step 3.h: "Re-subscribe to ZMQ ticks for each open
    # leg." Without this the local tick-feed interest index stays empty
    # after a restart and the tick processor will silently ignore every
    # tick for this run's symbols (SL/Target/Trail never fire). Only open
    # legs need ticks; rejected/closed legs are terminal.
    open_leg_symbols: list[tuple[str, str]] = []
    for leg in state.get("legs", {}).values():
        if leg.get("status") != "open":
            continue
        sym = leg.get("symbol")
        exch = leg.get("exchange")
        if sym and exch:
            open_leg_symbols.append((exch, sym))
    if open_leg_symbols:
        try:
            tick_feed.add_run_subscriptions(run.id, open_leg_symbols)
        except Exception:
            logger.exception(
                "Recovery: failed to re-register tick subscriptions for run %d",
                run.id,
            )

    logger.info(
        "Recovered run %d (strategy %d, mode=%s) - %d legs hydrated, "
        "%d open legs re-subscribed (checkpoint=%s)",
        run.id, strategy.id, run.mode, len(state["legs"]),
        len(open_leg_symbols),
        "yes" if checkpoint else "no",
    )


async def recover_all() -> None:
    """FastAPI startup entry point. Logs result counts and never raises."""
    try:
        async with async_session() as db:
            running_runs = list((await db.execute(
                select(SmStrategyRun).where(SmStrategyRun.stopped_at == None)  # noqa: E711
            )).scalars().all())
    except Exception:
        logger.exception("Strategy recovery: failed to enumerate running runs")
        return

    if not running_runs:
        logger.info("Strategy recovery: no active runs to recover")
        return

    recovered = 0
    failed = 0
    for run in running_runs:
        async with async_session() as db:
            try:
                # Re-load the run on this session so mutations bind correctly.
                run_fresh = await db.get(SmStrategyRun, run.id)
                if run_fresh is None or run_fresh.stopped_at is not None:
                    continue
                await _recover_run(db, run_fresh)
                recovered += 1
            except Exception:
                logger.exception("Strategy recovery: run %d failed; marking stopped", run.id)
                failed += 1
                try:
                    run_fresh = await db.get(SmStrategyRun, run.id)
                    strategy = await db.get(SmStrategy, run.strategy_id)
                    if run_fresh and run_fresh.stopped_at is None and strategy:
                        await repo.finalize_run(
                            db, run=run_fresh, strategy=strategy,
                            stop_reason="recovery_failed",
                        )
                except Exception:
                    logger.exception("Strategy recovery: failed to mark run %d as stopped", run.id)

    logger.info(
        "Strategy recovery: %d recovered, %d failed (of %d running)",
        recovered, failed, len(running_runs),
    )
