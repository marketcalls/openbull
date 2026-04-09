"""
External API - Orderbook endpoint.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/orderbook")
async def api_orderbook(request: Request):
    """Get order book data via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.orderbook_service import get_orderbook_with_auth

    async for db in get_db():
        api_user = await get_api_user(request, db)
        break

    user_id, auth_token, broker_name, config = api_user

    success, response_data, status_code = get_orderbook_with_auth(
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data
