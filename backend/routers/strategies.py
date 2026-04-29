"""
Saved-strategies CRUD endpoints.

Session-authed (cookie-based) under ``/web/strategies/*`` — these are
internal UI endpoints, not external API. The Strategy Builder posts here
when the user hits Save; the Strategy Portfolio page reads here. Per-user
isolation: every query filters on the authenticated user's id.

Endpoints:

* ``GET    /web/strategies``                  — list (filters: mode, status, underlying)
* ``GET    /web/strategies/{id}``             — fetch one
* ``POST   /web/strategies``                  — create
* ``PUT    /web/strategies/{id}``             — partial update (legs / status / notes / ...)
* ``DELETE /web/strategies/{id}``             — hard delete

Status transitions: setting ``status="closed"`` also stamps ``closed_at``
server-side so the Portfolio can sort/group closed trades by close date.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db
from backend.models.strategies import Strategy
from backend.models.user import User
from backend.schemas.strategies import (
    StrategyCreate,
    StrategyOut,
    StrategyUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/strategies", tags=["strategies"])


def _serialize(row: Strategy) -> dict:
    return StrategyOut.model_validate(row).model_dump(mode="json")


def _legs_payload(legs) -> list[dict]:
    """Pydantic StrategyLeg list -> JSON-safe list of dicts for the JSONB column."""
    return [leg.model_dump(mode="json") for leg in legs]


@router.get("")
async def list_strategies(
    mode: Optional[str] = Query(None, description='Filter: "live" or "sandbox"'),
    status: Optional[str] = Query(None, description='Filter: "active" / "closed" / "expired"'),
    underlying: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return saved strategies for the current user, newest first."""
    stmt = select(Strategy).where(Strategy.user_id == user.id)
    if mode:
        stmt = stmt.where(Strategy.mode == mode)
    if status:
        stmt = stmt.where(Strategy.status == status)
    if underlying:
        stmt = stmt.where(Strategy.underlying == underlying.upper())
    stmt = stmt.order_by(Strategy.created_at.desc())

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "status": "success",
        "strategies": [_serialize(r) for r in rows],
    }


@router.get("/{strategy_id}")
async def get_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single saved strategy by id (must belong to the current user)."""
    row = (
        await db.execute(
            select(Strategy).where(
                Strategy.id == strategy_id, Strategy.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {"status": "success", "strategy": _serialize(row)}


@router.post("")
async def create_strategy(
    payload: StrategyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a new strategy."""
    row = Strategy(
        user_id=user.id,
        name=payload.name.strip(),
        underlying=payload.underlying.strip().upper(),
        exchange=payload.exchange.strip().upper(),
        expiry_date=payload.expiry_date,
        mode=payload.mode,
        legs=_legs_payload(payload.legs),
        notes=payload.notes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "User %s saved strategy '%s' (id=%d, %d legs, mode=%s)",
        user.username,
        row.name,
        row.id,
        len(payload.legs),
        row.mode,
    )
    return {"status": "success", "strategy": _serialize(row)}


@router.put("/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    payload: StrategyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update — only fields present in the request body are touched.

    Setting ``status="closed"`` from any other status also stamps
    ``closed_at`` so the Portfolio UI can show "closed N days ago".
    """
    row = (
        await db.execute(
            select(Strategy).where(
                Strategy.id == strategy_id, Strategy.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    data = payload.model_dump(exclude_unset=True)

    if "legs" in data and payload.legs is not None:
        data["legs"] = _legs_payload(payload.legs)
    if "underlying" in data and data["underlying"]:
        data["underlying"] = data["underlying"].strip().upper()
    if "exchange" in data and data["exchange"]:
        data["exchange"] = data["exchange"].strip().upper()
    if "name" in data and data["name"]:
        data["name"] = data["name"].strip()

    if (
        data.get("status") == "closed"
        and row.status != "closed"
        and row.closed_at is None
    ):
        row.closed_at = datetime.now(timezone.utc)

    for key, value in data.items():
        setattr(row, key, value)

    await db.commit()
    await db.refresh(row)
    return {"status": "success", "strategy": _serialize(row)}


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a saved strategy."""
    row = (
        await db.execute(
            select(Strategy).where(
                Strategy.id == strategy_id, Strategy.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    await db.delete(row)
    await db.commit()
    logger.info("User %s deleted strategy id=%d", user.username, strategy_id)
    return {"status": "success"}
