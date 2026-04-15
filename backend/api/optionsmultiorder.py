"""
External API - Options multi-order endpoint.
Place multiple option legs (e.g. Iron Condor, Strangle, Spread) in one request.
BUY legs execute before SELL legs for margin efficiency.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/optionsmultiorder")
async def api_options_multiorder(request: Request):
    """Place a multi-leg options strategy."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.options_multiorder_service import place_options_multiorder

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in optionsmultiorder endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    success, response_data, status_code = place_options_multiorder(
        multi_data=body, auth_token=auth_token, broker=broker_name, config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
