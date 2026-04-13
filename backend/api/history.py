"""
External API - Historical OHLCV candles endpoint.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/history")
async def api_history(request: Request):
    """Get historical OHLCV candles via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.history_service import get_history_with_auth

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in history endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    symbol = body.get("symbol")
    exchange = body.get("exchange")
    interval = body.get("interval")
    start_date = body.get("start_date")
    end_date = body.get("end_date")

    if not all([symbol, exchange, interval, start_date, end_date]):
        return JSONResponse(
            content={"status": "error", "message": "symbol, exchange, interval, start_date, and end_date are required"},
            status_code=400,
        )

    success, response_data, status_code = get_history_with_auth(
        symbol=symbol, exchange=exchange, interval=interval,
        start_date=start_date, end_date=end_date,
        auth_token=auth_token, broker=broker_name, config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
