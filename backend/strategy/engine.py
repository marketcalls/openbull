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

from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyOrder,
    SmStrategyRun,
)
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
    # baked in by the wizard for futures legs; raw shares for cash.
    qty = int(leg_config["qty"])
    if qty <= 0:
        raise EngineError(f"Leg {leg_id}: qty must be > 0 (got {qty!r})")

    order_data = {
        "symbol": leg_config["symbol"],
        "exchange": leg_config["exchange"],
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
        symbol=leg_config["symbol"],
        exchange=leg_config["exchange"],
        action=broker_action,
        qty=qty,
        pricetype=strategy.pricetype or "MARKET",
        broker_order_id=broker_order_id,
        status="open" if ok else "rejected",
        reject_reason=None if ok else (response.get("message") if isinstance(response, dict) else "rejected"),
    )

    # Audit event + leg state update.
    repo.emit_leg_entry_placed(
        user_id=strategy.user_id,
        strategy_id=strategy.id,
        run_id=run.id,
        leg_id=leg_id,
        symbol=leg_config["symbol"],
        action=broker_action,
        qty=qty,
        broker_order_id=broker_order_id,
    )
    if ok:
        leg_state["current_side"] = requested_side
        leg_state["qty"] = qty
        leg_state["symbol"] = leg_config["symbol"]
        leg_state["exchange"] = leg_config["exchange"]
        leg_state["entry_order_id"] = order_row.id
        leg_state["status"] = "open"
        leg_state["exit_order_id"] = None
    else:
        # Order rejected at broker/sandbox. Leg stays flat. Surface the
        # reject_reason so the operator can investigate from the UI.
        leg_state["status"] = "rejected"
    await state_module.hydrate_run_state(run.id, state)

    # Tick subscription for risk eval - same pattern as batch start_run.
    if ok:
        try:
            tick_feed.add_run_subscriptions(
                run.id,
                [(leg_config["exchange"], leg_config["symbol"])],
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
) -> dict[str, Any]:
    """Close one signal-mode leg in response to long_exit or short_exit.

    Silent no-op (returns ``outcome='no_matching_position'``) when the
    leg isn't currently in the requested direction. Matches design 4.4
    so repeat exit alerts for an already-flat leg are idempotent.
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
