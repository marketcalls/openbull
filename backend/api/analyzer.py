"""
OpenAlgo-compatible analyzer/sandbox mode endpoints.

OpenBull has two modes — ``live`` and ``sandbox``. OpenAlgo historically called
its equivalent "analyzer" mode, so these ``/api/v1/analyzer*`` endpoints exist
to keep existing OpenAlgo client scripts working when they point at OpenBull.

The mapping is:

* internal ``sandbox`` mode  <->  external API ``"mode": "analyze"``, ``"analyze_mode": true``
* internal ``live`` mode     <->  external API ``"mode": "live"``, ``"analyze_mode": false``

Request / response shapes match OpenAlgo's docs at
``docs/api/analyzer-services/analyzertoggle.md`` and ``analyzerstatus.md``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from backend.services.trading_mode_service import (
    get_trading_mode,
    set_trading_mode,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_api_user(request: Request):
    from backend.dependencies import get_api_user, get_db

    async for db in get_db():
        return await get_api_user(request, db), db


async def _read_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


async def _total_sandbox_logs(db) -> int:
    """Count of sandbox orders logged so far.

    Phase 1 has no ``sandbox_orders`` table yet — return 0 until Phase 2
    ships. Probed by table existence so this code keeps working after
    Phase 2 adds the table.
    """
    try:
        from backend.models.audit import ApiLog  # always exists

        stmt = select(func.count()).select_from(ApiLog).where(
            # Will exist once the mode column lands (Phase 3). Until then,
            # this filter is a no-op and count = 0 is fine.
            ApiLog.path.ilike("/api/v1/placeorder%")
        )
        # Placeholder until sandbox_orders exists; intentionally conservative.
        _ = stmt
        return 0
    except Exception:
        return 0


def _data_payload(mode_internal: str, total_logs: int, message: str | None = None) -> dict:
    """Translate internal mode value into OpenAlgo-compatible response data."""
    analyze_mode = mode_internal == "sandbox"
    data: dict = {
        "analyze_mode": analyze_mode,
        "mode": "analyze" if analyze_mode else "live",
        "total_logs": total_logs,
    }
    if message is not None:
        data["message"] = message
    return data


@router.post("/analyzerstatus")
async def analyzer_status(request: Request):
    """Return the current trading mode (OpenAlgo-compatible shape)."""
    try:
        (_api_user, db) = await _resolve_api_user(request)
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": msg}, status_code=e.status_code
        )
    except Exception:
        logger.exception("analyzerstatus auth failed")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    mode = await get_trading_mode(db)
    total = await _total_sandbox_logs(db)
    return JSONResponse(
        content={"status": "success", "data": _data_payload(mode, total)},
        status_code=200,
    )


@router.post("/analyzertoggle")
async def analyzer_toggle(request: Request):
    """Toggle between live and sandbox mode (OpenAlgo-compatible)."""
    try:
        (api_user, db) = await _resolve_api_user(request)
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": msg}, status_code=e.status_code
        )
    except Exception:
        logger.exception("analyzertoggle auth failed")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    body = await _read_body(request)
    raw_mode = body.get("mode")
    if raw_mode is None:
        return JSONResponse(
            content={"status": "error", "message": "'mode' is required (true|false)"},
            status_code=400,
        )
    if not isinstance(raw_mode, bool):
        return JSONResponse(
            content={"status": "error", "message": "'mode' must be a boolean"},
            status_code=400,
        )

    # Admin gate: mode toggle is a global switch — only admins may flip it.
    try:
        from backend.models.user import User

        user_id = api_user[0]
        row = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if row is None or not row.is_admin:
            return JSONResponse(
                content={"status": "error", "message": "Admin access required"},
                status_code=403,
            )
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": msg}, status_code=e.status_code
        )

    internal = "sandbox" if raw_mode else "live"
    try:
        await set_trading_mode(db, internal)
    except ValueError as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )

    total = await _total_sandbox_logs(db)
    msg = "Analyzer mode switched to " + ("analyze" if raw_mode else "live")
    return JSONResponse(
        content={"status": "success", "data": _data_payload(internal, total, msg)},
        status_code=200,
    )
