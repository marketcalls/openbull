"""Strategy module WebSocket endpoint.

``WS /ws/strategy/{strategy_id}`` — session-cookie authed, sends an initial
snapshot on connect, then deltas + events while the run is live.

Phase 6 message shapes (subset of plan Section 7):
  * ``snapshot``  — full state on connect
  * ``delta``     — partial updates from each processed tick (throttled)
  * ``event``     — every risk event published on the bus
  * ``terminal``  — sent when the run stops (engine clears state)

Auth: relies on the existing session cookie, same as the rest of
``/web/strategy/*``. FastAPI's ``WebSocket`` exposes cookies via
``websocket.cookies`` — we decode the JWT and check ownership.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.strategy_module import SmStrategy
from backend.models.user import User
from backend.models.audit import ActiveSession
from backend.security import decode_access_token
from backend.strategy import broadcast, state as state_module
from backend.strategy.time_utils import format_ist, now_utc

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth (cookie-based, mirrors backend.dependencies.get_current_user)
# ---------------------------------------------------------------------------


async def _authenticate(websocket: WebSocket) -> Optional[int]:
    """Return the authenticated user_id, or None to close the socket."""
    token = websocket.cookies.get("access_token")
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    jti = payload.get("jti")
    if not user_id or not jti:
        return None
    async with async_session() as db:
        sess = (await db.execute(
            select(ActiveSession).where(
                ActiveSession.user_id == int(user_id),
                ActiveSession.session_token == jti,
            )
        )).scalar_one_or_none()
        if sess is None:
            return None
    return int(user_id)


async def _owns_strategy(db: AsyncSession, *, user_id: int, strategy_id: int) -> Optional[SmStrategy]:
    return (await db.execute(
        select(SmStrategy).where(SmStrategy.id == strategy_id, SmStrategy.user_id == user_id)
    )).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


async def _build_snapshot(strategy: SmStrategy) -> dict:
    """Initial full state sent on connect."""
    state = None
    if strategy.current_run_id:
        state = await state_module.get_run_state(strategy.current_run_id)

    legs_payload = []
    if state:
        for lid, leg in (state.get("legs") or {}).items():
            legs_payload.append({
                "leg_id": int(lid),
                "symbol": leg.get("symbol"),
                "exchange": leg.get("exchange"),
                "position": leg.get("position"),
                "qty": leg.get("qty"),
                "entry_avg": leg.get("entry_avg"),
                "ltp": leg.get("ltp"),
                "mtm": leg.get("mtm"),
                "status": leg.get("status"),
                "effective_sl": leg.get("effective_sl"),
                "effective_target": leg.get("effective_target"),
                "trail_active": leg.get("trail_active"),
                "favorable_peak": leg.get("favorable_peak"),
            })
    else:
        for leg in strategy.legs or []:
            legs_payload.append({
                "leg_id": int(leg.get("id")),
                "position": leg.get("position"),
                "qty": None,
                "status": "configured",
            })

    return {
        "type": "snapshot",
        "ts_ist": format_ist(now_utc()),
        "ts_ms_utc": int(now_utc().timestamp() * 1000),
        "strategy_id": strategy.id,
        "run_id": strategy.current_run_id,
        "status": strategy.status,
        "mode": (await _resolve_run_mode(strategy)) if strategy.current_run_id else None,
        "mtm_realized": (state or {}).get("pnl_realized") or 0.0,
        "mtm_unrealized": (state or {}).get("pnl_unrealized") or 0.0,
        "mtm_total": (state or {}).get("pnl_total") or 0.0,
        "peak": (state or {}).get("pnl_peak") or 0.0,
        "trough": (state or {}).get("pnl_trough") or 0.0,
        "legs": legs_payload,
    }


async def _resolve_run_mode(strategy: SmStrategy) -> Optional[str]:
    from backend.models.strategy_module import SmStrategyRun
    async with async_session() as db:
        run = await db.get(SmStrategyRun, strategy.current_run_id)
        return run.mode if run else None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/strategy/{strategy_id}")
async def strategy_ws(websocket: WebSocket, strategy_id: int):
    """One client per strategy. Receives snapshot + deltas + events live."""
    await websocket.accept()

    user_id = await _authenticate(websocket)
    if user_id is None:
        await websocket.send_json({"type": "error", "message": "Unauthorized"})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with async_session() as db:
        strategy = await _owns_strategy(db, user_id=user_id, strategy_id=strategy_id)
        if strategy is None:
            await websocket.send_json({"type": "error", "message": "Not found"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        snapshot = await _build_snapshot(strategy)
    await websocket.send_json(snapshot)

    queue = await broadcast.register(strategy_id)
    logger.info("WS /ws/strategy/%d connected (user=%d)", strategy_id, user_id)
    try:
        while True:
            # Race the queue against the client to detect disconnects promptly.
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Heartbeat — keeps the connection alive through proxies and
                # lets us notice a half-open socket.
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS /ws/strategy/%d error", strategy_id)
    finally:
        await broadcast.unregister(strategy_id, queue)
        logger.info("WS /ws/strategy/%d disconnected", strategy_id)
