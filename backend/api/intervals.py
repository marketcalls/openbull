"""
External API - Supported intervals endpoint.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.services.market_data_service import get_supported_intervals

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/intervals")
async def api_intervals(request: Request):
    """Get supported candle intervals via the external API."""
    from backend.dependencies import get_api_user, get_db

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in intervals endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user
    success, response_data, status_code = get_supported_intervals(broker_name)
    return JSONResponse(content=response_data, status_code=status_code)
