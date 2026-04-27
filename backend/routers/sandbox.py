"""
Sandbox configuration UI endpoints.

Exposes the ``sandbox_config`` key/value store for the ``/sandbox`` React page
and a reset button. Admin-only — same gate as the trading-mode switch —
because changing capital/leverage affects every user's simulated state.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies import get_current_user
from backend.models.user import User
from backend.sandbox import config as sbx_config
from backend.sandbox import (
    fund_manager,
    order_manager,
    pnl_snapshot,
    squareoff,
    t1_settle,
    weekly_reset,
)
from backend.sandbox._db import session_scope
from backend.models.sandbox import (
    SandboxDailyPnL,
    SandboxHolding,
    SandboxOrder,
    SandboxPosition,
    SandboxTrade,
)
from sqlalchemy import delete, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/sandbox", tags=["sandbox"])


class ConfigUpdate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., max_length=500)


@router.get("/config")
async def list_configs(user: User = Depends(get_current_user)):
    """Return every sandbox_config row so the UI can render a settings form."""
    return {"status": "success", "data": sbx_config.get_all_configs()}


@router.post("/config")
async def update_config(
    payload: ConfigUpdate,
    user: User = Depends(get_current_user),
):
    """Update a single editable config row. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    ok = sbx_config.set_config(payload.key, payload.value)
    if not ok:
        raise HTTPException(
            status_code=400, detail="Unknown or non-editable config key"
        )
    return {"status": "success", "key": payload.key, "value": payload.value}


@router.post("/reset")
async def reset_my_sandbox(user: User = Depends(get_current_user)):
    """Wipe the caller's sandbox orders / trades / positions / holdings and
    reset funds to the current ``starting_capital``. Per-user — doesn't touch
    other users' sandbox state.
    """
    with session_scope() as db:
        db.execute(delete(SandboxOrder).where(SandboxOrder.user_id == user.id))
        db.execute(delete(SandboxTrade).where(SandboxTrade.user_id == user.id))
        db.execute(delete(SandboxPosition).where(SandboxPosition.user_id == user.id))
        db.execute(delete(SandboxHolding).where(SandboxHolding.user_id == user.id))
    fund_manager.reset_funds(user.id)
    logger.info("Sandbox reset by user %s (id=%d)", user.username, user.id)
    return {"status": "success"}


@router.get("/summary")
async def summary(user: User = Depends(get_current_user)):
    """Tiny aggregate for the /sandbox page — orders count, funds snapshot."""
    total_orders = order_manager.count_all_orders()
    funds = fund_manager.get_funds_snapshot(user.id)
    return {
        "status": "success",
        "data": {"total_orders": total_orders, "funds": funds},
    }


@router.get("/mypnl")
async def my_daily_pnl(
    limit: int = 180,
    user: User = Depends(get_current_user),
):
    """Return the caller's daily P&L history (one row per trading day)."""
    limit = max(1, min(365, int(limit)))
    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxDailyPnL)
                .where(SandboxDailyPnL.user_id == user.id)
                .order_by(SandboxDailyPnL.snapshot_date.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        data = [
            {
                "date": r.snapshot_date,
                "starting_capital": round(r.starting_capital, 2),
                "available": round(r.available, 2),
                "used_margin": round(r.used_margin, 2),
                "realized_pnl": round(r.realized_pnl, 2),
                "unrealized_pnl": round(r.unrealized_pnl, 2),
                "total_pnl": round(r.total_pnl, 2),
                "positions_pnl": round(r.positions_pnl, 2),
                "holdings_pnl": round(r.holdings_pnl, 2),
                "trades_count": r.trades_count,
            }
            for r in rows
        ]
    return {"status": "success", "data": data}


@router.post("/squareoff-now")
async def squareoff_now(
    bucket: str = "nse_nfo_bse_bfo",
    user: User = Depends(get_current_user),
):
    """Manual trigger for an exchange-bucket square-off. Admin-only (matches
    ``POST /web/trading-mode``). Handy for testing Phase 2b logic without
    waiting for the scheduler."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if bucket not in ("nse_nfo_bse_bfo", "cds", "mcx"):
        raise HTTPException(status_code=400, detail="Invalid bucket")
    placed = squareoff.squareoff_bucket(bucket)
    return {"status": "success", "placed": placed, "bucket": bucket}


@router.post("/settle-now")
async def settle_now(user: User = Depends(get_current_user)):
    """Manual trigger for T+1 CNC settlement + daily P&L snapshot. Admin-only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    moved = t1_settle.settle_cnc_to_holdings()
    written = pnl_snapshot.snapshot_for_date()
    return {
        "status": "success",
        "holdings_moved": moved,
        "pnl_snapshots_written": written,
    }


@router.post("/wipe-all")
async def wipe_all(user: User = Depends(get_current_user)):
    """Manual trigger for the weekly reset job. Wipes every user's state.
    Admin-only and intentionally destructive."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    affected = weekly_reset.wipe_all_users()
    return {"status": "success", "users_wiped": affected}


# ---------------------------------------------------------------------------
# Squareoff scheduler introspection — matches openalgo's
# /sandbox/squareoff-status and /sandbox/reload-squareoff endpoints.
# ---------------------------------------------------------------------------

@router.get("/squareoff-status")
async def squareoff_status(user: User = Depends(get_current_user)):
    """Return each squareoff bucket's configured cut-off time and whether the
    job has already run today. Lets the UI render a "next run at HH:MM" badge
    without restarting the scheduler."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from backend.models.sandbox import SandboxConfig

    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(tz=IST)
    today_iso = now.date().isoformat()

    buckets = (
        ("nse_nfo_bse_bfo", "squareoff_nse_nfo_bse_bfo", "15:15"),
        ("cds", "squareoff_cds", "16:45"),
        ("mcx", "squareoff_mcx", "23:30"),
    )

    out: list[dict] = []
    with session_scope() as db:
        rows_by_key = {
            r.key: r.value
            for r in db.execute(select(SandboxConfig)).scalars().all()
        }
        for bucket, cfg_key, default in buckets:
            cutoff_str = rows_by_key.get(cfg_key, default)
            last_run = rows_by_key.get(f"_sched_last_squareoff_{bucket}", "")
            already_ran_today = last_run == today_iso
            out.append({
                "bucket": bucket,
                "cutoff_time": cutoff_str,
                "ran_today": already_ran_today,
                "last_run": last_run,
            })
    return {
        "status": "success",
        "now_ist": now.isoformat(timespec="seconds"),
        "buckets": out,
    }


@router.post("/reload-squareoff")
async def reload_squareoff(user: User = Depends(get_current_user)):
    """Acknowledge that the scheduler should pick up new squareoff times on
    its next tick. The scheduler reads config every loop iteration so this
    is a no-op confirmation; admin-gated to match openalgo's surface."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {
        "status": "success",
        "message": "Squareoff schedule reloads automatically on the next "
                   "60s scheduler tick — no restart required.",
    }


# ---------------------------------------------------------------------------
# Margin reconciliation — matches openalgo's reconcile_margin admin call.
# ---------------------------------------------------------------------------

@router.post("/reconcile-margin")
async def reconcile_margin_endpoint(
    auto_fix: bool = True,
    user: User = Depends(get_current_user),
):
    """Compare ``fund.used_margin`` against the sum of position margins for
    the calling user. Releases stuck margin to *available* if ``auto_fix``
    is true and a positive drift is found."""
    consistent, drift, details = fund_manager.reconcile_margin(
        user.id, auto_fix=auto_fix
    )
    return {
        "status": "success",
        "consistent": consistent,
        "discrepancy": drift,
        "details": details,
    }
