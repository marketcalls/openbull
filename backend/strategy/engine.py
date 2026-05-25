"""Strategy engine — Phase 4 skeleton.

Phase 4 scope:
    * start_run        — resolve every leg, place entry orders, open run row
    * stop_run         — exit every still-open leg, finalize run
    * close_leg        — exit one leg only; run keeps going
    * close_all        — alias for stop_run when invoked from "Close All" UI

Out of scope (later phases):
    * Tick subscription + risk evaluation (Phase 6 — SL/Target/Trail)
    * Strategy-level risk: Overall SL/Target, Lock-Profit (Phase 7)
    * Crash-safe recovery (Phase 5)
    * Live mode is wired but is intentionally not the default — Phase 10
      is when we exercise the live path end-to-end. Sandbox runs work today.

Engine is **stateless** in Phase 4 — every call reads what it needs from
the DB. Phase 5 introduces Redis state for tick-loop hot data; Phase 6
adds the tick subscriber that drives risk eval.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.strategy_events import (
    KillSwitchActivatedEvent,
    WebhookUnlockedEvent,
)
from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyOrder,
    SmStrategyRun,
)
from backend.utils.event_bus import bus
from backend.strategy import (
    repository as repo, state as state_module, symbol_resolver, tick_feed,
)
from backend.strategy.order_dispatch import dispatch_order

logger = logging.getLogger(__name__)


class EngineError(Exception):
    """Engine-side failure (resolution, dispatch, fill timeout, ...)."""


# ---------------------------------------------------------------------------
# Internals — leg resolution
# ---------------------------------------------------------------------------


def _resolve_leg(
    leg: dict[str, Any],
    *,
    underlying: str,
    underlying_exchange: str,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
    expiry_dates_cache: dict[str, list[str]],
) -> dict[str, Any]:
    """Resolve one leg config dict to a tradable symbol + lotsize.

    Caches the expiry-date list per (underlying, instrument) so multi-leg
    strategies don't fan out N copies of the same `get_expiry_dates` call.
    """
    segment = leg.get("segment")
    if segment == "options":
        instrument_key = "options"
    elif segment == "futures":
        instrument_key = "futures"
    else:
        # Cash equity — symbol IS the underlying; no expiry resolution.
        return {
            "symbol": underlying,
            "exchange": underlying_exchange,
            "lotsize": 1,
            "tick_size": 0.05,
            "strike": None,
            "expiry": None,
        }

    # Resolve the leg's expiry rank to a real date
    cache_key = f"{underlying}:{underlying_exchange}:{instrument_key}"
    if cache_key not in expiry_dates_cache:
        ok, data, _ = symbol_resolver.list_expiries(
            underlying, underlying_exchange, instrument_key,
        )
        if not ok or not data.get("data"):
            raise EngineError(
                f"No {instrument_key} expiries for {underlying} "
                f"on {underlying_exchange}: {data.get('message', 'empty')}"
            )
        expiry_dates_cache[cache_key] = data["data"]

    rank = leg.get("expiry") or "weekly"
    resolved, _ = symbol_resolver.resolve_expiry_rank(
        rank, expiry_dates_cache[cache_key],
    )
    if not resolved:
        raise EngineError(f"Couldn't resolve expiry rank '{rank}' for {underlying}")

    # Futures: build {underlying}{DDMMMYY}FUT
    if instrument_key == "futures":
        compact = resolved.replace("-", "").upper()
        symbol = f"{underlying}{compact}FUT"
        # Look up the FUT in symtoken to get lotsize/tick_size
        from backend.services.option_symbol_service import _lookup_option_in_db
        fut_exchange = (
            "NFO" if underlying_exchange in ("NSE", "NSE_INDEX")
            else "BFO" if underlying_exchange in ("BSE", "BSE_INDEX")
            else underlying_exchange
        )
        details = _lookup_option_in_db(symbol, fut_exchange)
        if not details:
            raise EngineError(f"FUT not in symtoken: {symbol} on {fut_exchange}")
        return {
            "symbol": details["symbol"],
            "exchange": details["exchange"],
            "lotsize": details["lotsize"],
            "tick_size": details["tick_size"],
            "strike": None,
            "expiry": details["expiry"],
        }

    # Options
    strike_mode = leg.get("strike_mode") or "atm"
    option_type = leg.get("option_type")
    if not option_type:
        raise EngineError(f"Leg {leg.get('id')}: option_type required for options segment")

    if strike_mode == "atm":
        atm_offset = leg.get("atm_offset") or "ATM"
        if not auth_token or not broker:
            # Without broker auth we can't fetch the underlying's LTP, which
            # the ATM resolution needs. Sandbox runs that don't actually need
            # to trade can use direct strikes instead.
            raise EngineError(
                f"Leg {leg.get('id')}: ATM resolution needs broker auth — "
                f"either select live mode (Phase 10) or use direct strikes."
            )
        ok, data, _ = symbol_resolver.resolve_atm(
            underlying=underlying,
            underlying_exchange=underlying_exchange,
            expiry_date=resolved,
            atm_offset=atm_offset,
            option_type=option_type,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        if not ok:
            raise EngineError(f"Leg {leg.get('id')}: {data.get('message', 'ATM resolution failed')}")
        return data

    if strike_mode == "strike":
        strike_value = leg.get("strike_value")
        if strike_value is None:
            raise EngineError(f"Leg {leg.get('id')}: strike_value required when strike_mode=strike")
        ok, data, _ = symbol_resolver.resolve_direct_strike(
            underlying=underlying,
            underlying_exchange=underlying_exchange,
            expiry_date=resolved,
            strike=float(strike_value),
            option_type=option_type,
        )
        if not ok:
            raise EngineError(f"Leg {leg.get('id')}: {data.get('message', 'direct strike not found')}")
        return data

    raise EngineError(f"Leg {leg.get('id')}: unknown strike_mode '{strike_mode}'")


def _entry_action(position: str) -> str:
    """B → BUY, S → SELL."""
    return "BUY" if position == "B" else "SELL"


def _exit_action(position: str) -> str:
    """Inverse of _entry_action."""
    return "SELL" if position == "B" else "BUY"


# ---------------------------------------------------------------------------
# Lifecycle: start
# ---------------------------------------------------------------------------


async def start_run(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    mode: str,
    broker: str,
    auth_token: Optional[str],
    config: Optional[dict[str, Any]],
    trigger_source: str = "manual",
) -> tuple[SmStrategyRun, list[dict[str, Any]]]:
    """Resolve all legs, place entries, return (run, leg_summaries).

    Per the plan, live mode requires `strategy.live_enabled`. The router
    enforces this; we double-check here for defense in depth.
    """
    if mode not in ("live", "sandbox"):
        raise EngineError(f"Invalid mode: {mode}")
    if mode == "live" and not strategy.live_enabled:
        raise EngineError(
            "Strategy is not enabled for live mode. Enable live in the detail "
            "page after re-authenticating, or start in sandbox mode."
        )

    # Acquire a row-level lock on the strategy row and re-read its status
    # from under the lock. Plan section 16 requires idempotency between
    # concurrent triggers (webhook + scheduler, manual + webhook, multiple
    # workers). Without the lock, two callers could both observe
    # status='stopped' on a stale in-memory copy, both pass this gate,
    # both resolve legs, both place entry orders - producing duplicate
    # broker orders and overwriting current_run_id with the second commit.
    locked = (await db.execute(
        select(SmStrategy)
        .where(SmStrategy.id == strategy.id)
        .with_for_update()
    )).scalar_one_or_none()
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    if locked.status != "stopped":
        raise EngineError(
            f"Cannot start - strategy is currently '{locked.status}'"
        )
    # The caller's `strategy` may be stale relative to what the lock now
    # sees in the DB. Use the locked row from here onwards.
    strategy = locked

    legs = strategy.legs or []
    if not legs:
        raise EngineError("Strategy has no legs configured")

    # Resolve every leg upfront — fail fast before any order goes out.
    expiry_cache: dict[str, list[str]] = {}
    resolved_legs: list[dict[str, Any]] = []
    for leg in legs:
        try:
            r = _resolve_leg(
                leg,
                underlying=strategy.underlying,
                underlying_exchange=strategy.underlying_exchange,
                auth_token=auth_token,
                broker=broker,
                config=config,
                expiry_dates_cache=expiry_cache,
            )
        except EngineError as e:
            raise EngineError(f"Leg {leg.get('id', '?')} resolution failed: {e}") from e
        resolved_lotsize = r["lotsize"]
        segment = leg.get("segment")
        if segment in ("options", "futures"):
            try:
                resolved_lotsize_int = int(resolved_lotsize) if resolved_lotsize is not None else 0
            except (TypeError, ValueError):
                resolved_lotsize_int = 0
            if resolved_lotsize_int <= 0:
                raise EngineError(
                    f"Leg {leg.get('id', '?')} ({r['symbol']}): lotsize missing or "
                    f"non-positive in symtoken (got {resolved_lotsize!r}). Refusing to "
                    f"place order — would default to 1 unit instead of the correct lot."
                )
            resolved_lotsize = resolved_lotsize_int
        resolved_legs.append({
            "leg_id": leg["id"],
            "position": leg["position"],
            "lots": leg["lots"],
            "symbol": r["symbol"],
            "exchange": r["exchange"],
            "lotsize": resolved_lotsize,
            "tick_size": r["tick_size"],
            "strike": r.get("strike"),
            "expiry": r.get("expiry"),
        })

    # Open a run row first — so any failure mid-placement is logged against
    # a real run id (and the cleanup path can square off whatever did fill).
    run = await repo.start_run(
        db, strategy=strategy, mode=mode, broker=broker, trigger_source=trigger_source,
    )

    # Place entry orders BUY-before-SELL — same convention as
    # options_multiorder_service. Avoids margin spikes on credit spreads
    # where the long leg should fund the short leg's margin.
    buy_legs = [r for r in resolved_legs if r["position"] == "B"]
    sell_legs = [r for r in resolved_legs if r["position"] == "S"]

    leg_summaries: list[dict[str, Any]] = []
    placement_errors: list[str] = []

    for r in buy_legs + sell_legs:
        action = _entry_action(r["position"])
        qty = int(r["lots"]) * int(r["lotsize"])
        order_data = {
            "symbol": r["symbol"],
            "exchange": r["exchange"],
            "action": action,
            "quantity": str(qty),
            "pricetype": strategy.pricetype or "MARKET",
            "product": strategy.product or "NRML",
            "price": "0",
            "trigger_price": "0",
            "strategy": strategy.name,
        }
        ok, response, _status = dispatch_order(
            mode=mode,
            user_id=strategy.user_id,
            order_data=order_data,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        broker_order_id = response.get("orderid") if isinstance(response, dict) else None

        order_row = await repo.record_order(
            db,
            run_id=run.id,
            leg_id=r["leg_id"],
            kind="entry",
            symbol=r["symbol"],
            exchange=r["exchange"],
            action=action,
            qty=qty,
            pricetype=strategy.pricetype or "MARKET",
            broker_order_id=broker_order_id,
            status="open" if ok else "rejected",
            reject_reason=None if ok else response.get("message", "rejected"),
        )
        # Reconcile against sandbox/broker immediately so the order's
        # actual fill price + status are on the row before the engine
        # emits the audit event. Without this the orderbook UI sticks
        # at 'open' and Live P&L has nothing to compute against.
        if ok:
            try:
                await reconcile_order_fill(
                    db, order_row=order_row, mode=mode,
                    user_id=strategy.user_id,
                    broker=broker, auth_token=auth_token,
                )
            except Exception:
                logger.exception(
                    "Fill recon failed for entry order %s", order_row.id,
                )
        repo.emit_leg_entry_placed(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            run_id=run.id,
            leg_id=r["leg_id"],
            symbol=r["symbol"],
            action=action,
            qty=qty,
            broker_order_id=broker_order_id,
        )
        leg_summaries.append({
            **r,
            "qty": qty,
            "order_id": order_row.id,
            "broker_order_id": broker_order_id,
            "status": order_row.status,
            "reject_reason": order_row.reject_reason,
        })
        if not ok:
            placement_errors.append(
                f"leg {r['leg_id']} ({r['symbol']}): {response.get('message', 'rejected')}"
            )

    # If every leg failed to place, the run is wedged — finalize as 'error'.
    # If only some failed, leave the run running so the user can square off
    # what did fill from the UI; log the partial failure prominently.
    all_failed = all(s["status"] == "rejected" for s in leg_summaries)
    if all_failed:
        await repo.finalize_run(
            db, run=run, strategy=strategy, stop_reason="error",
        )
        raise EngineError(
            f"All entry orders rejected: {'; '.join(placement_errors)}"
        )
    if placement_errors:
        logger.warning(
            "strategy %d run %d: partial entry failure — %s",
            strategy.id, run.id, "; ".join(placement_errors),
        )

    # Seed Redis state so the checkpoint loop and Phase 6 tick loop have
    # something to read. Failure is non-fatal — recovery rebuilds from DB.
    try:
        entry_by_leg = {ls["leg_id"]: ls for ls in leg_summaries}
        await state_module.init_run_state(
            run_id=run.id,
            strategy_id=strategy.id,
            strategy_legs=strategy.legs or [],
            entry_orders_by_leg=entry_by_leg,
        )
    except Exception:
        logger.exception("Failed to init Redis state for run %d", run.id)

    # Push reconciled fill prices into Redis state so the Live P&L card
    # has entry_avg to compute MTM against. Reads back the order rows
    # we just reconciled above and mirrors avg_fill_price -> entry_avg.
    try:
        for ls in leg_summaries:
            if ls.get("status") == "rejected":
                continue
            # Look up the freshly-reconciled order row by id.
            order_row = await db.get(SmStrategyOrder, ls["order_id"])
            if order_row is None or order_row.avg_fill_price is None:
                continue
            await _apply_fill_to_state(
                run.id,
                leg_id=ls["leg_id"],
                order_kind="entry",
                avg_fill_price=float(order_row.avg_fill_price),
                filled_qty=int(order_row.filled_qty or order_row.qty),
                strategy_legs=strategy.legs or [],
            )
    except Exception:
        logger.exception("Failed to apply entry fills to state for run %d", run.id)

    # Subscribe to ticks for every leg that placed successfully — Phase 6
    # risk evaluator runs on these ticks.
    try:
        symbols = list({
            (ls["exchange"], ls["symbol"])
            for ls in leg_summaries
            if ls.get("status") != "rejected"
            and ls.get("symbol") and ls.get("exchange")
        })
        tick_feed.add_run_subscriptions(run.id, symbols)
    except Exception:
        logger.exception("Failed to subscribe ticks for run %d", run.id)

    return run, leg_summaries


# ---------------------------------------------------------------------------
# Lifecycle: exit (whole run or single leg)
# ---------------------------------------------------------------------------


async def _exit_legs(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    run: SmStrategyRun,
    leg_ids: list[int],
    exit_kind: str,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Place exit orders for the named legs.

    Strategy-leg config (from `strategy.legs`) supplies position/lots/etc.
    Phase 4 doesn't read fill state from the orderbook — we trust the
    engine's invariant that an entry placed = a leg open until manually
    closed. Phase 5 adds broker reconciliation.
    """
    cfg_by_leg: dict[int, dict[str, Any]] = {leg["id"]: leg for leg in (strategy.legs or [])}

    # We need each leg's resolved symbol again. Re-resolve from the run's
    # entry orders rather than re-running symbol resolution: the user might
    # have rotated tokens or the underlying LTP may have moved, but the
    # symbol they entered on is the symbol they exit on.
    #
    # Also collect existing non-rejected exits — a leg that's already been
    # closed (manually or otherwise) must not get a duplicate exit when a
    # subsequent stop_run/close_all runs over it.
    entry_orders_by_leg: dict[int, SmStrategyOrder] = {}
    legs_with_exits: set[int] = set()
    for o in await repo.list_orders_for_run(db, user_id=strategy.user_id, run_id=run.id):
        if o.kind == "entry" and o.leg_id in leg_ids:
            entry_orders_by_leg[o.leg_id] = o
        elif o.kind != "entry" and o.status != "rejected":
            legs_with_exits.add(o.leg_id)

    summaries: list[dict[str, Any]] = []
    for leg_id in leg_ids:
        cfg = cfg_by_leg.get(leg_id)
        entry = entry_orders_by_leg.get(leg_id)
        if not cfg or not entry:
            summaries.append({
                "leg_id": leg_id,
                "status": "skipped",
                "reason": "no entry order found for this leg",
            })
            continue
        if entry.status == "rejected":
            summaries.append({
                "leg_id": leg_id,
                "status": "skipped",
                "reason": "entry was rejected — nothing to exit",
            })
            continue
        if leg_id in legs_with_exits:
            summaries.append({
                "leg_id": leg_id,
                "status": "skipped",
                "reason": "already closed",
            })
            continue

        action = _exit_action(cfg["position"])
        order_data = {
            "symbol": entry.symbol,
            "exchange": entry.exchange,
            "action": action,
            "quantity": str(entry.qty),
            "pricetype": "MARKET",
            "product": strategy.product or "NRML",
            "price": "0",
            "trigger_price": "0",
            "strategy": strategy.name,
        }
        ok, response, _ = dispatch_order(
            mode=run.mode,
            user_id=strategy.user_id,
            order_data=order_data,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        broker_order_id = response.get("orderid") if isinstance(response, dict) else None
        order_row = await repo.record_order(
            db,
            run_id=run.id,
            leg_id=leg_id,
            kind=exit_kind,
            symbol=entry.symbol,
            exchange=entry.exchange,
            action=action,
            qty=int(entry.qty),
            pricetype="MARKET",
            broker_order_id=broker_order_id,
            status="open" if ok else "rejected",
            reject_reason=None if ok else response.get("message", "rejected"),
        )
        # Reconcile the exit so realized P&L lands in state immediately.
        if ok:
            try:
                await reconcile_order_fill(
                    db, order_row=order_row, mode=run.mode,
                    user_id=strategy.user_id,
                    broker=broker, auth_token=auth_token,
                )
                if order_row.avg_fill_price is not None:
                    await _apply_fill_to_state(
                        run.id,
                        leg_id=leg_id,
                        order_kind=exit_kind,
                        avg_fill_price=float(order_row.avg_fill_price),
                        filled_qty=int(order_row.filled_qty or order_row.qty),
                        strategy_legs=strategy.legs or [],
                    )
            except Exception:
                logger.exception(
                    "Fill recon failed for exit order %s", order_row.id,
                )
        repo.emit_leg_exit_placed(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            run_id=run.id,
            leg_id=leg_id,
            symbol=entry.symbol,
            action=action,
            qty=int(entry.qty),
            kind=exit_kind,
            broker_order_id=broker_order_id,
        )
        try:
            await state_module.mark_leg_closed(
                run.id, leg_id,
                exit_order_id=order_row.id,
                exit_kind=exit_kind,
                exit_status=order_row.status,
            )
        except Exception:
            logger.exception("Failed to mark leg %d closed in Redis state", leg_id)
        summaries.append({
            "leg_id": leg_id,
            "order_id": order_row.id,
            "broker_order_id": broker_order_id,
            "status": order_row.status,
            "reject_reason": order_row.reject_reason,
        })
    return summaries


async def stop_run(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    stop_reason: str,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Exit every leg that has an entry, then mark the run stopped.

    Used by both /stop and /close_all. The audit trail captures the user's
    intent via the differentiated event kinds (run_stopped vs close_all_manual).
    """
    if strategy.status != "running" or strategy.current_run_id is None:
        raise EngineError(f"No active run to stop (status='{strategy.status}')")

    run = await repo.get_run(
        db, user_id=strategy.user_id, run_id=strategy.current_run_id,
    )
    leg_ids = [int(leg["id"]) for leg in (strategy.legs or [])]
    summaries = await _exit_legs(
        db, strategy=strategy, run=run, leg_ids=leg_ids,
        exit_kind=_exit_kind_for_stop(stop_reason),
        auth_token=auth_token, broker=broker, config=config,
    )
    await repo.finalize_run(
        db, run=run, strategy=strategy, stop_reason=stop_reason,
    )
    try:
        await state_module.clear_run_state(run.id)
    except Exception:
        logger.exception("Failed to clear Redis state for run %d", run.id)
    try:
        tick_feed.remove_run_subscriptions(run.id)
    except Exception:
        logger.exception("Failed to unsubscribe ticks for run %d", run.id)
    return {"run_id": run.id, "stop_reason": stop_reason, "legs": summaries}


def _exit_kind_for_stop(stop_reason: str) -> str:
    """Map a `stop_reason` to the right `strategy_order.kind`.

    Keeps the order audit precise so post-mortems can answer "why was leg N
    exited at 14:32:11?" without joining tables.
    """
    return {
        "manual": "exit_close_all",
        "scheduler": "exit_close_all",
        "overall_sl": "exit_overall_sl",
        "overall_target": "exit_overall_target",
        "lock_profit": "exit_lock_profit",
        "eod": "exit_eod",
        "expiry": "exit_expiry",
        "daily_loss_limit": "exit_daily_loss_limit",
        "tick_stale": "exit_close_all",
        "recovery_failed": "exit_recovery",
        "error": "exit_close_all",
    }.get(stop_reason, "exit_close_all")


async def close_leg(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    leg_id: int,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Exit a single leg. Run stays open."""
    if strategy.status != "running" or strategy.current_run_id is None:
        raise EngineError(f"No active run (status='{strategy.status}')")
    if not any(int(leg["id"]) == leg_id for leg in (strategy.legs or [])):
        raise EngineError(f"Leg {leg_id} does not exist on this strategy")

    run = await repo.get_run(
        db, user_id=strategy.user_id, run_id=strategy.current_run_id,
    )
    summaries = await _exit_legs(
        db, strategy=strategy, run=run, leg_ids=[leg_id],
        exit_kind="exit_leg_manual",
        auth_token=auth_token, broker=broker, config=config,
    )
    return {"run_id": run.id, "legs": summaries}


# ---------------------------------------------------------------------------
# Signal-mode engine (slice 4)
#
# Signal-mode strategies don't run the batch lifecycle (start_run places
# every leg at once, stop_run squares everything). Instead each leg has
# its own state and reacts to per-leg TradingView signals
# (long_entry / long_exit / short_entry / short_exit). The same run row
# spans the trading day - the first signal of the day creates it; the
# scheduler's auto-stop at exit_time finalizes it (slice 6).
#
# Per the design (docs/plan/strategy-signal-mode.md section 5):
#   - enter_leg places one entry; idempotent if leg is already in the
#     requested direction; deferred-to-v2 flip when in the opposite
#     direction (v1 returns 'position_conflict' so the operator must
#     exit first).
#   - exit_leg_by_signal places one exit; silent no-op when the leg
#     isn't in the requested direction (matches design 4.4).
# ---------------------------------------------------------------------------


# Map signal action -> (entry_side, broker_action)
_ENTRY_ACTION_TO_SIDE: dict[str, str] = {
    "long_entry": "long",
    "short_entry": "short",
}
_ENTRY_ACTION_TO_BROKER: dict[str, str] = {
    "long_entry": "BUY",
    "short_entry": "SELL",
}
# Exit signals translate the requested closing side to the broker action
# needed to flatten it.
_EXIT_ACTION_TO_SIDE: dict[str, str] = {
    "long_exit": "long",
    "short_exit": "short",
}
_EXIT_ACTION_TO_BROKER: dict[str, str] = {
    "long_exit": "SELL",   # close a long => sell
    "short_exit": "BUY",   # close a short => buy
}


async def _get_or_create_signal_run(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    mode: str,
    broker: str,
    trigger_source: str,
) -> SmStrategyRun:
    """Find the strategy's active run or create one.

    Signal-mode strategies use one run per trading day - the first signal
    creates it; the scheduler's auto-stop closes it (slice 6). Concurrent
    callers are serialized by the SELECT FOR UPDATE lock the caller already
    holds on the strategy row (see enter_leg).
    """
    if strategy.current_run_id is not None:
        existing = await db.get(SmStrategyRun, strategy.current_run_id)
        if existing is not None and existing.stopped_at is None:
            return existing
    # No active run - create one.
    run = await repo.start_run(
        db, strategy=strategy, mode=mode, broker=broker,
        trigger_source=trigger_source,
    )
    return run


def _signal_leg_state(state_legs: dict[str, Any], leg_id: int) -> dict[str, Any]:
    """Return the leg-state dict, initializing a flat shell on first touch.

    Batch-mode init_run_state seeds every leg up front. Signal-mode legs
    are dormant until their first signal, so the first enter_leg call may
    find no leg entry in the state dict.
    """
    key = str(leg_id)
    leg = state_legs.get(key)
    if leg is None:
        leg = {
            "leg_id": leg_id,
            "current_side": None,
            "qty": None,
            "symbol": None,
            "exchange": None,
            "entry_order_id": None,
            "entry_avg": None,
            "exit_order_id": None,
            "ltp": None,
            "mtm": 0.0,
            "status": "configured",
        }
        state_legs[key] = leg
    return leg


async def _signal_lock_strategy(
    db: AsyncSession, strategy_id: int,
) -> Optional[SmStrategy]:
    """Acquire the FOR UPDATE row lock used by every signal-mode entry/exit.

    Same pattern as start_run's lock from iteration 7 - serializes
    concurrent signals on the same strategy across workers.
    """
    return (await db.execute(
        select(SmStrategy)
        .where(SmStrategy.id == strategy_id)
        .with_for_update()
    )).scalar_one_or_none()


def _intraday_window_status(strategy: SmStrategy) -> str:
    """Classify the current moment against an intraday strategy's window.

    Returns one of:
      * 'no_window'       - positional strategy, no window applies.
      * 'outside_entry'   - now < entry_time. Entry signals are blocked;
                            exit signals pass through.
      * 'active'          - entry_time <= now < exit_time. Everything OK.
      * 'outside_trading' - now >= exit_time. All signals blocked; the
                            scheduler's auto-square job is the only thing
                            still allowed to act on the run.

    All comparisons in IST (Asia/Kolkata) since strategy.entry_time /
    exit_time are stored as bare IST time-of-day per design section 4.4.
    """
    if strategy.strategy_type != "intraday":
        return "no_window"
    entry_t = strategy.entry_time
    exit_t = strategy.exit_time
    if entry_t is None or exit_t is None:
        # Schema validates these are non-null for intraday; if a row got
        # here without them treat as no_window rather than crash.
        return "no_window"
    from backend.strategy.time_utils import now_ist
    now_t = now_ist().time()
    if now_t < entry_t:
        return "outside_entry"
    if now_t >= exit_t:
        return "outside_trading"
    return "active"


def _direction_admits_entry(direction: str, action: str) -> bool:
    """Defense-in-depth direction check at the engine layer.

    The webhook handler already runs the same gate before dispatching,
    but the engine entry points (enter_leg / exit_leg_by_signal) are
    public functions that any caller could invoke - scheduler-fired
    auto-exits, internal management endpoints, future programmatic
    APIs. Re-checking here means a `direction=short_only` strategy
    can't accept a long_entry no matter who calls the engine.

    Returns True when the action is admitted by the strategy's
    direction filter. Mirrors webhook_handler._direction_allows but
    re-implemented here to avoid a backend->backend module dependency.
    """
    if direction == "both":
        return True
    if direction == "long_only":
        return action in ("long_entry", "long_exit")
    if direction == "short_only":
        return action in ("short_entry", "short_exit")
    return False  # unknown direction value -> fail closed


async def enter_leg(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    leg_config: dict[str, Any],
    action: str,
    mode: str,
    broker: str,
    auth_token: Optional[str],
    config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Open one signal-mode leg in response to long_entry or short_entry.

    Returns ``{outcome: ..., note?: ..., order_id?: ..., run_id?: ...}``
    so the webhook handler can shape the HTTP response without knowing
    the engine internals. ``outcome`` is one of:
      * ``placed``               - new entry order placed
      * ``already_in_position``  - leg already long/short; silent no-op
      * ``position_conflict``    - leg in opposite direction; v1 refuses
                                   the flip and asks the operator to exit
                                   first (deferred design item, see
                                   docs/plan/strategy-signal-mode.md
                                   section 9)
    """
    if action not in _ENTRY_ACTION_TO_SIDE:
        raise EngineError(f"enter_leg: invalid action {action!r}")
    requested_side = _ENTRY_ACTION_TO_SIDE[action]
    broker_action = _ENTRY_ACTION_TO_BROKER[action]
    leg_id = int(leg_config["id"])

    # Lock the strategy row (same TOCTOU guard the batch-mode start_run
    # uses) and re-read it. The caller's `strategy` may be stale.
    locked = await _signal_lock_strategy(db, strategy.id)
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    strategy = locked

    # Defense-in-depth direction gate. The webhook handler already runs
    # this check upstream; the engine re-checks for any caller that
    # bypasses the webhook (scheduler auto-exits, internal management
    # endpoints, future programmatic APIs). Records a leg_entry_rejected
    # event before returning so the audit trail captures attempted
    # entries that were filtered out.
    strategy_direction = getattr(strategy, "direction", "both") or "both"
    if not _direction_admits_entry(strategy_direction, action):
        repo.emit_leg_entry_rejected(
            user_id=strategy.user_id,
            strategy_id=strategy.id,
            run_id=strategy.current_run_id,
            leg_id=leg_id,
            reason=f"direction={strategy_direction} blocks {action}",
            payload={
                "action": action,
                "direction": strategy_direction,
                "leg_symbol": leg_config.get("symbol"),
                "leg_exchange": leg_config.get("exchange"),
            },
        )
        return {
            "outcome": "direction_blocked",
            "note": f"strategy direction={strategy_direction!r} blocks {action!r}",
            "leg_id": leg_id,
            "direction": strategy_direction,
        }

    # Intraday window check. Entry signals refused before entry_time and
    # after exit_time; exit signals are handled separately below in
    # exit_leg_by_signal. Positional strategies bypass the window check.
    window = _intraday_window_status(strategy)
    if window == "outside_entry":
        return {
            "outcome": "outside_entry_window",
            "note": (
                f"entry signals are accepted from {strategy.entry_time} IST onward"
            ),
            "leg_id": leg_id,
        }
    if window == "outside_trading":
        return {
            "outcome": "outside_trading_window",
            "note": (
                f"trading window closed at {strategy.exit_time} IST; auto-square has run"
            ),
            "leg_id": leg_id,
        }

    # Ensure there is an active run.
    run = await _get_or_create_signal_run(
        db, strategy=strategy, mode=mode, broker=broker,
        trigger_source="webhook",
    )

    # Read leg state from Redis - lazily init for first-signal-of-day.
    state = await state_module.get_run_state(run.id)
    if state is None:
        # Brand-new run created above hasn't been state-seeded yet.
        state = {
            "run_id": run.id,
            "strategy_id": strategy.id,
            "pnl_realized": 0.0, "pnl_unrealized": 0.0, "pnl_total": 0.0,
            "pnl_peak": 0.0, "pnl_trough": 0.0,
            "lock_armed": False, "lock_floor": None,
            "trail_to_entry_active": False,
            "legs": {},
        }
    leg_state = _signal_leg_state(state.setdefault("legs", {}), leg_id)
    current_side = leg_state.get("current_side")

    # Idempotency / conflict detection.
    if current_side == requested_side:
        # Already in the requested direction - silent no-op per design 4.4.
        await state_module.hydrate_run_state(run.id, state)
        return {
            "outcome": "already_in_position",
            "note": f"already {requested_side}",
            "leg_id": leg_id,
            "run_id": run.id,
        }
    if current_side is not None and current_side != requested_side:
        # Opposite direction - flip is deferred to a future slice (open
        # question 9.flip in the design doc). For now refuse and let the
        # operator exit first.
        return {
            "outcome": "position_conflict",
            "note": (
                f"leg currently {current_side}; exit first via "
                f"{current_side}_exit before opening the opposite side"
            ),
            "leg_id": leg_id,
            "run_id": run.id,
        }

    # Place the entry order. qty is the absolute share/lot count stored
    # on the signal-mode leg config - lotsize multiplication already
    # baked in by the wizard for futures/options legs; raw shares for cash.
    qty = int(leg_config["qty"])
    if qty <= 0:
        raise EngineError(f"Leg {leg_id}: qty must be > 0 (got {qty!r})")

    # Resolve futures/options to a real contract symbol at signal time.
    # The leg's `symbol` carries the base (e.g. NIFTY, RELIANCE, CRUDEOIL)
    # and `expiry` carries the rank (current/next for FUT, weekly/monthly/
    # current/next for OPT). Cash legs trade the underlying directly.
    segment = leg_config.get("segment") or "cash"
    if segment in ("futures", "options"):
        try:
            resolved = _resolve_leg(
                leg_config,
                underlying=leg_config["symbol"],
                underlying_exchange=leg_config["exchange"],
                auth_token=auth_token,
                broker=broker,
                config=config,
                expiry_dates_cache={},
            )
        except EngineError as e:
            raise EngineError(f"Leg {leg_id} resolution failed: {e}") from e
        order_symbol = resolved["symbol"]
        order_exchange = resolved["exchange"]
    else:
        order_symbol = leg_config["symbol"]
        order_exchange = leg_config["exchange"]

    order_data = {
        "symbol": order_symbol,
        "exchange": order_exchange,
        "action": broker_action,
        "quantity": str(qty),
        "pricetype": strategy.pricetype or "MARKET",
        "product": strategy.product or "MIS",
        "price": "0",
        "trigger_price": "0",
        "strategy": strategy.name,
    }
    ok, response, _status = dispatch_order(
        mode=mode, user_id=strategy.user_id, order_data=order_data,
        auth_token=auth_token, broker=broker, config=config,
    )
    broker_order_id = response.get("orderid") if isinstance(response, dict) else None
    order_row = await repo.record_order(
        db,
        run_id=run.id,
        leg_id=leg_id,
        kind="entry",
        symbol=order_symbol,
        exchange=order_exchange,
        action=broker_action,
        qty=qty,
        pricetype=strategy.pricetype or "MARKET",
        broker_order_id=broker_order_id,
        status="open" if ok else "rejected",
        reject_reason=None if ok else (response.get("message") if isinstance(response, dict) else "rejected"),
    )

    # Reconcile against sandbox/broker fill so the row carries the real
    # status + executed price. Drives the orderbook UI off 'open' onto
    # 'complete' immediately and puts entry_avg in Redis state.
    if ok:
        try:
            await reconcile_order_fill(
                db, order_row=order_row, mode=mode,
                user_id=strategy.user_id,
                broker=broker, auth_token=auth_token,
            )
        except Exception:
            logger.exception(
                "Fill recon failed for signal entry order %s", order_row.id,
            )

    # Audit event + leg state update.
    repo.emit_leg_entry_placed(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id,
        leg_id=leg_id,
        symbol=order_symbol,
        action=broker_action,
        qty=qty,
        broker_order_id=broker_order_id,
    )
    if ok:
        leg_state["current_side"] = requested_side
        leg_state["qty"] = qty
        # Store the RESOLVED contract so exit_leg_by_signal targets the
        # same instrument even after expiry rolls (e.g. 'current' moves
        # to the next contract; the leg-state pins the entered contract).
        leg_state["symbol"] = order_symbol
        leg_state["exchange"] = order_exchange
        leg_state["entry_order_id"] = order_row.id
        leg_state["status"] = "open"
        leg_state["exit_order_id"] = None
        if order_row.avg_fill_price is not None:
            leg_state["entry_avg"] = float(order_row.avg_fill_price)
    else:
        # Order rejected at broker/sandbox. Leg stays flat. Surface the
        # reject_reason so the operator can investigate from the UI.
        leg_state["status"] = "rejected"
    await state_module.hydrate_run_state(run.id, state)

    # Recompute strategy aggregates after the state mutation above.
    if ok and order_row.avg_fill_price is not None:
        try:
            await _apply_fill_to_state(
                run.id,
                leg_id=leg_id,
                order_kind="entry",
                avg_fill_price=float(order_row.avg_fill_price),
                filled_qty=int(order_row.filled_qty or qty),
                strategy_legs=strategy.legs or [],
            )
        except Exception:
            logger.exception(
                "Failed to apply signal-entry fill to state for run %d leg %d",
                run.id, leg_id,
            )

    # Tick subscription for risk eval - same pattern as batch start_run.
    if ok:
        try:
            tick_feed.add_run_subscriptions(
                run.id,
                [(order_exchange, order_symbol)],
            )
        except Exception:
            logger.exception("signal enter_leg: failed to subscribe ticks")

    return {
        "outcome": "placed" if ok else "rejected",
        "order_id": order_row.id,
        "broker_order_id": broker_order_id,
        "leg_id": leg_id,
        "run_id": run.id,
        "reject_reason": None if ok else order_row.reject_reason,
    }


async def exit_leg_by_signal(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    leg_config: dict[str, Any],
    action: str,
    mode: str,
    broker: str,
    auth_token: Optional[str],
    config: Optional[dict[str, Any]],
    bypass_window: bool = False,
) -> dict[str, Any]:
    """Close one signal-mode leg in response to long_exit or short_exit.

    Silent no-op (returns ``outcome='no_matching_position'``) when the
    leg isn't currently in the requested direction. Matches design 4.4
    so repeat exit alerts for an already-flat leg are idempotent.

    ``bypass_window`` is set to True only by :func:`signal_auto_square`
    so the scheduler's end-of-day cleanup can close positions AT
    exit_time without being blocked by its own window check. External
    callers (webhook handler) always leave it False.
    """
    if action not in _EXIT_ACTION_TO_SIDE:
        raise EngineError(f"exit_leg_by_signal: invalid action {action!r}")
    requested_side = _EXIT_ACTION_TO_SIDE[action]
    broker_action = _EXIT_ACTION_TO_BROKER[action]
    leg_id = int(leg_config["id"])

    locked = await _signal_lock_strategy(db, strategy.id)
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    strategy = locked

    # Defense-in-depth direction gate (same rationale as enter_leg).
    # Exit signals are gated symmetrically so a short_only strategy
    # can't process a long_exit aimed at a leg that may have opened
    # under a previous direction setting before it was tightened.
    strategy_direction = getattr(strategy, "direction", "both") or "both"
    if not _direction_admits_entry(strategy_direction, action):
        return {
            "outcome": "direction_blocked",
            "note": f"strategy direction={strategy_direction!r} blocks {action!r}",
            "leg_id": leg_id,
            "direction": strategy_direction,
        }

    # Intraday window check. Exits are allowed BEFORE entry_time
    # (operator might want to flatten a stale overnight position) but
    # blocked AFTER exit_time - by then the scheduler's auto-square
    # should have flattened everything, and any signal arriving late
    # is treated as a no-op so a delayed TV alert can't re-open a
    # position that just got squared.
    # bypass_window: True only when signal_auto_square calls us; that
    # path fires AT exit_time and needs to close positions despite the
    # window itself being over.
    if not bypass_window:
        window = _intraday_window_status(strategy)
        if window == "outside_trading":
            return {
                "outcome": "outside_trading_window",
                "note": (
                    f"trading window closed at {strategy.exit_time} IST; "
                    f"auto-square has run"
                ),
                "leg_id": leg_id,
            }

    # No active run = nothing to exit. Silent no-op.
    if strategy.current_run_id is None:
        return {
            "outcome": "no_matching_position",
            "note": "no active run on this strategy",
            "leg_id": leg_id,
        }
    run = await db.get(SmStrategyRun, strategy.current_run_id)
    if run is None or run.stopped_at is not None:
        return {
            "outcome": "no_matching_position",
            "note": "run already stopped",
            "leg_id": leg_id,
        }

    state = await state_module.get_run_state(run.id)
    leg_state = (state or {}).get("legs", {}).get(str(leg_id)) if state else None
    current_side = leg_state.get("current_side") if leg_state else None
    if current_side != requested_side:
        return {
            "outcome": "no_matching_position",
            "note": (
                f"leg is {current_side or 'flat'}; "
                f"{action} requires current_side={requested_side}"
            ),
            "leg_id": leg_id,
            "run_id": run.id,
        }

    # Exit the position. Quantity comes from the state (the qty actually
    # in the market), not the leg config - if a future flip lands the qty
    # could legitimately differ from the configured size.
    qty = int(leg_state.get("qty") or leg_config["qty"])
    if qty <= 0:
        raise EngineError(f"Leg {leg_id}: exit qty must be > 0 (got {qty!r})")

    order_data = {
        "symbol": leg_state.get("symbol") or leg_config["symbol"],
        "exchange": leg_state.get("exchange") or leg_config["exchange"],
        "action": broker_action,
        "quantity": str(qty),
        "pricetype": "MARKET",
        "product": strategy.product or "MIS",
        "price": "0",
        "trigger_price": "0",
        "strategy": strategy.name,
    }
    ok, response, _status = dispatch_order(
        mode=mode, user_id=strategy.user_id, order_data=order_data,
        auth_token=auth_token, broker=broker, config=config,
    )
    broker_order_id = response.get("orderid") if isinstance(response, dict) else None
    order_row = await repo.record_order(
        db,
        run_id=run.id,
        leg_id=leg_id,
        kind="exit_signal",
        symbol=order_data["symbol"],
        exchange=order_data["exchange"],
        action=broker_action,
        qty=qty,
        pricetype="MARKET",
        broker_order_id=broker_order_id,
        status="open" if ok else "rejected",
        reject_reason=None if ok else (response.get("message") if isinstance(response, dict) else "rejected"),
    )
    # Reconcile fill + compute realized P&L. Exit avg price drives the
    # realized column on the Live P&L card and on the Positions view.
    if ok:
        try:
            await reconcile_order_fill(
                db, order_row=order_row, mode=mode,
                user_id=strategy.user_id,
                broker=broker, auth_token=auth_token,
            )
            if order_row.avg_fill_price is not None:
                await _apply_fill_to_state(
                    run.id,
                    leg_id=leg_id,
                    order_kind="exit_signal",
                    avg_fill_price=float(order_row.avg_fill_price),
                    filled_qty=int(order_row.filled_qty or qty),
                    strategy_legs=strategy.legs or [],
                )
        except Exception:
            logger.exception(
                "Fill recon failed for signal exit order %s", order_row.id,
            )
    repo.emit_leg_exit_placed(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id,
        leg_id=leg_id,
        symbol=order_data["symbol"],
        action=broker_action,
        qty=qty,
        kind="exit_signal",
        broker_order_id=broker_order_id,
    )
    if ok:
        leg_state["current_side"] = None
        leg_state["status"] = "closed"
        leg_state["exit_order_id"] = order_row.id
        await state_module.hydrate_run_state(run.id, state)

    return {
        "outcome": "exited" if ok else "rejected",
        "order_id": order_row.id,
        "broker_order_id": broker_order_id,
        "leg_id": leg_id,
        "run_id": run.id,
        "reject_reason": None if ok else order_row.reject_reason,
    }


async def signal_auto_square(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    mode: str,
    broker: str,
    auth_token: Optional[str],
    config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Square every open signal-mode leg at end-of-day, then finalize the run.

    Called from the scheduler's _fire_auto_stop when the strategy's
    auto_stop_time IST cron tick fires (or, fallback, when exit_time
    fires for intraday strategies without an explicit auto_stop_time).

    Sequential rather than parallel - design open question (section 9)
    resolved here: sequential preserves audit ordering (each exit's
    event lands before the next one places), keeps the broker-side
    request rate well under the typical 10/sec limit even with 10 legs,
    and lets one leg's failure surface in the log next to the right
    leg rather than collated with N other failures. The slowdown
    (~50-200ms per leg) is irrelevant at EOD when the market window
    is already over.

    Each leg's exit goes through ``exit_leg_by_signal``, which carries
    the same direction-gate + state-check + silent-no-op contract that
    a manual long_exit/short_exit signal does. That means:
      - legs already flat (status='closed') are no-op'd cleanly
      - legs in the wrong direction relative to the strategy's
        current direction setting are skipped (operator must widen
        direction to clean up legacy positions)

    Idempotent: a second auto-square call on an already-squared run
    finalizes nothing extra (finalize_run is guarded by run.stopped_at).
    """
    # Lock the strategy row to serialize against concurrent webhook
    # signals - if a long_exit arrives while we're auto-squaring, the
    # caller waits behind us and then finds nothing to exit.
    locked = await _signal_lock_strategy(db, strategy.id)
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    strategy = locked

    if strategy.current_run_id is None:
        return {"outcome": "no_active_run", "leg_summaries": []}
    run = await db.get(SmStrategyRun, strategy.current_run_id)
    if run is None or run.stopped_at is not None:
        return {"outcome": "already_stopped", "leg_summaries": []}

    state = await state_module.get_run_state(run.id)
    open_legs: list[tuple[int, str]] = []
    for lid_str, leg in (state or {}).get("legs", {}).items():
        side = leg.get("current_side")
        if side == "long":
            open_legs.append((int(lid_str), "long_exit"))
        elif side == "short":
            open_legs.append((int(lid_str), "short_exit"))

    cfg_by_leg = {int(leg["id"]): leg for leg in (strategy.legs or [])}
    summaries: list[dict[str, Any]] = []
    for leg_id, exit_action in open_legs:
        leg_config = cfg_by_leg.get(leg_id)
        if leg_config is None:
            # Leg appeared in state but isn't on the strategy config.
            # Should be unreachable; log and skip rather than crash.
            logger.warning(
                "signal_auto_square: leg %d in state but missing from "
                "strategy %d.legs - skipping",
                leg_id, strategy.id,
            )
            continue
        try:
            result = await exit_leg_by_signal(
                db, strategy=strategy, leg_config=leg_config,
                action=exit_action, mode=mode, broker=broker,
                auth_token=auth_token, config=config,
                bypass_window=True,
            )
        except Exception:
            logger.exception(
                "signal_auto_square: exit_leg_by_signal raised for "
                "strategy %d leg %d", strategy.id, leg_id,
            )
            summaries.append({"leg_id": leg_id, "outcome": "error"})
            continue
        # Re-lock for the next iteration - exit_leg_by_signal commits
        # its transaction (via record_order) and releases the row lock.
        locked = await _signal_lock_strategy(db, strategy.id)
        if locked is not None:
            strategy = locked
        summaries.append({"leg_id": leg_id, **result})

    # Finalize the run regardless of per-leg outcomes - the trading
    # window is closed, and any legs still open are operator-cleanup.
    try:
        await repo.finalize_run(
            db, run=run, strategy=strategy, stop_reason="eod",
        )
    except Exception:
        logger.exception(
            "signal_auto_square: finalize_run raised for strategy %d run %d",
            strategy.id, run.id,
        )

    # Clear Redis state and unsubscribe ticks - same teardown as
    # batch-mode stop_run.
    try:
        await state_module.clear_run_state(run.id)
    except Exception:
        logger.exception(
            "signal_auto_square: clear_run_state raised for run %d", run.id,
        )
    try:
        tick_feed.remove_run_subscriptions(run.id)
    except Exception:
        logger.exception(
            "signal_auto_square: tick unsubscribe raised for run %d", run.id,
        )

    return {
        "outcome": "squared",
        "run_id": run.id,
        "stop_reason": "eod",
        "leg_summaries": summaries,
    }


# ---------------------------------------------------------------------------
# Kill switch (per-strategy isolation)
# ---------------------------------------------------------------------------
#
# Operator-triggered emergency stop. Walks the current run's pending orders
# and cancels each, then flattens open positions through the kind-appropriate
# path (stop_run for batch, signal_auto_square for signal), then flips
# webhook_locked=True so the webhook handler rejects every subsequent
# signal until the operator explicitly unlocks.
#
# Idempotent: re-firing the kill on an already-killed strategy is a no-op
# (no run -> nothing to cancel/flatten; webhook_locked is already True).
# ---------------------------------------------------------------------------


async def _cancel_pending_orders_for_run(
    db: AsyncSession,
    *,
    run: SmStrategyRun,
    strategy: SmStrategy,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Cancel every non-terminal order on the strategy's current run.

    Walks ``sm_strategy_order`` rows where ``status`` is not in the terminal
    set (``complete`` / ``rejected`` / ``cancelled``). For each, routes the
    cancel through the documented service surface (sandbox or live), records
    the new ``status='cancelled'`` on the order row, and returns per-order
    outcomes for the audit trail.

    Failures are isolated per-order so one stuck cancel doesn't abort the
    rest of the kill-switch sequence.
    """
    terminal = {"complete", "rejected", "cancelled"}
    orders = await repo.list_orders_for_run(
        db, user_id=strategy.user_id, run_id=run.id,
    )
    summaries: list[dict[str, Any]] = []
    for o in orders:
        if (o.status or "").lower() in terminal or not o.broker_order_id:
            continue
        try:
            if run.mode == "sandbox":
                from backend.services.sandbox_service import (
                    cancel_order as sbx_cancel,
                )
                ok, response, _ = sbx_cancel(strategy.user_id, o.broker_order_id)
            else:
                if not (auth_token and broker):
                    summaries.append({
                        "order_id": o.id,
                        "outcome": "skipped",
                        "note": "live cancel needs broker auth",
                    })
                    continue
                from backend.services.order_service import cancel_order_service
                ok, response, _ = cancel_order_service(
                    o.broker_order_id, auth_token, broker, config,
                )
            o.status = "cancelled" if ok else o.status
            o.reject_reason = (
                None if ok else (response.get("message") if isinstance(response, dict) else "cancel failed")
            )
            summaries.append({
                "order_id": o.id,
                "broker_order_id": o.broker_order_id,
                "outcome": "cancelled" if ok else "cancel_failed",
                "note": None if ok else o.reject_reason,
            })
        except Exception as e:
            logger.exception(
                "Kill switch: cancel raised for order %s on strategy %d",
                o.broker_order_id, strategy.id,
            )
            summaries.append({
                "order_id": o.id,
                "broker_order_id": o.broker_order_id,
                "outcome": "error",
                "note": repr(e),
            })
    if summaries:
        await db.commit()
    return summaries


async def kill_strategy(
    db: AsyncSession,
    *,
    strategy: SmStrategy,
    auth_token: Optional[str],
    broker: Optional[str],
    config: Optional[dict[str, Any]],
    triggered_by: str = "manual",
) -> dict[str, Any]:
    """Per-strategy kill switch.

    Three actions, in order:
      1. Cancel every pending/open order on the current run (sandbox or
         broker via the documented service surfaces).
      2. Flatten remaining open positions: ``stop_run`` for batch-mode
         strategies, ``signal_auto_square`` for signal-mode.
      3. Flip ``webhook_locked=True`` so the webhook handler refuses
         every subsequent action for this strategy.

    Idempotent: safe to invoke when the strategy is already stopped or
    already locked. ``triggered_by`` is one of ``'manual'`` (operator
    pressed the button), ``'daily_loss_limit'`` (engine auto-trigger),
    or ``'panic'`` (user-level panic button). Lands in the
    KillSwitchActivatedEvent payload for forensics.

    Returns a structured summary the router shapes into the HTTP
    response.
    """
    # Lock the strategy row first - same TOCTOU guard the entry/exit
    # paths use. Defends against a webhook signal arriving while the
    # operator presses the kill button.
    locked = await _signal_lock_strategy(db, strategy.id)
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    strategy = locked

    already_locked = bool(getattr(strategy, "webhook_locked", False))
    cancelled: list[dict[str, Any]] = []
    flatten_result: dict[str, Any] = {"outcome": "no_run"}
    run: Optional[SmStrategyRun] = None

    # Step 1 + 2: only meaningful when a run is active.
    if strategy.current_run_id is not None:
        run = await db.get(SmStrategyRun, strategy.current_run_id)
        if run is not None and run.stopped_at is None:
            cancelled = await _cancel_pending_orders_for_run(
                db, run=run, strategy=strategy,
                auth_token=auth_token, broker=broker, config=config,
            )
            try:
                kind = getattr(strategy, "strategy_kind", "batch") or "batch"
                if kind == "signal":
                    flatten_result = await signal_auto_square(
                        db, strategy=strategy, mode=run.mode, broker=broker or run.broker,
                        auth_token=auth_token, config=config,
                    )
                else:
                    flatten_result = await stop_run(
                        db, strategy=strategy, stop_reason="manual",
                        auth_token=auth_token, broker=broker, config=config,
                    )
            except EngineError as e:
                # Already stopped (race against another concurrent stop) -
                # acceptable, continue to lock the webhook.
                flatten_result = {"outcome": "already_stopped", "note": str(e)}
            except Exception:
                logger.exception(
                    "Kill switch: flatten raised for strategy %d", strategy.id,
                )
                flatten_result = {"outcome": "flatten_error"}

    # Step 3: flip the lock. Re-acquire the row lock - the flatten step
    # committed its own transaction and released the SELECT FOR UPDATE.
    re_locked = await _signal_lock_strategy(db, strategy.id)
    if re_locked is None:
        raise EngineError(f"Strategy {strategy.id} not found post-flatten")
    strategy = re_locked
    if not strategy.webhook_locked:
        strategy.webhook_locked = True
        await db.commit()
        await db.refresh(strategy)

    # Audit event - even on idempotent re-kill we emit one so the Events
    # tab shows every operator press, with payload distinguishing trigger.
    bus.publish(KillSwitchActivatedEvent(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id if run else None,
        severity="critical",
        message=(
            f"Kill switch activated ({triggered_by}). "
            f"{len(cancelled)} order(s) cancelled, flatten outcome: "
            f"{flatten_result.get('outcome')}"
        ),
        payload={
            "triggered_by": triggered_by,
            "already_locked_before": already_locked,
            "cancelled_orders": cancelled,
            "flatten_outcome": flatten_result.get("outcome"),
            "stop_reason": flatten_result.get("stop_reason"),
        },
    ))

    # WS: webhook_locked flipped - flow control UI elements need to know.
    try:
        from backend.strategy import broadcast as _bc
        if _bc.has_subscribers(strategy.id):
            _bc.push_strategy_update(strategy.id, {
                "id": strategy.id,
                "webhook_locked": True,
                "status": strategy.status,
                "current_run_id": strategy.current_run_id,
            })
    except Exception:
        logger.warning("kill_strategy: WS push failed", exc_info=True)

    return {
        "strategy_id": strategy.id,
        "webhook_locked": True,
        "cancelled_orders": cancelled,
        "flatten": flatten_result,
        "triggered_by": triggered_by,
    }


async def unlock_webhook(
    db: AsyncSession, *, strategy: SmStrategy,
) -> dict[str, Any]:
    """Clear the kill-switch lock. Strategy stays stopped - operator must
    manually start to resume entries.
    """
    locked = await _signal_lock_strategy(db, strategy.id)
    if locked is None:
        raise EngineError(f"Strategy {strategy.id} not found")
    strategy = locked
    if not strategy.webhook_locked:
        return {"strategy_id": strategy.id, "webhook_locked": False, "noop": True}
    strategy.webhook_locked = False
    await db.commit()
    await db.refresh(strategy)
    bus.publish(WebhookUnlockedEvent(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=strategy.current_run_id,
        severity="warn",
        message="Webhook lock cleared - strategy can accept signals again",
        payload={"unlock_method": "manual"},
    ))
    try:
        from backend.strategy import broadcast as _bc
        if _bc.has_subscribers(strategy.id):
            _bc.push_strategy_update(strategy.id, {
                "id": strategy.id,
                "webhook_locked": False,
            })
    except Exception:
        logger.warning("unlock_webhook: WS push failed", exc_info=True)
    return {"strategy_id": strategy.id, "webhook_locked": False, "noop": False}


# ---------------------------------------------------------------------------
# Fill reconciliation (slice A - fixes trader-experience gaps 1, 3, 4, 6)
#
# Without this, sm_strategy_order rows stay at status='open' / avg_fill_price=
# None forever even though the sandbox/broker has already filled the order
# at a real price. Result: the Live P&L card never gets entry_avg, the
# orderbook UI never flips from open->complete, and there's no executed
# price column to render in Positions.
#
# Sandbox path: re-read the order from backend.sandbox.order_manager and
# copy the truth into the strategy order row + Redis state. Synchronous -
# the sandbox already executed the fill inside sandbox_service.place_order
# (opportunistic fill at LTP, see sandbox_service.py:217).
#
# Live path: hit orderstatus_service.get_orderstatus_with_auth, which goes
# through the documented service surface. Async fills may not be ready
# immediately - the periodic poller (deferred slice D) handles those.
# ---------------------------------------------------------------------------


_CANONICAL_ORDER_STATUSES = {"open", "complete", "cancelled", "rejected", "trigger_pending"}


def _canonical_status(raw: Optional[str]) -> Optional[str]:
    """Normalize broker / sandbox status text to OpenBull's canonical set.

    Different brokers return different spellings - 'COMPLETED', 'filled',
    'EXECUTED' etc. - all of which mean the same thing. Map them so the
    orderbook UI sees a consistent vocabulary.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("complete", "completed", "filled", "executed"):
        return "complete"
    if s in ("rejected", "reject"):
        return "rejected"
    if s in ("cancelled", "canceled"):
        return "cancelled"
    if s in ("open", "pending", "submitted"):
        return "open"
    if s in ("trigger_pending", "trigger pending", "modified"):
        return "trigger_pending"
    return s if s in _CANONICAL_ORDER_STATUSES else None


def _reconcile_sandbox_order(
    user_id: int, order_row: SmStrategyOrder,
) -> bool:
    """Pull the truth from the sandbox row and copy it onto sm_strategy_order.

    Returns True when the row was updated. Idempotent - reading the same
    sandbox row twice is fine.
    """
    if not order_row.broker_order_id:
        return False
    try:
        from backend.sandbox import order_manager as sbx_orders
        sbx = sbx_orders.get_order(user_id, order_row.broker_order_id)
    except Exception:
        logger.exception(
            "fill-recon: sandbox lookup failed for order %s",
            order_row.broker_order_id,
        )
        return False
    if sbx is None:
        return False
    new_status = _canonical_status(getattr(sbx, "status", None))
    avg_price = getattr(sbx, "average_price", None)
    filled_qty = getattr(sbx, "filled_quantity", None)
    timestamp = getattr(sbx, "order_timestamp", None)

    changed = False
    if new_status and new_status != order_row.status:
        order_row.status = new_status
        changed = True
    if avg_price is not None and float(avg_price) > 0:
        if order_row.avg_fill_price is None or float(order_row.avg_fill_price) != float(avg_price):
            order_row.avg_fill_price = float(avg_price)
            changed = True
    if filled_qty is not None and (order_row.filled_qty or 0) != int(filled_qty):
        order_row.filled_qty = int(filled_qty)
        changed = True
    if new_status == "complete" and order_row.filled_at is None and timestamp is not None:
        # Sandbox order_timestamp is timezone-naive in some places; coerce
        # to UTC to match the column's timestamptz semantics.
        from backend.strategy.time_utils import now_utc
        order_row.filled_at = (
            timestamp if getattr(timestamp, "tzinfo", None) is not None else now_utc()
        )
        changed = True
    return changed


def _reconcile_live_order_via_service(
    order_row: SmStrategyOrder,
    *,
    broker: str,
    auth_token: str,
) -> bool:
    """Live-mode reconciliation through the documented orderstatus service.

    Same shape contract as the sandbox path. Failures are isolated so a
    transient broker-API blip doesn't abort the run lifecycle.
    """
    if not order_row.broker_order_id:
        return False
    try:
        from backend.services.orderstatus_service import (
            get_orderstatus_with_auth,
        )
        ok, response, _status = get_orderstatus_with_auth(
            order_row.broker_order_id, auth_token, broker,
        )
    except Exception:
        logger.exception(
            "fill-recon: live status fetch raised for order %s on %s",
            order_row.broker_order_id, broker,
        )
        return False
    if not ok or not isinstance(response, dict):
        return False
    data = response.get("data") or {}
    new_status = _canonical_status(data.get("order_status") or data.get("status"))
    avg_price = data.get("average_price")
    filled_qty = data.get("filled_quantity") or data.get("filledqty")

    changed = False
    if new_status and new_status != order_row.status:
        order_row.status = new_status
        changed = True
    try:
        if avg_price is not None and float(avg_price) > 0:
            if order_row.avg_fill_price is None or float(order_row.avg_fill_price) != float(avg_price):
                order_row.avg_fill_price = float(avg_price)
                changed = True
    except (TypeError, ValueError):
        pass
    try:
        if filled_qty is not None and (order_row.filled_qty or 0) != int(filled_qty):
            order_row.filled_qty = int(filled_qty)
            changed = True
    except (TypeError, ValueError):
        pass
    if new_status == "complete" and order_row.filled_at is None:
        from backend.strategy.time_utils import now_utc
        order_row.filled_at = now_utc()
        changed = True
    return changed


async def reconcile_order_fill(
    db: AsyncSession,
    *,
    order_row: SmStrategyOrder,
    mode: str,
    user_id: int,
    broker: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> bool:
    """Reconcile a single sm_strategy_order row against the live truth.

    Commits the change when something was updated. Idempotent - calling
    multiple times is safe.

    Also pushes a second ``order_update`` WS frame (the first fires from
    ``repo.record_order`` at placement time) so the client sees the
    status flip from 'open' to 'complete' + executed price land without
    waiting for a poll.
    """
    if mode == "sandbox":
        changed = _reconcile_sandbox_order(user_id, order_row)
    elif mode == "live" and broker and auth_token:
        changed = _reconcile_live_order_via_service(
            order_row, broker=broker, auth_token=auth_token,
        )
    else:
        return False
    if changed:
        await db.commit()
        await db.refresh(order_row)
        try:
            from backend.strategy import broadcast
            run = await db.get(SmStrategyRun, order_row.run_id)
            if run is not None and broadcast.has_subscribers(run.strategy_id):
                broadcast.push_order_update(
                    run.strategy_id, broadcast.format_order_row(order_row),
                )
        except Exception:
            logger.warning("reconcile_order_fill: WS push failed", exc_info=True)
    return changed


async def _apply_fill_to_state(
    run_id: int,
    *,
    leg_id: int,
    order_kind: str,
    avg_fill_price: float,
    filled_qty: int,
    strategy_legs: list[dict[str, Any]],
) -> None:
    """Mirror a filled order's price/qty into Redis run state.

    Entry fills set ``leg.entry_avg`` and bump ``status='open'``. Exit
    fills compute realized P&L = ``(exit - entry) * qty * sign(side)``,
    clear current_side, and set ``status='closed'``. Strategy-level
    aggregates ``pnl_realized`` / ``pnl_unrealized`` are recomputed from
    the per-leg numbers so the Live P&L card has real data to render.
    """
    state = await state_module.get_run_state(run_id)
    if state is None:
        return
    legs = state.setdefault("legs", {})
    key = str(leg_id)
    leg = legs.get(key)
    if leg is None:
        leg = {"leg_id": leg_id, "status": "open"}
        legs[key] = leg

    if order_kind == "entry":
        leg["entry_avg"] = avg_fill_price
        leg["qty"] = filled_qty
        leg["status"] = "open"
        # For signal-mode legs the current_side has already been set by
        # enter_leg before the dispatch; for batch-mode legs we look at
        # the per-leg config's 'position' (B/S) to derive the side.
        if not leg.get("current_side"):
            cfg = next(
                (c for c in strategy_legs if int(c.get("id", -1)) == leg_id),
                None,
            )
            if cfg and cfg.get("position") == "S":
                leg["current_side"] = "short"
            elif cfg and cfg.get("position") == "B":
                leg["current_side"] = "long"
    else:
        # Any non-entry kind is treated as an exit for P&L purposes.
        entry_avg = leg.get("entry_avg")
        side = leg.get("current_side")
        if entry_avg is not None and side in ("long", "short"):
            sign = 1 if side == "long" else -1
            realized = (float(avg_fill_price) - float(entry_avg)) * filled_qty * sign
            leg["realized_pnl"] = float(leg.get("realized_pnl") or 0.0) + realized
        leg["exit_avg"] = avg_fill_price
        leg["status"] = "closed"
        leg["current_side"] = None
        leg["mtm"] = 0.0  # closed legs contribute 0 to unrealized

    # Recompute strategy-level aggregates.
    realized_total = 0.0
    unrealized_total = 0.0
    for lg in legs.values():
        realized_total += float(lg.get("realized_pnl") or 0.0)
        if lg.get("status") == "open":
            unrealized_total += float(lg.get("mtm") or 0.0)
    state["pnl_realized"] = realized_total
    state["pnl_unrealized"] = unrealized_total
    state["pnl_total"] = realized_total + unrealized_total
    state["pnl_peak"] = max(float(state.get("pnl_peak") or 0.0), state["pnl_total"])
    state["pnl_trough"] = min(float(state.get("pnl_trough") or 0.0), state["pnl_total"])

    await state_module.hydrate_run_state(run_id, state)
