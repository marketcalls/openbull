"""
External API - Expiry dates endpoint.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.services.market_data_service import get_expiry_dates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/expiry")
async def api_expiry(request: Request):
    """Get expiry dates for a symbol via the external API."""
    from backend.dependencies import get_api_user, get_db

    try:
        async for db in get_db():
            await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in expiry endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        body = {}

    symbol = body.get("symbol")
    exchange = body.get("exchange")
    if not symbol or not exchange:
        return JSONResponse(content={"status": "error", "message": "symbol and exchange are required"}, status_code=400)

    success, response_data, status_code = get_expiry_dates(symbol, exchange)
    return JSONResponse(content=response_data, status_code=status_code)
