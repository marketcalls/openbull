"""
External API - Option Greeks endpoint.
Returns Black-76 Greeks (delta, gamma, theta, vega, rho) and implied volatility
for a given option symbol.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/optiongreeks")
async def api_option_greeks(request: Request):
    """Compute Greeks + IV for an option symbol."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.option_greeks_service import get_option_greeks

    try:
        async for db in get_db():
            api_user = await get_api_user(request, db)
            break
    except HTTPException as e:
        message = e.detail if isinstance(e.detail, str) else str(e.detail)
        return JSONResponse(content={"status": "error", "message": message}, status_code=e.status_code)
    except Exception:
        logger.exception("Unexpected error in optiongreeks endpoint")
        return JSONResponse(content={"status": "error", "message": "An unexpected error occurred"}, status_code=500)

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    symbol = body.get("symbol")
    exchange = body.get("exchange")
    if not symbol or not exchange:
        return JSONResponse(
            content={"status": "error", "message": "symbol and exchange are required"},
            status_code=400,
        )

    interest_rate = body.get("interest_rate")
    spot_price = body.get("spot_price")
    option_price = body.get("option_price")

    success, response_data, status_code = get_option_greeks(
        symbol=symbol, exchange=exchange,
        interest_rate=float(interest_rate) if interest_rate is not None else None,
        spot_price=float(spot_price) if spot_price is not None else None,
        option_price=float(option_price) if option_price is not None else None,
        auth_token=auth_token, broker=broker_name, config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
