"""
External API - Symbol search endpoint.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.services.market_data_service import search_symbols_api

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search")
async def api_search(request: Request):
    """Search symbols via the external API."""
    from backend.dependencies import get_api_user, get_db

    try:
        async for db in get_db():
            await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in search endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        body = {}

    query = body.get("query")
    exchange = body.get("exchange")
    if not query:
        return JSONResponse(content={"status": "error", "message": "query is required"}, status_code=400)

    success, response_data, status_code = search_symbols_api(query, exchange)
    return JSONResponse(content=response_data, status_code=status_code)
