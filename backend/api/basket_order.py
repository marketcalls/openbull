"""
External API - Basket order endpoint.
Places multiple orders concurrently. BUY legs execute before SELL legs.
Response format follows OpenAlgo standard.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/basketorder")
async def api_basket_order(request: Request):
    """Place multiple orders simultaneously."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.basket_order_service import place_basket_order

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(
            content={"status": "error", "message": message},
            status_code=e.status_code,
        )
    except Exception:
        logger.exception("Unexpected error in basketorder endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    basket_data = {
        "strategy": body.get("strategy", ""),
        "orders": body.get("orders", []),
    }

    success, response_data, status_code = place_basket_order(
        basket_data=basket_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
