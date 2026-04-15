"""
External API - Place order, modify order, cancel order endpoints.
All endpoints accept JSON body with 'apikey' field for authentication.
Response format follows OpenAlgo standard:
  Success: {"status": "success", ...}
  Error:   {"status": "error", "message": "..."}
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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


async def _get_request_body(request: Request) -> dict:
    """Safely parse JSON request body."""
    try:
        return await request.json()
    except Exception:
        return {}


@router.post("/placeorder")
async def api_place_order(request: Request):
    """Place a regular order via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    body = await _get_request_body(request)
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

    return JSONResponse(content=response_data, status_code=status_code)


@router.post("/placesmartorder")
async def api_place_smart_order(request: Request):
    """Place a smart (position-aware) order via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    body = await _get_request_body(request)
    order_data = {
        "symbol": body.get("symbol"),
        "exchange": body.get("exchange"),
        "action": body.get("action", "BUY"),
        "quantity": body.get("quantity", "0"),
        # OpenAlgo defaults: pricetype=MARKET, product=MIS when omitted
        "pricetype": body.get("pricetype", "MARKET"),
        "product": body.get("product", "MIS"),
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

    return JSONResponse(content=response_data, status_code=status_code)


@router.post("/modifyorder")
async def api_modify_order(request: Request):
    """Modify an existing order via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    body = await _get_request_body(request)
    # Accept (but ignore for the broker call) the OpenAlgo-style metadata fields
    # symbol/action/exchange/product/strategy so that requests built for OpenAlgo
    # work as-is. The broker modify endpoint only needs the fields below.
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

    return JSONResponse(content=response_data, status_code=status_code)


@router.post("/cancelorder")
async def api_cancel_order(request: Request):
    """Cancel a specific order via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    body = await _get_request_body(request)
    orderid = body.get("orderid")
    # OpenAlgo accepts an optional `strategy` field for telemetry — accepted here
    # for request-shape parity even though the broker call doesn't use it.
    _strategy = body.get("strategy", "")
    if not orderid:
        return JSONResponse(
            content={"status": "error", "message": "orderid is required"},
            status_code=400,
        )

    success, response_data, status_code = cancel_order_service(
        orderid=orderid,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)


@router.post("/cancelallorder")
async def api_cancel_all_orders(request: Request):
    """Cancel all open orders via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    # OpenAlgo accepts an optional `strategy` for telemetry — read but unused.
    body = await _get_request_body(request)
    _strategy = body.get("strategy", "")

    success, response_data, status_code = cancel_all_orders_service(
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)


@router.post("/closeposition")
async def api_close_all_positions(request: Request):
    """Close all open positions via the external API."""
    try:
        api_user = await _resolve_api_user(request)
    except Exception as e:
        return _error_from_exception(e)

    user_id, auth_token, broker_name, config = api_user

    body = await _get_request_body(request)
    api_key = body.get("apikey", "")
    # OpenAlgo accepts an optional `strategy` for telemetry — read but unused.
    _strategy = body.get("strategy", "")

    success, response_data, status_code = close_all_positions_service(
        api_key=api_key,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)


def _error_from_exception(exc: Exception) -> JSONResponse:
    """Convert an HTTPException or generic exception to OpenAlgo error format."""
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            content={"status": "error", "message": message},
            status_code=exc.status_code,
        )
    logger.exception("Unexpected error in API endpoint")
    return JSONResponse(
        content={"status": "error", "message": "An unexpected error occurred"},
        status_code=500,
    )
