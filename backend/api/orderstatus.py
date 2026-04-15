"""
External API - Order status endpoint.
Response format follows OpenAlgo standard:
  Success: {"status": "success", "data": {...}}
  Error:   {"status": "error", "message": "..."}
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/orderstatus")
async def api_orderstatus(request: Request):
    """Get status of a specific order via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.orderstatus_service import get_orderstatus_with_auth

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
        logger.exception("Unexpected error in orderstatus endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    orderid = body.get("orderid")
    _strategy = body.get("strategy", "")  # OpenAlgo parity — accepted but unused
    if not orderid:
        return JSONResponse(
            content={"status": "error", "message": "orderid is required"},
            status_code=400,
        )

    success, response_data, status_code = get_orderstatus_with_auth(
        orderid=orderid,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
