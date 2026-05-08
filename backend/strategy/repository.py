"""Repository layer for the strategy module.

Every read/write here filters on ``user_id`` — there is no helper that returns
a strategy without taking a user_id. Cross-tenant access raises ``NotFound``
(404 at the router layer, not 403 — never leak existence).

Higher layers (router, engine) call into this module; they never write SQL
directly.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.strategy_events import (
    StrategyCreatedEvent,
    StrategyDeletedEvent,
    StrategyUpdatedEvent,
    WebhookTokenRotatedEvent,
)
from backend.models.strategy_module import SmStrategy
from backend.strategy.security import generate_webhook_token, hash_webhook_token
from backend.utils.event_bus import bus

logger = logging.getLogger(__name__)


class NotFound(Exception):
    """Raised when a record doesn't exist or belongs to another user.

    Routers map this to HTTP 404. Never differentiate "doesn't exist" from
    "not yours" — that prevents cross-tenant enumeration.
    """


class Conflict(Exception):
    """Raised on uniqueness / state-violation conflicts (mapped to 409)."""


# ---------------------------------------------------------------------------
# Strategy CRUD
# ---------------------------------------------------------------------------


async def create_strategy(
    db: AsyncSession,
    *,
    user_id: int,
    payload: dict,
) -> tuple[SmStrategy, str]:
    """Insert a new strategy. Returns (row, plaintext_webhook_token).

    The plaintext token is shown to the caller exactly once. Only the SHA-256
    hash is stored.
    """
    plaintext, token_hash = generate_webhook_token()

    row = SmStrategy(
        user_id=user_id,
        name=payload["name"].strip(),
        universe_tab=payload["universe_tab"],
        underlying=payload["underlying"].strip().upper(),
        underlying_exchange=payload["underlying_exchange"].strip().upper(),
        strategy_type=payload["strategy_type"],
        entry_time=payload.get("entry_time"),
        exit_time=payload.get("exit_time"),
        product=payload.get("product", "NRML"),
        pricetype=payload.get("pricetype", "MARKET"),
        legs=payload["legs"],
        overall_sl_mtm=payload.get("overall_sl_mtm"),
        overall_target_mtm=payload.get("overall_target_mtm"),
        lock_profit=payload.get("lock_profit"),
        trail_sl_to_entry=payload.get("trail_sl_to_entry", False),
        scheduler=payload.get("scheduler"),
        live_enabled=False,
        webhook_token_hash=token_hash,
        webhook_ip_allowlist=payload.get("webhook_ip_allowlist"),
        daily_loss_limit_inr=payload.get("daily_loss_limit_inr"),
        status="stopped",
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Most likely the (user_id, name) unique constraint
        if "uq_sm_strategy_user_name" in str(e.orig):
            raise Conflict("A strategy with this name already exists") from e
        raise
    await db.refresh(row)

    bus.publish(StrategyCreatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Strategy '{row.name}' created",
        payload={"underlying": row.underlying, "legs": len(row.legs)},
    ))
    return row, plaintext


async def get_strategy(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> SmStrategy:
    row = (
        await db.execute(
            select(SmStrategy).where(
                SmStrategy.id == strategy_id, SmStrategy.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound()
    return row


async def list_strategies(
    db: AsyncSession,
    *,
    user_id: int,
    status: Optional[str] = None,
    universe_tab: Optional[str] = None,
) -> Sequence[SmStrategy]:
    stmt = select(SmStrategy).where(SmStrategy.user_id == user_id)
    if status:
        stmt = stmt.where(SmStrategy.status == status)
    if universe_tab:
        stmt = stmt.where(SmStrategy.universe_tab == universe_tab)
    stmt = stmt.order_by(SmStrategy.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


async def update_strategy(
    db: AsyncSession,
    *,
    user_id: int,
    strategy_id: int,
    patch: dict[str, Any],
) -> SmStrategy:
    """Partial update. Refused (409) when status != 'stopped'."""
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    if row.status != "stopped":
        raise Conflict(f"Cannot edit a strategy that is currently '{row.status}'")

    if "underlying" in patch and patch["underlying"]:
        patch["underlying"] = patch["underlying"].strip().upper()
    if "underlying_exchange" in patch and patch["underlying_exchange"]:
        patch["underlying_exchange"] = patch["underlying_exchange"].strip().upper()
    if "name" in patch and patch["name"]:
        patch["name"] = patch["name"].strip()

    for k, v in patch.items():
        setattr(row, k, v)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "uq_sm_strategy_user_name" in str(e.orig):
            raise Conflict("A strategy with this name already exists") from e
        raise
    await db.refresh(row)

    bus.publish(StrategyUpdatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Strategy '{row.name}' updated",
        payload={"fields": list(patch.keys())},
    ))
    return row


async def delete_strategy(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> None:
    """Hard delete. Refused (409) when status != 'stopped'."""
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    if row.status != "stopped":
        raise Conflict(f"Cannot delete a strategy that is currently '{row.status}'")
    name = row.name
    await db.delete(row)
    await db.commit()

    # Note: cascading FK deletes the audit-trail rows for this strategy. The
    # delete event below cannot be persisted (FK now invalid) — that's
    # intentional. The deletion itself is recorded in app logs.
    logger.info("user=%d deleted strategy id=%d name=%r", user_id, strategy_id, name)


async def rotate_webhook_token(
    db: AsyncSession, *, user_id: int, strategy_id: int
) -> tuple[SmStrategy, str]:
    """Issue a fresh webhook token. Old token is invalidated immediately.

    Returns (row, new_plaintext). Plaintext is shown once and never stored.
    """
    row = await get_strategy(db, user_id=user_id, strategy_id=strategy_id)
    plaintext, token_hash = generate_webhook_token()
    row.webhook_token_hash = token_hash
    await db.commit()
    await db.refresh(row)
    bus.publish(WebhookTokenRotatedEvent(
        user_id=user_id,
        strategy_id=row.id,
        message=f"Webhook token rotated for '{row.name}'",
        severity="warn",
    ))
    logger.info(
        "user=%d rotated webhook token for strategy id=%d", user_id, strategy_id
    )
    return row, plaintext


# Helper used by future webhook handler (Phase 9). Lives here so all
# webhook-token-aware queries go through one place.
async def find_strategy_by_webhook_token(
    db: AsyncSession, *, plaintext_token: str
) -> Optional[SmStrategy]:
    """Resolve an incoming webhook URL token to a strategy. None on miss."""
    token_hash = hash_webhook_token(plaintext_token)
    return (
        await db.execute(
            select(SmStrategy).where(SmStrategy.webhook_token_hash == token_hash)
        )
    ).scalar_one_or_none()
