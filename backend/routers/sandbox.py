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
from backend.sandbox import fund_manager, order_manager
from backend.sandbox._db import session_scope
from backend.models.sandbox import (
    SandboxHolding,
    SandboxOrder,
    SandboxPosition,
    SandboxTrade,
)
from sqlalchemy import delete

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
