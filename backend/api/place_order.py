"""
External API - Place order, modify order, cancel order endpoints.
All endpoints accept JSON body with 'apikey' field for authentication.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from backend.services.order_service import (
    place_order,
    place_smart_order,
    modify_order_service,
    cancel_order_service,
    cancel_all_orders_service,
    close_all_positions_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_api_user(request: Request) -> tuple:
    """Resolve API user from request body. Returns (user_id, auth_token, broker_name, config)."""
    from backend.dependencies import get_api_user, get_db

    async for db in get_db():
        result = await get_api_user(request, db)
        return result


@router.post("/placeorder")
async def api_place_order(request: Request):
    """Place a regular order via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    body = await request.json()
    order_data = {
        "symbol": body.get("symbol"),
        "exchange": body.get("exchange"),
        "action": body.get("action"),
        "quantity": body.get("quantity"),
        "pricetype": body.get("pricetype"),
        "product": body.get("product"),
        "price": body.get("price", "0"),
        "trigger_price": body.get("trigger_price", "0"),
        "disclosed_quantity": body.get("disclosed_quantity", "0"),
        "strategy": body.get("strategy", ""),
    }

    success, response_data, status_code = place_order(
        order_data=order_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data


@router.post("/placesmartorder")
async def api_place_smart_order(request: Request):
    """Place a smart (position-aware) order via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    body = await request.json()
    order_data = {
        "symbol": body.get("symbol"),
        "exchange": body.get("exchange"),
        "action": body.get("action", "BUY"),
        "quantity": body.get("quantity", "0"),
        "pricetype": body.get("pricetype"),
        "product": body.get("product"),
        "price": body.get("price", "0"),
        "trigger_price": body.get("trigger_price", "0"),
        "disclosed_quantity": body.get("disclosed_quantity", "0"),
        "strategy": body.get("strategy", ""),
        "position_size": body.get("position_size", "0"),
    }

    success, response_data, status_code = place_smart_order(
        order_data=order_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data


@router.post("/modifyorder")
async def api_modify_order(request: Request):
    """Modify an existing order via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    body = await request.json()
    modify_data = {
        "orderid": body.get("orderid"),
        "quantity": body.get("quantity"),
        "price": body.get("price"),
        "pricetype": body.get("pricetype"),
        "trigger_price": body.get("trigger_price", "0"),
        "disclosed_quantity": body.get("disclosed_quantity", "0"),
    }

    success, response_data, status_code = modify_order_service(
        data=modify_data,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data


@router.post("/cancelorder")
async def api_cancel_order(request: Request):
    """Cancel a specific order via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    body = await request.json()
    orderid = body.get("orderid")
    if not orderid:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "orderid is required"})

    success, response_data, status_code = cancel_order_service(
        orderid=orderid,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data


@router.post("/cancelallorder")
async def api_cancel_all_orders(request: Request):
    """Cancel all open orders via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    success, response_data, status_code = cancel_all_orders_service(
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data


@router.post("/closeposition")
async def api_close_all_positions(request: Request):
    """Close all open positions via the external API."""
    user_id, auth_token, broker_name, config = await _resolve_api_user(request)

    body = await request.json()
    api_key = body.get("apikey", "")

    success, response_data, status_code = close_all_positions_service(
        api_key=api_key,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    if not success:
        raise HTTPException(status_code=status_code, detail=response_data)

    return response_data
