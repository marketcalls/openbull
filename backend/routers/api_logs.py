"""
Read-side of the api_logs pipeline.

Any authenticated user can see *their own* rows; admins see everything.
Cursor-paginated (id-descending) so the list stays stable under concurrent
inserts.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db
from backend.models.audit import ApiLog
from backend.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/logs", tags=["api-logs"])


def _row_to_dict(r: ApiLog) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "user_id": r.user_id,
        "auth_method": r.auth_method,
        "mode": r.mode,
        "method": r.method,
        "path": r.path,
        "status_code": r.status_code,
        "duration_ms": r.duration_ms,
        "client_ip": r.client_ip,
        "user_agent": r.user_agent,
        "request_id": r.request_id,
        "request_body": r.request_body,
        "response_body": r.response_body,
        "error": r.error,
    }


def _apply_filters(stmt, *, user: User, filters: dict):
    # Non-admins see only their own rows. This is enforced server-side, not
    # via any client-sent flag, so the UI cannot escalate.
    if not user.is_admin:
        stmt = stmt.where(ApiLog.user_id == user.id)
    elif filters.get("user_id") is not None:
        stmt = stmt.where(ApiLog.user_id == filters["user_id"])

    if filters.get("method"):
        stmt = stmt.where(ApiLog.method == filters["method"].upper())
    if filters.get("mode"):
        stmt = stmt.where(ApiLog.mode == filters["mode"])
    if filters.get("status") is not None:
        stmt = stmt.where(ApiLog.status_code == filters["status"])
    if filters.get("status_class"):
        # "2xx" / "3xx" / "4xx" / "5xx"
        try:
            bucket = int(filters["status_class"][0])
            lo = bucket * 100
            hi = lo + 100
            stmt = stmt.where(ApiLog.status_code >= lo, ApiLog.status_code < hi)
        except Exception:
            pass
    if filters.get("path_contains"):
        stmt = stmt.where(ApiLog.path.ilike(f"%{filters['path_contains']}%"))
    if filters.get("start"):
        stmt = stmt.where(ApiLog.created_at >= filters["start"])
    if filters.get("end"):
        stmt = stmt.where(ApiLog.created_at <= filters["end"])
    return stmt


@router.get("")
async def list_api_logs(
    limit: int = Query(100, ge=1, le=1000),
    before_id: int | None = Query(None, ge=1),
    method: str | None = Query(None, max_length=8),
    mode: str | None = Query(None, pattern=r"^(live|sandbox)$"),
    status: int | None = Query(None, ge=100, le=599),
    status_class: str | None = Query(None, pattern=r"^[1-5]xx$"),
    path_contains: str | None = Query(None, max_length=200),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    user_id: int | None = Query(None, ge=1),  # admin-only filter
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paginated log list. Users see their own rows; admins see all."""
    filters = {
        "method": method,
        "mode": mode,
        "status": status,
        "status_class": status_class,
        "path_contains": path_contains,
        "start": start,
        "end": end,
        "user_id": user_id,
    }
    stmt = select(ApiLog).order_by(ApiLog.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(ApiLog.id < before_id)
    stmt = _apply_filters(stmt, user=user, filters=filters)

    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = rows[-1].id if rows and len(rows) == limit else None
    return {
        "count": len(rows),
        "next_cursor": next_cursor,
        "items": [_row_to_dict(r) for r in rows],
    }


@router.get("/stats")
async def api_logs_stats(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Small aggregate: total rows + simple status breakdown for the viewer."""
    base = select(func.count()).select_from(ApiLog)
    if not user.is_admin:
        base = base.where(ApiLog.user_id == user.id)
    if start:
        base = base.where(ApiLog.created_at >= start)
    if end:
        base = base.where(ApiLog.created_at <= end)
    total = (await db.execute(base)).scalar() or 0

    # 2xx / 4xx / 5xx counts
    def bucket(lo: int, hi: int):
        q = base.where(ApiLog.status_code >= lo, ApiLog.status_code < hi)
        return q

    ok_q = (
        select(func.count()).select_from(ApiLog).where(
            ApiLog.status_code >= 200, ApiLog.status_code < 300
        )
    )
    client_err_q = (
        select(func.count()).select_from(ApiLog).where(
            ApiLog.status_code >= 400, ApiLog.status_code < 500
        )
    )
    server_err_q = (
        select(func.count()).select_from(ApiLog).where(
            ApiLog.status_code >= 500
        )
    )
    if not user.is_admin:
        ok_q = ok_q.where(ApiLog.user_id == user.id)
        client_err_q = client_err_q.where(ApiLog.user_id == user.id)
        server_err_q = server_err_q.where(ApiLog.user_id == user.id)
    if start:
        ok_q = ok_q.where(ApiLog.created_at >= start)
        client_err_q = client_err_q.where(ApiLog.created_at >= start)
        server_err_q = server_err_q.where(ApiLog.created_at >= start)
    if end:
        ok_q = ok_q.where(ApiLog.created_at <= end)
        client_err_q = client_err_q.where(ApiLog.created_at <= end)
        server_err_q = server_err_q.where(ApiLog.created_at <= end)

    return {
        "total": total,
        "ok_2xx": (await db.execute(ok_q)).scalar() or 0,
        "client_errors_4xx": (await db.execute(client_err_q)).scalar() or 0,
        "server_errors_5xx": (await db.execute(server_err_q)).scalar() or 0,
    }


@router.get("/{log_id}")
async def get_api_log(
    log_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(select(ApiLog).where(ApiLog.id == log_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Log not found")
    if not user.is_admin and row.user_id != user.id:
        # Same response as missing, to avoid leaking existence of other users' rows.
        raise HTTPException(status_code=404, detail="Log not found")
    return _row_to_dict(row)


@router.get("/export.csv")
async def export_api_logs(
    method: str | None = Query(None, max_length=8),
    mode: str | None = Query(None, pattern=r"^(live|sandbox)$"),
    status: int | None = Query(None, ge=100, le=599),
    status_class: str | None = Query(None, pattern=r"^[1-5]xx$"),
    path_contains: str | None = Query(None, max_length=200),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    user_id: int | None = Query(None, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream up to 10,000 matching rows as CSV. Respects user scope."""
    filters = {
        "method": method,
        "mode": mode,
        "status": status,
        "status_class": status_class,
        "path_contains": path_contains,
        "start": start,
        "end": end,
        "user_id": user_id,
    }
    stmt = select(ApiLog).order_by(ApiLog.id.desc()).limit(10_000)
    stmt = _apply_filters(stmt, user=user, filters=filters)
    rows = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "created_at", "user_id", "auth_method", "mode", "method", "path",
        "status_code", "duration_ms", "client_ip", "request_id", "error",
        "request_body", "response_body",
    ])
    for r in rows:
        w.writerow([
            r.id,
            r.created_at.isoformat() if r.created_at else "",
            r.user_id or "",
            r.auth_method or "",
            r.mode or "",
            r.method or "",
            r.path or "",
            r.status_code,
            f"{r.duration_ms:.2f}" if r.duration_ms is not None else "",
            r.client_ip or "",
            r.request_id or "",
            r.error or "",
            r.request_body or "",
            r.response_body or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="api_logs.csv"'},
    )
