"""Strategy module — Phase 1 CRUD router.

Session-cookie auth (matches the legacy ``/web/strategies`` convention).
External API-key access via ``/api/v1/strategy/*`` is a future phase.

Endpoints:

* ``GET    /web/strategy``                            — list (filters: status, universe_tab)
* ``GET    /web/strategy/{id}``                       — fetch one
* ``POST   /web/strategy``                            — create (returns one-time webhook token)
* ``PATCH  /web/strategy/{id}``                       — partial update (only when status=stopped)
* ``DELETE /web/strategy/{id}``                       — hard delete (only when status=stopped)
* ``POST   /web/strategy/{id}/rotate_webhook_token``  — issue a fresh token

Higher-risk lifecycle endpoints (start, stop, close_all, per-leg close,
webhook receiver) ship in later phases when the engine exists.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, ConfigDict, Field

from backend.config import get_settings
from backend.dependencies import get_broker_context, get_current_user, get_db, BrokerContext
from backend.models.strategy_module import SmStrategy
from backend.models.user import User
from backend.schemas.strategy_module import (
    StrategyCreate,
    StrategyCreateResponse,
    StrategyListItem,
    StrategyOut,
    StrategyUpdate,
)
from backend.strategy import engine, repository as repo, symbol_resolver
from backend.strategy.time_utils import format_ist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/strategy", tags=["strategy-module"])


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _webhook_url(request: Request, plaintext_token: str) -> str:
    """Construct the public webhook URL for the strategy.

    Uses ``settings.public_url`` if set, else the request's own scheme+host.
    The token segment is the credential — never log this URL except in the
    one-time response on create/rotate.
    """
    base = getattr(get_settings(), "public_url", None)
    if not base:
        base = f"{request.url.scheme}://{request.url.netloc}"
    return f"{base.rstrip('/')}/webhook/strategy/{plaintext_token}"


def _strategy_out(row: SmStrategy, *, webhook_url: str) -> StrategyOut:
    """Hydrate a StrategyOut. Webhook URL is supplied separately because the
    plaintext token isn't stored — list endpoints render a placeholder URL.
    """
    return StrategyOut(
        id=row.id,
        name=row.name,
        strategy_kind=getattr(row, "strategy_kind", "batch") or "batch",
        direction=getattr(row, "direction", "both") or "both",
        universe_tab=row.universe_tab,
        underlying=row.underlying,
        underlying_exchange=row.underlying_exchange,
        strategy_type=row.strategy_type,
        entry_time=row.entry_time.isoformat() if row.entry_time else None,
        exit_time=row.exit_time.isoformat() if row.exit_time else None,
        product=row.product,
        pricetype=row.pricetype,
        legs=row.legs,
        overall_sl_mtm=float(row.overall_sl_mtm) if row.overall_sl_mtm is not None else None,
        overall_target_mtm=float(row.overall_target_mtm) if row.overall_target_mtm is not None else None,
        lock_profit=row.lock_profit,
        trail_sl_to_entry=row.trail_sl_to_entry,
        scheduler=row.scheduler,
        live_enabled=row.live_enabled,
        webhook_url=webhook_url,
        webhook_ip_allowlist=row.webhook_ip_allowlist,
        webhook_locked=bool(getattr(row, "webhook_locked", False)),
        daily_loss_limit_inr=float(row.daily_loss_limit_inr) if row.daily_loss_limit_inr is not None else None,
        status=row.status,
        current_run_id=row.current_run_id,
        created_at=format_ist(row.created_at),
        updated_at=format_ist(row.updated_at),
    )


def _strategy_list_item(row: SmStrategy) -> StrategyListItem:
    return StrategyListItem(
        id=row.id,
        name=row.name,
        strategy_kind=getattr(row, "strategy_kind", "batch") or "batch",
        direction=getattr(row, "direction", "both") or "both",
        universe_tab=row.universe_tab,
        underlying=row.underlying,
        strategy_type=row.strategy_type,
        status=row.status,
        live_enabled=row.live_enabled,
        webhook_locked=bool(getattr(row, "webhook_locked", False)),
        # Phase 1 has no engine yet — P&L surfaces zero. Phase 6 wires live
        # values via Redis state lookup.
        pnl_realized=0.0,
        pnl_unrealized=0.0,
        pnl_total=0.0,
        created_at=format_ist(row.created_at),
        updated_at=format_ist(row.updated_at),
    )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_strategies(
    status: Optional[str] = Query(None),
    universe_tab: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await repo.list_strategies(
        db, user_id=user.id, status=status, universe_tab=universe_tab
    )
    return {
        "status": "success",
        "strategies": [_strategy_list_item(r).model_dump() for r in rows],
    }


@router.get("/{strategy_id}")
async def get_strategy(
    strategy_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    # Token plaintext is unrecoverable — show a masked URL on read.
    masked_url = _webhook_url(request, "[token-hidden]")
    return {"status": "success", "strategy": _strategy_out(row, webhook_url=masked_url).model_dump()}


@router.post("", status_code=201)
async def create_strategy(
    payload: StrategyCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    body = payload.model_dump(mode="python")
    try:
        row, plaintext_token = await repo.create_strategy(
            db, user_id=user.id, payload=body
        )
    except repo.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    webhook_url = _webhook_url(request, plaintext_token)
    logger.info(
        "user=%d created strategy id=%d name=%r underlying=%s",
        user.id, row.id, row.name, row.underlying,
    )
    return StrategyCreateResponse(
        strategy=_strategy_out(row, webhook_url=webhook_url),
        webhook_token=plaintext_token,
    ).model_dump()


@router.patch("/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    patch: StrategyUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    patch_dict = patch.model_dump(mode="python", exclude_unset=True)
    if not patch_dict:
        raise HTTPException(status_code=400, detail="Empty patch")
    try:
        row = await repo.update_strategy(
            db, user_id=user.id, strategy_id=strategy_id, patch=patch_dict
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    except repo.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    masked_url = _webhook_url(request, "[token-hidden]")
    return {"status": "success", "strategy": _strategy_out(row, webhook_url=masked_url).model_dump()}


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await repo.delete_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    except repo.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    logger.info("user=%d deleted strategy id=%d", user.id, strategy_id)
    return Response(status_code=204)


@router.post("/{strategy_id}/rotate_webhook_token")
async def rotate_webhook_token(
    strategy_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        row, plaintext_token = await repo.rotate_webhook_token(
            db, user_id=user.id, strategy_id=strategy_id
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    webhook_url = _webhook_url(request, plaintext_token)
    return StrategyCreateResponse(
        strategy=_strategy_out(row, webhook_url=webhook_url),
        webhook_token=plaintext_token,
    ).model_dump()


# ---------------------------------------------------------------------------
# Phase 3 helper endpoints — power the wizard's underlying / expiry / strike
# pickers. All session-cookie authed; thin wrappers over symbol_resolver.
# ---------------------------------------------------------------------------


@router.get("/underlyings")
async def list_underlyings(
    universe_tab: str = Query(..., description="weekly_monthly | monthly_only | stocks_fno | mcx | delta"),
    user: User = Depends(get_current_user),
):
    ok, data, status_code = symbol_resolver.list_underlyings_for_tab(universe_tab)
    if not ok:
        raise HTTPException(status_code=status_code, detail=data.get("message", "Failed"))
    return data


@router.get("/expiries")
async def list_expiries(
    underlying: str = Query(..., min_length=1, max_length=50),
    underlying_exchange: str = Query(..., min_length=1, max_length=20),
    instrument: str = Query("options", pattern="^(options|futures)$"),
    user: User = Depends(get_current_user),
):
    """Sorted expiry dates for an underlying (DD-MMM-YY).

    Thin wrapper over the platform-wide get_expiry_dates so the wizard can
    use session-cookie auth instead of the API-keyed ``/api/v1/expiry``.
    """
    ok, data, status_code = symbol_resolver.list_expiries(
        underlying, underlying_exchange, instrument,
    )
    if not ok:
        raise HTTPException(status_code=status_code, detail=data.get("message", "Failed"))
    return data


@router.get("/strikes")
async def list_strikes(
    underlying: str = Query(..., min_length=1, max_length=50),
    underlying_exchange: str = Query(..., min_length=1, max_length=20),
    expiry: Optional[str] = Query(
        None,
        description="Concrete expiry like 28-MAY-26 or 28MAY26. If omitted, expiry_rank is required.",
    ),
    expiry_rank: Optional[str] = Query(
        None,
        pattern="^(weekly|monthly|current|next)$",
        description="Resolves to a date via symbol_resolver.resolve_expiry_rank.",
    ),
    option_type: str = Query(..., pattern="^(CE|PE)$"),
    user: User = Depends(get_current_user),
):
    """Sorted strikes for an option chain.

    Caller can pass either a concrete ``expiry`` or an ``expiry_rank``; one
    of the two is required. Resolved expiry is included in the response so
    the wizard knows which date the strikes belong to.
    """
    if not expiry and not expiry_rank:
        raise HTTPException(status_code=400, detail="Either expiry or expiry_rank is required")

    if not expiry:
        # Resolve rank to a real date first.
        ok_e, expiries, sc_e = symbol_resolver.list_expiries(
            underlying, underlying_exchange, "options",
        )
        if not ok_e:
            raise HTTPException(status_code=sc_e, detail=expiries.get("message", "Expiries unavailable"))
        resolved, _ = symbol_resolver.resolve_expiry_rank(
            expiry_rank or "weekly", expiries.get("data", []),
        )
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail=f"No expiry found for rank '{expiry_rank}' on {underlying}",
            )
        expiry = resolved

    ok, data, status_code = symbol_resolver.list_strikes(
        underlying=underlying,
        underlying_exchange=underlying_exchange,
        expiry_date=expiry,
        option_type=option_type,
    )
    if not ok:
        raise HTTPException(status_code=status_code, detail=data.get("message", "Failed"))
    return data


# ---------------------------------------------------------------------------
# Phase 4 — lifecycle (start / stop / close_all / per-leg close) + scoped views
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(..., pattern="^(live|sandbox)$")


def _format_order(o) -> dict:
    return {
        "id": o.id,
        "leg_id": o.leg_id,
        "kind": o.kind,
        "broker_order_id": o.broker_order_id,
        "symbol": o.symbol,
        "exchange": o.exchange,
        "action": o.action,
        "qty": o.qty,
        "pricetype": o.pricetype,
        "price": float(o.price) if o.price is not None else 0.0,
        "trigger_price": float(o.trigger_price) if o.trigger_price is not None else 0.0,
        "status": o.status,
        "placed_at": format_ist(o.placed_at),
        "filled_at": format_ist(o.filled_at),
        "avg_fill_price": float(o.avg_fill_price) if o.avg_fill_price is not None else None,
        "filled_qty": o.filled_qty,
        "reject_reason": o.reject_reason,
    }


def _format_run(r) -> dict:
    return {
        "id": r.id,
        "strategy_id": r.strategy_id,
        "mode": r.mode,
        "broker": r.broker,
        "started_at": format_ist(r.started_at),
        "stopped_at": format_ist(r.stopped_at),
        "stop_reason": r.stop_reason,
        "pnl_realized": float(r.pnl_realized) if r.pnl_realized is not None else 0.0,
        "pnl_peak": float(r.pnl_peak) if r.pnl_peak is not None else 0.0,
        "pnl_trough": float(r.pnl_trough) if r.pnl_trough is not None else 0.0,
        "trigger_source": r.trigger_source,
    }


def _format_event(e) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "ts": format_ist(e.ts),
        "kind": e.kind,
        "severity": e.severity,
        "leg_id": e.leg_id,
        "message": e.message,
        "payload": e.payload,
    }


@router.post("/{strategy_id}/start")
async def start_run(
    strategy_id: int,
    payload: StartRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    broker_ctx: BrokerContext = Depends(get_broker_context),
):
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if payload.mode == "live" and not strategy.live_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Live mode is not enabled on this strategy. Enable it from "
                "the detail page after re-authenticating."
            ),
        )

    try:
        run, legs = await engine.start_run(
            db,
            strategy=strategy,
            mode=payload.mode,
            broker=broker_ctx.broker_name,
            auth_token=broker_ctx.auth_token,
            config=broker_ctx.broker_config,
            trigger_source="manual",
        )
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("strategy %d: start_run failed", strategy_id)
        raise HTTPException(status_code=500, detail="Failed to start run")

    return {
        "status": "success",
        "run": _format_run(run),
        "legs": legs,
    }


@router.post("/{strategy_id}/stop")
async def stop_run_endpoint(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    broker_ctx: BrokerContext = Depends(get_broker_context),
):
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        result = await engine.stop_run(
            db,
            strategy=strategy,
            stop_reason="manual",
            auth_token=broker_ctx.auth_token,
            broker=broker_ctx.broker_name,
            config=broker_ctx.broker_config,
        )
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **result}


@router.post("/{strategy_id}/close_all")
async def close_all_endpoint(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    broker_ctx: BrokerContext = Depends(get_broker_context),
):
    """Strategy-level Close All — same effect as /stop, but the audit trail
    differentiates the two (close_all_manual event vs run_stopped event)
    via the differentiated UI button."""
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        result = await engine.stop_run(
            db,
            strategy=strategy,
            stop_reason="manual",
            auth_token=broker_ctx.auth_token,
            broker=broker_ctx.broker_name,
            config=broker_ctx.broker_config,
        )
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **result}


@router.post("/{strategy_id}/kill_switch")
async def kill_switch_endpoint(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    broker_ctx: BrokerContext = Depends(get_broker_context),
):
    """Kill switch — cancel pending orders, flatten positions, and lock
    the webhook so external TradingView signals are refused until the
    operator explicitly unlocks. Idempotent: re-pressing on an already-
    killed strategy is a no-op (still emits the audit event).
    """
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        result = await engine.kill_strategy(
            db,
            strategy=strategy,
            auth_token=broker_ctx.auth_token,
            broker=broker_ctx.broker_name,
            config=broker_ctx.broker_config,
            triggered_by="manual",
        )
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **result}


@router.post("/{strategy_id}/unlock_webhook")
async def unlock_webhook_endpoint(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear the kill-switch lock. Strategy stays stopped - operator must
    manually press Start to resume entries. Webhook is back online for
    signal-mode strategies; the lock can be re-applied any time via
    /kill_switch.
    """
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        result = await engine.unlock_webhook(db, strategy=strategy)
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **result}


