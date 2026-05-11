"""Async tick processor — the engine's hot loop.

Consumes ticks dispatched by :mod:`backend.strategy.tick_feed`, evaluates
per-leg risk via :mod:`backend.strategy.risk_evaluator`, updates Redis state,
fires exit orders when rules hit, persists ``sm_strategy_event`` rows via
the event bus, and pushes WS deltas to connected UI clients.

Phase 6 scope: per-leg SL / Target / Trail SL. Phase 7 adds strategy-level
Overall SL / Target, Lock-Profit, and Trail-to-entry, hooked into the same
processor.

The processor is one async task per FastAPI worker. Concurrent ticks for
different runs are inter-leaved cooperatively; there is no per-run locking
because Phase 5 already guarantees one engine instance owns each run via
the Redis ownership lock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from backend.database import async_session
from backend.events.strategy_events import (
    LegSlHitEvent,
    LegTargetHitEvent,
    LegTrailAdvancedEvent,
    LegTrailArmedEvent,
    LockProfitArmedEvent,
    LockProfitFloorAdvancedEvent,
    LockProfitTriggeredEvent,
    OverallSlHitEvent,
    OverallTargetHitEvent,
    TrailToEntryActivatedEvent,
)
from backend.strategy import (
    broadcast,
    repository as repo,
    risk_evaluator,
    state as state_module,
    strategy_risk,
)
from backend.strategy.time_utils import format_ist, now_utc
from backend.utils.event_bus import bus

logger = logging.getLogger(__name__)


_task: Optional[asyncio.Task] = None
_queue: Optional[asyncio.Queue] = None
_running: bool = False


def get_queue() -> asyncio.Queue:
    """Return the tick queue, initializing if needed."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=10_000)
    return _queue


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start() -> asyncio.Queue:
    """Spin up the tick consumer. Returns the queue so callers can inject."""
    global _task, _running
    queue = get_queue()
    if _running:
        return queue
    _running = True
    _task = asyncio.create_task(_loop(), name="strategy-tick-processor")
    return queue


async def stop() -> None:
    global _task, _running
    _running = False
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error stopping tick processor")
    _task = None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def _loop() -> None:
    queue = get_queue()
    logger.info("Strategy tick processor started")
    try:
        while _running:
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await _process_tick(tick)
            except Exception:
                logger.exception("Tick processing failed for %s", tick)
    finally:
        logger.info("Strategy tick processor stopped")


# ---------------------------------------------------------------------------
# Tick handling
# ---------------------------------------------------------------------------


async def _process_tick(tick: dict[str, Any]) -> None:
    """One tick → fan out to every interested run, evaluate, broadcast."""
    exchange = tick["exchange"]
    symbol = tick["symbol"]
    ltp = float(tick["ltp"])
    run_ids = tick.get("run_ids") or []

    for run_id in run_ids:
        try:
            await _process_tick_for_run(run_id, exchange, symbol, ltp)
        except Exception:
            logger.exception("Tick processing failed for run %d", run_id)


