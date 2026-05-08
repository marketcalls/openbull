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

from backend.config import get_settings
from backend.dependencies import get_current_user, get_db
from backend.models.strategy_module import SmStrategy
from backend.models.user import User
from backend.schemas.strategy_module import (
    StrategyCreate,
    StrategyCreateResponse,
    StrategyListItem,
    StrategyOut,
    StrategyUpdate,
)
from backend.strategy import repository as repo, symbol_resolver
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
        universe_tab=row.universe_tab,
        underlying=row.underlying,
        strategy_type=row.strategy_type,
        status=row.status,
        live_enabled=row.live_enabled,
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
