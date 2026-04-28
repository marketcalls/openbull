"""
External API - Historical IV + Greeks chart for ATM CE & PE.
Computes Black-76 IV and Δ/Γ/Θ/V at each candle close over a chosen window.
NSE/BSE only.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ivchart")
async def api_iv_chart(request: Request):
    """Return IV + Greeks time-series for ATM CE & PE of an underlying/expiry."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.iv_chart_service import get_iv_chart_data

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in ivchart endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_date = body.get("expiry_date")
    interval = body.get("interval", "5m")
    days_raw = body.get("days", 1)

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

    interest_rate_raw = body.get("interest_rate")
    interest_rate: float | None
    if interest_rate_raw is None:
        interest_rate = None
    else:
        try:
            interest_rate = float(interest_rate_raw)
        except (ValueError, TypeError):
            return JSONResponse(
                content={"status": "error", "message": "interest_rate must be a number"},
                status_code=400,
            )

    success, response_data, status_code = get_iv_chart_data(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        interval=interval,
        days=days,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
        interest_rate=interest_rate,
    )
    return JSONResponse(content=response_data, status_code=status_code)
