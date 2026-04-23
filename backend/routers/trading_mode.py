"""
Trading-mode REST endpoints.

``GET  /web/trading-mode`` — any authenticated user may read the current mode
  so the UI can paint the right theme / banner.

``POST /web/trading-mode`` — admin-only mutation. Body: ``{"mode": "live" | "sandbox"}``.
  Mode is a *global* setting — one per instance, not per user — matching
  openalgo's design.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db
from backend.models.user import User
from backend.services.trading_mode_service import (
    get_trading_mode,
    set_trading_mode,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/trading-mode", tags=["trading-mode"])


class TradingModePayload(BaseModel):
    mode: str = Field(..., description='"live" or "sandbox"')


@router.get("")
async def read_trading_mode(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current global trading mode."""
    mode = await get_trading_mode(db)
    return {"mode": mode}


@router.post("")
async def update_trading_mode(
    payload: TradingModePayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch the instance between live and sandbox. Admin-only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        mode = await set_trading_mode(db, payload.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("User %s switched trading mode to %s", user.username, mode)
    return {"mode": mode}
