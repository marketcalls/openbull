"""Admin-only endpoint to review persisted error logs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db
from backend.models.audit import ErrorLog
from backend.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/error-logs")
async def list_error_logs(
    limit: int = Query(100, ge=1, le=500),
    before_id: int | None = Query(None, ge=1),
    level: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent WARNING+ log records.

    Admin-only. Paginate by passing ``before_id`` from the last item on the
    previous page. ``level`` may be WARNING, ERROR, or CRITICAL.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    stmt = select(ErrorLog).order_by(ErrorLog.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(ErrorLog.id < before_id)
    if level:
        stmt = stmt.where(ErrorLog.level == level.upper())

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "level": r.level,
                "logger": r.logger,
                "message": r.message,
                "module": r.module,
                "func_name": r.func_name,
                "lineno": r.lineno,
                "request_id": r.request_id,
                "exc_text": r.exc_text,
            }
            for r in rows
        ],
    }
