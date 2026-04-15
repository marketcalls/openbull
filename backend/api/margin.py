"""
External API - Margin calculator endpoint.
Response format follows OpenAlgo standard:
  Success: {"status": "success", "data": {...}}
  Error:   {"status": "error", "message": "..."}
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/margin")
async def api_margin(request: Request):
    """Calculate margin for a basket of positions via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.margin_service import calculate_margin

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": message},
            status_code=e.status_code,
        )
    except Exception:
        logger.exception("Unexpected error in margin endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    margin_data = {"positions": body.get("positions", [])}

    success, response_data, status_code = calculate_margin(
        margin_data=margin_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
