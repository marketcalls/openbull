"""
External API - Options order endpoint.
Place an option order by specifying offset (ATM/ITM/OTM) instead of an exact strike.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/optionsorder")
async def api_options_order(request: Request):
    """Place a single options order via offset spec."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.place_options_order_service import place_options_order

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in optionsorder endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    success, response_data, status_code = place_options_order(
        options_data=body, auth_token=auth_token, broker=broker_name, config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
