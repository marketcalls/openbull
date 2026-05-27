"""Periodic checkpoint writer for active runs.

Background asyncio task started in the FastAPI lifespan. Every
``STRATEGY_CHECKPOINT_INTERVAL_SEC`` seconds it walks every running run
and writes a ``sm_strategy_checkpoint`` row reflecting the current Redis
state. Recovery.py reads the latest checkpoint on boot to restore peak /
trough / lock-profit / trail-to-entry without re-running the whole run
from the start.

The loop is intentionally chatty about exceptions but never crashes —
checkpoint loss is recoverable (state is rebuilt from DB orders +
broker reconciliation on next boot), so a transient Redis hiccup
shouldn't kill the daemon.

Phase 5 ships the loop with empty-but-correct snapshots. Phase 6 starts
populating live pnl_total / pnl_unrealized into the state, at which
point checkpoints become useful for crash-time P&L preservation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.strategy_module import SmStrategyCheckpoint, SmStrategyRun
from backend.strategy import state as state_module

logger = logging.getLogger(__name__)

CHECKPOINT_INTERVAL_SEC = 5

_task: asyncio.Task | None = None
_running: bool = False


def _safe_jsonable(obj: Any) -> Any:
    """Coerce datetime → ISO for JSONB column; leave the rest alone."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


async def _write_one(db: AsyncSession, run: SmStrategyRun) -> bool:
    state = await state_module.get_run_state(run.id)
    if state is None:
        # No state in Redis (engine never ran, or Redis was flushed). Skip —
        # recovery on next boot will rehydrate from DB.
        return False
    legs_blob = state.get("legs") or {}
    row = SmStrategyCheckpoint(
        run_id=run.id,
        pnl_realized=state.get("pnl_realized") or 0.0,
        pnl_unrealized=state.get("pnl_unrealized") or 0.0,
        pnl_total=state.get("pnl_total") or 0.0,
        pnl_peak=state.get("pnl_peak") or 0.0,
        pnl_trough=state.get("pnl_trough") or 0.0,
        lock_floor=state.get("lock_floor"),
        trail_to_entry_active=bool(state.get("trail_to_entry_active") or False),
        leg_state=json.loads(json.dumps(legs_blob, default=_safe_jsonable)),
    )
    db.add(row)
    return True


async def _checkpoint_pass() -> None:
    async with async_session() as db:
        runs = list((await db.execute(
            select(SmStrategyRun).where(SmStrategyRun.stopped_at == None)  # noqa: E711
        )).scalars().all())
        if not runs:
            return
        written = 0
        for run in runs:
            try:
                if await _write_one(db, run):
                    written += 1
            except Exception:
                logger.exception("Checkpoint write failed for run %d", run.id)
        if written > 0:
            await db.commit()
            logger.debug("Strategy checkpoints written: %d", written)


async def _loop() -> None:
    logger.info("Strategy checkpoint loop started (interval=%ds)", CHECKPOINT_INTERVAL_SEC)
    try:
        while _running:
            try:
                await _checkpoint_pass()
            except Exception:
                logger.exception("Strategy checkpoint pass failed")
            # Sleep in 1s slices so shutdown returns quickly.
            for _ in range(CHECKPOINT_INTERVAL_SEC):
                if not _running:
                    return
                await asyncio.sleep(1)
    finally:
        logger.info("Strategy checkpoint loop stopped")


def start() -> None:
    """Kick off the daemon task. Idempotent."""
    global _task, _running
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_loop(), name="strategy-checkpoint")


async def stop() -> None:
    """Stop the loop and await the task. Safe to call on shutdown."""
    global _task, _running
    _running = False
    if _task is None:
        return
    if not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error stopping strategy checkpoint task")
    _task = None
