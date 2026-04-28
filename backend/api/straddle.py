"""
External API - Dynamic ATM Straddle chart endpoint.
Mirrors openalgo's /straddle/api/straddle-data: time series with spot, ATM
strike, CE/PE prices, straddle (CE+PE) and synthetic future (K+CE-PE).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/straddle")
async def api_straddle(request: Request):
    """Compute the dynamic-ATM straddle + synthetic-future time series."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.straddle_chart_service import get_straddle_chart_data

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in straddle endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_date = body.get("expiry_date")
    interval = body.get("interval", "1m")
    days_raw = body.get("days", 5)

    if not underlying or not exchange or not expiry_date:
        return JSONResponse(
            content={"status": "error", "message": "underlying, exchange and expiry_date are required"},
            status_code=400,
        )

    try:
        days = int(days_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "days must be an integer"},
            status_code=400,
        )

    success, response_data, status_code = get_straddle_chart_data(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        interval=interval,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
        days=days,
    )
    return JSONResponse(content=response_data, status_code=status_code)
