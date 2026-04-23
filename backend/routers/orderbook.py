"""
Orderbook router - GET /web/orderbook
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_broker_context, BrokerContext
from backend.services.orderbook_service import get_orderbook_with_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web", tags=["orderbook"])


@router.get("/orderbook")
async def orderbook(ctx: BrokerContext = Depends(get_broker_context)):
    """Get order book data from the broker."""
    success, response_data, status_code = get_orderbook_with_auth(
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
        user_id=ctx.user.id,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data.get("message", "Failed to fetch orderbook"))

    return response_data
