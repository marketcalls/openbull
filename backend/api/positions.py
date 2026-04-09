"""
External API - Positions endpoint.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/positions")
async def api_positions(request: Request):
    """Get open positions via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.positions_service import get_positions_with_auth

    async for db in get_db():
        api_user = await get_api_user(request, db)
        break

    user_id, auth_token, broker_name, config = api_user

    success, response_data, status_code = get_positions_with_auth(
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data
