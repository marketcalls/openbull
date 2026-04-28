"""
External API - GEX (Gamma Exposure) Dashboard endpoint.
Mirrors openalgo's /gex/api/gex-data: per-strike OI walls and net GEX along
with totals and futures price.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/gex")
async def api_gex(request: Request):
    """Compute Gamma Exposure (GEX) per strike for an underlying + expiry."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.gex_service import get_gex_data

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in gex endpoint")
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

    success, response_data, status_code = get_gex_data(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
