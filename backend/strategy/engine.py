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
    if strategy.status != "stopped":
        raise EngineError(f"Cannot start — strategy is currently '{strategy.status}'")

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
        resolved_legs.append({
            "leg_id": leg["id"],
            "position": leg["position"],
            "lots": leg["lots"],
            "symbol": r["symbol"],
            "exchange": r["exchange"],
            "lotsize": r["lotsize"] or 1,
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
