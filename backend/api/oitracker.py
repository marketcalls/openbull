"""
External API - OI Tracker endpoint.
Returns CE/PE OI per strike around ATM, totals, PCR, and matching-expiry
futures price for an underlying.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/oitracker")
async def api_oi_tracker(request: Request):
    """Build the OI Tracker payload for a given underlying + expiry."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.oi_tracker_service import get_oi_tracker_data

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in oitracker endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_date = body.get("expiry_date")

    if not underlying or not exchange or not expiry_date:
        return JSONResponse(
            content={"status": "error", "message": "underlying, exchange and expiry_date are required"},
            status_code=400,
        )

    success, response_data, status_code = get_oi_tracker_data(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
