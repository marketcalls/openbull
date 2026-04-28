"""
External API - Volatility Surface endpoint.
Returns a rectangular IV grid across (strikes × expiries) using OTM convention
(CE IV for K>=ATM, PE IV for K<ATM). Mirrors openalgo's vol surface API.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_STRIKE_COUNT = 10
MAX_EXPIRIES = 8
MAX_STRIKE_COUNT = 40


@router.post("/volsurface")
async def api_vol_surface(request: Request):
    """Build the volatility surface payload for a given underlying + expiry list."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.vol_surface_service import get_vol_surface_data

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in volsurface endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_dates = body.get("expiry_dates")
    strike_count_raw = body.get("strike_count", DEFAULT_STRIKE_COUNT)

    if not underlying or not exchange:
        return JSONResponse(
            content={"status": "error", "message": "underlying and exchange are required"},
            status_code=400,
        )

    if not isinstance(expiry_dates, list) or len(expiry_dates) == 0:
        return JSONResponse(
            content={"status": "error", "message": "expiry_dates must be a non-empty list"},
            status_code=400,
        )

    if len(expiry_dates) > MAX_EXPIRIES:
        return JSONResponse(
            content={"status": "error", "message": f"At most {MAX_EXPIRIES} expiries supported"},
            status_code=400,
        )

    try:
        strike_count = int(strike_count_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "strike_count must be an integer"},
            status_code=400,
        )

    if strike_count < 1 or strike_count > MAX_STRIKE_COUNT:
        return JSONResponse(
            content={"status": "error", "message": f"strike_count must be between 1 and {MAX_STRIKE_COUNT}"},
            status_code=400,
        )

    success, response_data, status_code = get_vol_surface_data(
        underlying=underlying,
        exchange=exchange,
        expiry_dates=[str(e) for e in expiry_dates],
        strike_count=strike_count,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
