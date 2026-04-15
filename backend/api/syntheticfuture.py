"""
External API - Synthetic future endpoint.
Calculates synthetic future price from ATM CE+PE for an underlying+expiry.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/syntheticfuture")
async def api_synthetic_future(request: Request):
    """Compute synthetic future price (ATM CE + ATM PE)."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.synthetic_future_service import calculate_synthetic_future

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in syntheticfuture endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_date = body.get("expiry_date")

    missing = [n for n, v in [("underlying", underlying), ("exchange", exchange), ("expiry_date", expiry_date)] if not v]
    if missing:
        return JSONResponse(
            content={"status": "error", "message": f"Missing mandatory field(s): {', '.join(missing)}"},
            status_code=400,
        )

    success, response_data, status_code = calculate_synthetic_future(
        underlying=underlying, exchange=exchange, expiry_date=expiry_date,
        auth_token=auth_token, broker=broker_name, config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
