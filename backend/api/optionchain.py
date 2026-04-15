"""
External API - Option chain endpoint.
Returns CE+PE quotes for strikes around ATM for a given underlying+expiry.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_STRIKE_COUNT = 10


@router.post("/optionchain")
async def api_option_chain(request: Request):
    """Return option chain (strike_count strikes either side of ATM) with live CE/PE quotes."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.option_chain_service import get_option_chain

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in optionchain endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    expiry_date = body.get("expiry_date")
    strike_count_raw = body.get("strike_count", DEFAULT_STRIKE_COUNT)

    if not underlying or not exchange:
        return JSONResponse(
            content={"status": "error", "message": "underlying and exchange are required"},
            status_code=400,
        )

    try:
        strike_count = None if strike_count_raw in (None, "all") else int(strike_count_raw)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "strike_count must be an integer or 'all'"},
            status_code=400,
        )

    success, response_data, status_code = get_option_chain(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        strike_count=strike_count,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
