"""
External API - Split order endpoint.
Splits a large order into multiple smaller orders of specified size.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/splitorder")
async def api_split_order(request: Request):
    """Split a large order into multiple smaller orders."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.split_order_service import split_order

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
        logger.exception("Unexpected error in splitorder endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    split_data = {
        "symbol": body.get("symbol"),
        "exchange": body.get("exchange"),
        "action": body.get("action"),
        "quantity": body.get("quantity"),
        "splitsize": body.get("splitsize"),
        "pricetype": body.get("pricetype"),
        "product": body.get("product"),
        "price": body.get("price", "0"),
        "trigger_price": body.get("trigger_price", "0"),
        "disclosed_quantity": body.get("disclosed_quantity", "0"),
        "strategy": body.get("strategy", ""),
    }

    success, response_data, status_code = split_order(
        split_data=split_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