@router.post("/{strategy_id}/legs/{leg_id}/close")
async def close_leg_endpoint(
    strategy_id: int,
    leg_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    broker_ctx: BrokerContext = Depends(get_broker_context),
):
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        result = await engine.close_leg(
            db,
            strategy=strategy,
            leg_id=leg_id,
            auth_token=broker_ctx.auth_token,
            broker=broker_ctx.broker_name,
            config=broker_ctx.broker_config,
        )
    except engine.EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **result}


@router.get("/{strategy_id}/orders")
async def get_strategy_orders(
    strategy_id: int,
    run_id: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if run_id is not None:
        orders = await repo.list_orders_for_run(
            db, user_id=user.id, run_id=run_id,
        )
    else:
        orders = await repo.list_orders_for_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    return {
        "status": "success",
        "orders": [_format_order(o) for o in orders],
    }


@router.get("/{strategy_id}/positions")
async def get_strategy_positions(
    strategy_id: int,
    run_id: Optional[int] = Query(
        None,
        description=(
            "Run to scope positions to. Defaults to current_run_id when "
            "the strategy is running, latest run otherwise."
        ),
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Strategy-scoped positions view.

    Aggregates filled orders for the chosen run into per-symbol
    positions. ``net_qty`` is signed: positive=long, negative=short,
    0=flat (still returned so the operator can see the round-trip
    realized P&L from a fully-exited leg).

    Each position row carries:
      - ``symbol`` / ``exchange``
      - ``net_qty``                  signed share/contract count
      - ``side``                     'long' / 'short' / 'flat'
      - ``avg_entry_price``          weighted average of entry fills
      - ``avg_exit_price``           weighted average of exit fills (or
                                     None if still open)
      - ``ltp``                      from MarketDataCache when available
      - ``unrealized_pnl``           (ltp - avg_entry) * net_qty * sign
      - ``realized_pnl``             locked-in from closed portion
      - ``product`` / ``last_kind``  for context
    """
    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Pick the run to scope to.
    resolved_run_id = run_id
    if resolved_run_id is None:
        resolved_run_id = strategy.current_run_id
    if resolved_run_id is None:
        # No run ever -> empty positions, not an error.
        latest = await repo.list_runs(
            db, user_id=user.id, strategy_id=strategy_id,
        )
        if latest:
            resolved_run_id = latest[0].id
    if resolved_run_id is None:
        return {"status": "success", "run_id": None, "positions": []}

    orders = await repo.list_orders_for_run(
        db, user_id=user.id, run_id=resolved_run_id,
    )

    # Aggregate per (symbol, exchange, product).
    from backend.services.market_data_cache import get_market_data_cache
    cache = get_market_data_cache()

    aggs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for o in orders:
        if (o.status or "").lower() != "complete":
            continue
        fill_qty = int(o.filled_qty or o.qty or 0)
        fill_price = float(o.avg_fill_price or 0)
        if fill_qty <= 0 or fill_price <= 0:
            continue
        key = (o.symbol, o.exchange, strategy.product)
        a = aggs.setdefault(key, {
            "symbol": o.symbol,
            "exchange": o.exchange,
            "product": strategy.product,
            "buy_qty": 0,
            "buy_value": 0.0,
            "sell_qty": 0,
            "sell_value": 0.0,
            "last_kind": o.kind,
            "last_action_at": o.placed_at,
        })
        signed = fill_qty if (o.action or "").upper() == "BUY" else -fill_qty
        if signed > 0:
            a["buy_qty"] += fill_qty
            a["buy_value"] += fill_qty * fill_price
        else:
            a["sell_qty"] += fill_qty
            a["sell_value"] += fill_qty * fill_price
        if o.placed_at and (a["last_action_at"] is None or o.placed_at > a["last_action_at"]):
            a["last_kind"] = o.kind
            a["last_action_at"] = o.placed_at

    positions: list[dict[str, Any]] = []
    for a in aggs.values():
        net_qty = a["buy_qty"] - a["sell_qty"]
        avg_buy = (a["buy_value"] / a["buy_qty"]) if a["buy_qty"] > 0 else 0.0
        avg_sell = (a["sell_value"] / a["sell_qty"]) if a["sell_qty"] > 0 else 0.0
        # Closed-portion realized P&L: min(buy_qty, sell_qty) units round-
        # tripped. For pure-long round-trips: realized = (avg_sell -
        # avg_buy) * matched_qty. For pure-short: (avg_sell - avg_buy) *
        # matched_qty with sell as the opener, so the same formula
        # surfaces the correct sign.
        matched = min(a["buy_qty"], a["sell_qty"])
        realized = (avg_sell - avg_buy) * matched if matched > 0 else 0.0

        # LTP from cache - only available when something has subscribed
        # the symbol on the broker WS feed. The Live tab subscribes via
        # tick_feed.add_run_subscriptions on each entry, but the broker
        # WS adapter must also be subscribed for ticks to land here.
        ltp_entry = cache.get_ltp(a["symbol"], a["exchange"]) or {}
        ltp_val = ltp_entry.get("value") if isinstance(ltp_entry, dict) else None
        try:
            ltp_f = float(ltp_val) if ltp_val is not None else None
        except (TypeError, ValueError):
            ltp_f = None

        if net_qty == 0:
            side = "flat"
            avg_entry = avg_buy if a["buy_qty"] > 0 else avg_sell
            unrealized = 0.0
        elif net_qty > 0:
            side = "long"
            avg_entry = avg_buy
            unrealized = ((ltp_f - avg_entry) * net_qty) if ltp_f else 0.0
        else:
            side = "short"
            avg_entry = avg_sell
            unrealized = ((avg_entry - ltp_f) * abs(net_qty)) if ltp_f else 0.0

        positions.append({
            "symbol": a["symbol"],
            "exchange": a["exchange"],
            "product": a["product"],
            "net_qty": net_qty,
            "side": side,
            "avg_entry_price": round(avg_entry, 4),
            "avg_exit_price": round(avg_sell if side == "long" else avg_buy, 4) if matched > 0 else None,
            "ltp": ltp_f,
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(realized, 2),
            "last_kind": a["last_kind"],
        })

    # Strategy-level totals as a convenience.
    tot_realized = sum(p["realized_pnl"] for p in positions)
    tot_unrealized = sum(p["unrealized_pnl"] for p in positions)
    return {
        "status": "success",
        "run_id": resolved_run_id,
        "positions": positions,
        "summary": {
            "realized": round(tot_realized, 2),
            "unrealized": round(tot_unrealized, 2),
            "total": round(tot_realized + tot_unrealized, 2),
        },
    }


@router.get("/{strategy_id}/tradebook")
async def get_strategy_tradebook(
    strategy_id: int,
    run_id: Optional[int] = Query(
        None,
        description="Filter to a specific run. Default: all runs.",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Strategy-scoped tradebook. Returns every filled order as a trade.

    Each row carries Time (IST), Run, Kind, Symbol, Exchange, Action,
    Filled Qty, Executed Price, Trade Value, Order ID.
    """
    try:
        await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if run_id is not None:
        orders = await repo.list_orders_for_run(
            db, user_id=user.id, run_id=run_id,
        )
    else:
        orders = await repo.list_orders_for_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )

    trades: list[dict[str, Any]] = []
    for o in orders:
        if (o.status or "").lower() != "complete":
            continue
        fill_qty = int(o.filled_qty or o.qty or 0)
        fill_price = float(o.avg_fill_price or 0)
        if fill_qty <= 0 or fill_price <= 0:
            continue
        trades.append({
            "order_id": o.id,
            "run_id": o.run_id,
            "leg_id": o.leg_id,
            "kind": o.kind,
            "symbol": o.symbol,
            "exchange": o.exchange,
            "action": o.action,
            "filled_qty": fill_qty,
            "avg_fill_price": round(fill_price, 2),
            "trade_value": round(fill_qty * fill_price, 2),
            "broker_order_id": o.broker_order_id,
            "filled_at": format_ist(o.filled_at) if o.filled_at else format_ist(o.placed_at),
        })

    # Newest first.
    trades.sort(key=lambda t: t["filled_at"] or "", reverse=True)
    return {"status": "success", "trades": trades}


@router.get("/{strategy_id}/runs")
async def get_strategy_runs(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    runs = await repo.list_runs(db, user_id=user.id, strategy_id=strategy_id)
    return {"status": "success", "runs": [_format_run(r) for r in runs]}


@router.get("/{strategy_id}/events")
async def get_strategy_events(
    strategy_id: int,
    run_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    events = await repo.list_events_for_strategy(
        db, user_id=user.id, strategy_id=strategy_id, run_id=run_id, limit=limit,
    )
    return {"status": "success", "events": [_format_event(e) for e in events]}


class EnableLiveRequest(BaseModel):
    """Password re-auth body for going live (plan Section 14.3)."""

    model_config = ConfigDict(extra="forbid")
    password: str = Field(..., min_length=1, max_length=200)


@router.post("/{strategy_id}/enable_live")
async def enable_live_mode(
    strategy_id: int,
    payload: EnableLiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flip ``strategy.live_enabled`` to true after password re-auth.

    Phase 10 lower bar than the plan's 5-min re-auth window — we always
    re-verify the password here, regardless of how recently the user
    logged in. The endpoint is idempotent (already-live is 200 ok),
    refuses to flip when the strategy is currently running.
    """
    from backend.events.strategy_events import LiveEnabledEvent
    from backend.security import verify_password
    from backend.utils.event_bus import bus

    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if strategy.status != "stopped":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot toggle live mode while strategy is '{strategy.status}'",
        )

    if not verify_password(payload.password, user.password_hash):
        # Generic error — no distinction between "user not found" and
        # "wrong password". Audit-log the failed attempt for forensics.
        logger.warning(
            "enable_live: password verification failed for user=%d strategy=%d",
            user.id, strategy_id,
        )
        bus.publish(LiveEnabledEvent(
            user_id=user.id,
            strategy_id=strategy.id,
            severity="warn",
            message="enable_live rejected — wrong password",
            payload={"action": "enable", "result": "rejected_password"},
        ))
        raise HTTPException(status_code=401, detail="Password verification failed")

    if strategy.live_enabled:
        return {"status": "success", "live_enabled": True, "note": "already enabled"}

    strategy.live_enabled = True
    await db.commit()
    await db.refresh(strategy)

    bus.publish(LiveEnabledEvent(
        user_id=user.id,
        strategy_id=strategy.id,
        severity="warn",  # always warn — flipping to live is a high-impact change
        message=f"Live mode enabled on '{strategy.name}'",
        payload={"action": "enable"},
    ))
    logger.info(
        "user=%d enabled LIVE mode on strategy=%d (%s)",
        user.id, strategy_id, strategy.name,
    )
    return {"status": "success", "live_enabled": True}


@router.post("/{strategy_id}/disable_live")
async def disable_live_mode(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flip ``strategy.live_enabled`` back to false. No password re-prompt
    on disable — the bar is intentionally lower than enabling. Refused
    while the strategy is currently running (manual /stop first)."""
    from backend.events.strategy_events import LiveEnabledEvent
    from backend.utils.event_bus import bus

    try:
        strategy = await repo.get_strategy(
            db, user_id=user.id, strategy_id=strategy_id,
        )
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if strategy.status != "stopped":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot toggle live mode while strategy is '{strategy.status}'",
        )
    if not strategy.live_enabled:
        return {"status": "success", "live_enabled": False, "note": "already disabled"}

    strategy.live_enabled = False
    await db.commit()
    await db.refresh(strategy)

    bus.publish(LiveEnabledEvent(
        user_id=user.id,
        strategy_id=strategy.id,
        severity="info",
        message=f"Live mode disabled on '{strategy.name}'",
        payload={"action": "disable"},
    ))
    logger.info(
        "user=%d disabled live mode on strategy=%d", user.id, strategy_id,
    )
    return {"status": "success", "live_enabled": False}


@router.get("/{strategy_id}/webhook_events")
async def get_strategy_webhook_events(
    strategy_id: int,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent webhook deliveries for this strategy — the Webhook tab UI
    reads this to show the audit log of TradingView hits.
    """
    from sqlalchemy import desc, select
    from backend.models.strategy_module import SmWebhookEvent

    try:
        await repo.get_strategy(db, user_id=user.id, strategy_id=strategy_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Strategy not found")
    rows = (await db.execute(
        select(SmWebhookEvent)
        .where(SmWebhookEvent.strategy_id == strategy_id)
        .order_by(desc(SmWebhookEvent.received_at))
        .limit(limit)
    )).scalars().all()
    return {
        "status": "success",
        "webhook_events": [
            {
                "id": r.id,
                "received_at": format_ist(r.received_at),
                "action": r.action,
                "mode": r.mode,
                "result": r.result,
                "ip": str(r.ip) if r.ip else None,
                "user_agent": r.user_agent,
                "error": r.error,
                "payload": r.payload,
            }
            for r in rows
        ],
    }
