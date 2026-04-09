"""
External API - Holdings endpoint.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/holdings")
async def api_holdings(request: Request):
    """Get holdings/portfolio data via the external API."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.holdings_service import get_holdings_with_auth

    async for db in get_db():
        api_user = await get_api_user(request, db)
        break

    user_id, auth_token, broker_name, config = api_user

    success, response_data, status_code = get_holdings_with_auth(
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data
