"""
External API - Market depth endpoint.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/depth")
async def api_depth(request: Request):
    """Get 5-level market depth for a symbol via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.depth_service import get_depth_with_auth

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in depth endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    symbol = body.get("symbol")
    exchange = body.get("exchange")
    if not symbol or not exchange:
        return JSONResponse(content={"status": "error", "message": "symbol and exchange are required"}, status_code=400)

    success, response_data, status_code = get_depth_with_auth(
        symbol=symbol, exchange=exchange, auth_token=auth_token, broker=broker_name, config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
