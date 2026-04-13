"""
Ping endpoint - connectivity and authentication check for the external API.
Response format follows OpenAlgo standard:
  Success: {"status": "success", "message": "pong"}
  Error:   {"status": "error", "message": "..."}
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ping")
async def ping(request: Request):
    """Check API connectivity and authentication."""
    from backend.dependencies import get_api_user, get_db

    try:
        async for db in get_db():
            await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": message},
            status_code=e.status_code,
        )
    except Exception:
        logger.exception("Unexpected error in ping endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    return JSONResponse(
        content={"status": "success", "message": "pong"},
        status_code=200,
    )
