"""
Holdings router - GET /web/holdings
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_broker_context, BrokerContext
from backend.services.holdings_service import get_holdings_with_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web", tags=["holdings"])


@router.get("/holdings")
async def holdings(ctx: BrokerContext = Depends(get_broker_context)):
    """Get holdings/portfolio data from the broker."""
    success, response_data, status_code = get_holdings_with_auth(
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
        user_id=ctx.user.id,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data.get("message", "Failed to fetch holdings"))

    return response_data