async def _process_tick_for_run(
    run_id: int, exchange: str, symbol: str, ltp: float
) -> None:
    state = await state_module.get_run_state(run_id)
    if state is None:
        # Run isn't live in Redis (stopped or crashed before recovery) —
        # tick is irrelevant.
        return

    # Find every leg matching this symbol that's still open.
    matched_legs: list[tuple[str, dict]] = [
        (lid, leg) for lid, leg in state.get("legs", {}).items()
        if leg.get("status") == "open"
        and leg.get("symbol") == symbol
        and leg.get("exchange") == exchange
    ]
    if not matched_legs:
        return

    # Load the strategy row once (overall_sl_mtm / overall_target_mtm /
    # lock_profit / trail_sl_to_entry + per-leg configs) so the tick path
    # is one DB read instead of N.
    strategy_row = await _load_strategy_for_run(run_id)
    if strategy_row is None:
        return

    state_changed = False
    triggered_exits: list[tuple[int, str]] = []  # (leg_id, exit_kind)
    sl_leg_ids_this_tick: list[int] = []

    for lid, leg in matched_legs:
        entry_avg = leg.get("entry_avg")
        if entry_avg is None:
            leg["ltp"] = ltp
            state_changed = True
            continue

        leg_config = _find_leg_config(strategy_row.legs or [], int(lid))
        outcome = risk_evaluator.evaluate_leg(
            position=leg.get("position"),
            qty=int(leg.get("qty") or 0),
            entry_avg=float(entry_avg),
            ltp=ltp,
            sl_pts=leg_config.get("sl_pts"),
            target_pts=leg_config.get("target_pts"),
            trail_x=float((leg_config.get("trail") or {}).get("x") or 0),
            trail_y=float((leg_config.get("trail") or {}).get("y") or 0),
            prior_favorable_peak=float(leg.get("favorable_peak") or 0.0),
            prior_trail_active=bool(leg.get("trail_active") or False),
            prior_effective_sl=leg.get("effective_sl"),
            prior_effective_target=leg.get("effective_target"),
        )

        leg["ltp"] = ltp
        leg["mtm"] = outcome.leg_mtm
        leg["favorable_peak"] = outcome.favorable_peak
        leg["trail_active"] = outcome.trail_active
        leg["effective_sl"] = outcome.effective_sl
        leg["effective_target"] = outcome.effective_target
        state_changed = True

        if outcome.triggered:
            await _publish_risk_event(
                run_id, int(lid), outcome, leg, state["run_id"], symbol, ltp,
            )
            if outcome.triggered == "sl":
                triggered_exits.append((int(lid), "exit_sl"))
                sl_leg_ids_this_tick.append(int(lid))
            elif outcome.triggered == "target":
                triggered_exits.append((int(lid), "exit_target"))

    # ---- Cross-cutting: Trail-SL-to-entry ----
    # When a leg's SL fires AND the strategy has trail_sl_to_entry enabled,
    # snap every other open leg's effective SL to its entry price. Overall
    # SL is bypassed for the rest of the run.
    if (
        sl_leg_ids_this_tick
        and strategy_row.trail_sl_to_entry
        and not state.get("trail_to_entry_active")
    ):
        moved_total = 0
        for sl_leg_id in sl_leg_ids_this_tick:
            moved_total += strategy_risk.apply_trail_to_entry(
                state.get("legs") or {}, sl_leg_id,
            )
        if moved_total > 0:
            state["trail_to_entry_active"] = True
            triggering = sl_leg_ids_this_tick[0]
            bus.publish(TrailToEntryActivatedEvent(
                user_id=strategy_row.user_id,
                strategy_id=strategy_row.id,
                run_id=run_id,
                leg_id=triggering,
                severity="warn",
                message=(
                    f"Trail-SL-to-entry activated by leg {triggering} SL hit; "
                    f"moved {moved_total} leg(s) to entry. Overall SL bypassed."
                ),
                payload={"trigger_leg_id": triggering, "moved_legs": moved_total},
            ))

    # Re-aggregate strategy-level P&L now that legs updated.
    realized, unrealized, total = risk_evaluator.compute_strategy_mtm(state["legs"])
    state["pnl_realized"] = realized
    state["pnl_unrealized"] = unrealized
    state["pnl_total"] = total

    # ---- Strategy-level rule layer (lock-profit + overall SL/target) ----
    outcome = strategy_risk.evaluate_strategy(
        pnl_realized=realized,
        pnl_unrealized=unrealized,
        prior_pnl_peak=float(state.get("pnl_peak") or 0.0),
        prior_pnl_trough=float(state.get("pnl_trough") or 0.0),
        lock_armed=bool(state.get("lock_armed") or False),
        lock_floor=state.get("lock_floor"),
        trail_to_entry_active=bool(state.get("trail_to_entry_active") or False),
        overall_sl_mtm=float(strategy_row.overall_sl_mtm) if strategy_row.overall_sl_mtm is not None else None,
        overall_target_mtm=float(strategy_row.overall_target_mtm) if strategy_row.overall_target_mtm is not None else None,
        lock_profit_cfg=strategy_row.lock_profit,
    )
    state["pnl_peak"] = outcome.pnl_peak
    state["pnl_trough"] = outcome.pnl_trough
    state["lock_armed"] = outcome.lock_armed
    state["lock_floor"] = outcome.lock_floor

    for ev in outcome.events:
        _publish_strategy_event(
            user_id=strategy_row.user_id,
            strategy_id=strategy_row.id,
            run_id=run_id,
            event=ev,
        )

    if state_changed or outcome.events:
        await state_module.hydrate_run_state(run_id, state)
        _broadcast_delta(run_id, state, exchange, symbol, ltp)

    # ---- Dispatch exits ----
    # Per-leg first (preserves audit ordering — leg event before run stop).
    for leg_id, exit_kind in triggered_exits:
        await _trigger_exit(run_id, leg_id, exit_kind)

    # If a strategy-level rule triggered, close every still-open leg via
    # engine.stop_run and finalize the run with the right stop_reason.
    if outcome.stop_reason:
        await _trigger_run_stop(run_id, outcome.stop_reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_strategy_for_run(run_id: int):
    """One DB read per tick — returns the full SmStrategy row.

    Replaces the per-leg lookup in Phase 6. Phase 7+ may cache this in
    Redis with a short TTL if the per-tick cost surfaces in profiling.
    """
    from sqlalchemy import select
    from backend.models.strategy_module import SmStrategy, SmStrategyRun

    async with async_session() as db:
        stmt = (
            select(SmStrategy)
            .join(SmStrategyRun, SmStrategyRun.strategy_id == SmStrategy.id)
            .where(SmStrategyRun.id == run_id)
        )
        return (await db.execute(stmt)).scalar_one_or_none()


def _find_leg_config(legs: list[dict[str, Any]], leg_id: int) -> dict[str, Any]:
    """Find one leg's config in the strategy.legs jsonb array."""
    for leg in legs:
        if int(leg.get("id", -1)) == leg_id:
            return leg
    return {}


def _publish_strategy_event(
    *, user_id: int, strategy_id: int, run_id: int, event: dict[str, Any],
) -> None:
    """Translate a strategy_risk outcome event to a typed bus event."""
    kind = event["kind"]
    common = {
        "user_id": user_id,
        "strategy_id": strategy_id,
        "run_id": run_id,
        "severity": event.get("severity") or "info",
        "message": event.get("message") or "",
        "payload": event.get("payload") or {},
    }
    if kind == "lock_profit_armed":
        bus.publish(LockProfitArmedEvent(**common))
    elif kind == "lock_profit_floor_advanced":
        bus.publish(LockProfitFloorAdvancedEvent(**common))
    elif kind == "lock_profit_triggered":
        bus.publish(LockProfitTriggeredEvent(**common))
    elif kind == "overall_sl_hit":
        bus.publish(OverallSlHitEvent(**common))
    elif kind == "overall_target_hit":
        bus.publish(OverallTargetHitEvent(**common))
    else:
        logger.warning("Unknown strategy-risk event kind: %s", kind)


async def _publish_risk_event(
    run_id: int, leg_id: int, outcome: risk_evaluator.RiskOutcome,
    leg: dict[str, Any], strategy_id_ref: int, symbol: str, ltp: float,
) -> None:
    """Persist + broadcast the risk event. The audit subscriber writes to DB."""
    # We need user_id + strategy_id for the event. Pulled from the run row.
    from sqlalchemy import select
    from backend.models.strategy_module import SmStrategy, SmStrategyRun

    async with async_session() as db:
        run = await db.get(SmStrategyRun, run_id)
        if run is None:
            return
        strategy = await db.get(SmStrategy, run.strategy_id)
        if strategy is None:
            return
        user_id = strategy.user_id
        strategy_id = strategy.id

    payload = {
        "symbol": symbol,
        "ltp_at_trigger": ltp,
        "entry_avg": leg.get("entry_avg"),
        "leg_mtm_at_trigger": outcome.leg_mtm,
        "effective_sl": outcome.effective_sl,
        "effective_target": outcome.effective_target,
    }
    kind = outcome.triggered
    if kind == "sl":
        msg = f"SL hit on leg {leg_id} ({symbol}) at {ltp:.2f}"
        bus.publish(LegSlHitEvent(
            user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
            severity="warn", message=msg, payload=payload,
        ))
    elif kind == "target":
        msg = f"Target hit on leg {leg_id} ({symbol}) at {ltp:.2f}"
        bus.publish(LegTargetHitEvent(
            user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
            severity="info", message=msg, payload=payload,
        ))
    elif kind == "trail_armed":
        msg = f"Trail SL armed on leg {leg_id} ({symbol}) at peak={outcome.favorable_peak:.2f}"
        bus.publish(LegTrailArmedEvent(
            user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
            severity="info", message=msg, payload=payload,
        ))
    elif kind == "trail_advanced":
        msg = f"Trail SL advanced on leg {leg_id} → {outcome.effective_sl:.2f}"
        bus.publish(LegTrailAdvancedEvent(
            user_id=user_id, strategy_id=strategy_id, run_id=run_id, leg_id=leg_id,
            severity="info", message=msg, payload=payload,
        ))


def _broadcast_delta(
    run_id: int, state: dict[str, Any], exchange: str, symbol: str, ltp: float,
) -> None:
    """Push a small delta frame to connected UI clients."""
    strategy_id = state.get("strategy_id")
    if strategy_id is None:
        return
    legs_payload = []
    for lid, leg in state.get("legs", {}).items():
        if leg.get("symbol") == symbol and leg.get("exchange") == exchange:
            legs_payload.append({
                "leg_id": int(lid),
                "ltp": leg.get("ltp"),
                "mtm": leg.get("mtm"),
                "effective_sl": leg.get("effective_sl"),
                "effective_target": leg.get("effective_target"),
                "trail_active": leg.get("trail_active"),
                "favorable_peak": leg.get("favorable_peak"),
                "status": leg.get("status"),
            })
    broadcast.push_delta(int(strategy_id), {
        "type": "delta",
        "ts_ist": format_ist(now_utc()),
        "ts_ms_utc": int(now_utc().timestamp() * 1000),
        "mtm_realized": state.get("pnl_realized"),
        "mtm_unrealized": state.get("pnl_unrealized"),
        "mtm_total": state.get("pnl_total"),
        "peak": state.get("pnl_peak"),
        "trough": state.get("pnl_trough"),
        "legs": legs_payload,
    })


async def _trigger_exit(run_id: int, leg_id: int, exit_kind: str) -> None:
    """Engine fires an exit when a leg's rule hits."""
    from backend.strategy import engine

    from backend.models.strategy_module import SmStrategy, SmStrategyRun

    async with async_session() as db:
        run = await db.get(SmStrategyRun, run_id)
        if run is None or run.stopped_at is not None:
            return
        strategy = await db.get(SmStrategy, run.strategy_id)
        if strategy is None:
            return
        try:
            # Live mode would need broker auth here; rule-driven exits in
            # Phase 6 are sandbox-only until Phase 10. Pass empty broker
            # context for sandbox.
            await engine._exit_legs(  # noqa: SLF001 — engine-internal call
                db, strategy=strategy, run=run, leg_ids=[leg_id],
                exit_kind=exit_kind,
                auth_token=None, broker=run.broker, config=None,
            )
        except Exception:
            logger.exception("Auto-exit failed for run %d leg %d", run_id, leg_id)


async def _trigger_run_stop(run_id: int, stop_reason: str) -> None:
    """Strategy-level rule fired — square off remaining legs and finalize.

    Routes through ``engine.stop_run`` so the lifecycle bookkeeping (state
    cleanup, tick-feed unsubscribe, finalize_run event) is identical to a
    manual /stop. Already-closed legs are skipped by the Phase 4
    'already closed' guard in ``engine._exit_legs``.
    """
    from backend.strategy import engine

    from backend.models.strategy_module import SmStrategy

    async with async_session() as db:
        # Resolve strategy via the run row (mirrors stop_run's own lookup).
        from backend.models.strategy_module import SmStrategyRun
        run = await db.get(SmStrategyRun, run_id)
        if run is None or run.stopped_at is not None:
            return
        strategy = await db.get(SmStrategy, run.strategy_id)
        if strategy is None or strategy.status != "running":
            return
        try:
            await engine.stop_run(
                db,
                strategy=strategy,
                stop_reason=stop_reason,
                auth_token=None,
                broker=run.broker,
                config=None,
            )
        except Exception:
            logger.exception(
                "Auto-stop_run failed for run %d (reason=%s)", run_id, stop_reason,
            )
