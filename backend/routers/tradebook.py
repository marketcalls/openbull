"""
Tradebook router - GET /web/tradebook
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_broker_context, BrokerContext
from backend.services.tradebook_service import get_tradebook_with_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web", tags=["tradebook"])


@router.get("/tradebook")
async def tradebook(ctx: BrokerContext = Depends(get_broker_context)):
    """Get trade book data from the broker."""
    success, response_data, status_code = get_tradebook_with_auth(
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
        user_id=ctx.user.id,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data.get("message", "Failed to fetch tradebook"))

    return response_data
