"""
External API - Open position endpoint.
Response format follows OpenAlgo standard:
  Success: {"status": "success", "data": {"quantity": int}}
  Error:   {"status": "error", "message": "..."}
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/openposition")
async def api_openposition(request: Request):
    """Get open position quantity for a specific symbol via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.openposition_service import get_openposition_with_auth

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": message},
            status_code=e.status_code,
        )
    except Exception:
        logger.exception("Unexpected error in openposition endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    symbol = body.get("symbol")
    exchange = body.get("exchange")
    product = body.get("product")
    _strategy = body.get("strategy", "")  # OpenAlgo parity — accepted but unused

    if not all([symbol, exchange, product]):
        return JSONResponse(
            content={"status": "error", "message": "symbol, exchange, and product are required"},
            status_code=400,
        )

    success, response_data, status_code = get_openposition_with_auth(
        symbol=symbol,
        exchange=exchange,
        product=product,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
