"""
External API - Option Greeks endpoint.
Mirrors openalgo restx_api/option_greeks.py: same request body, response shape,
and Black-76 model (NFO/BFO/CDS/MCX).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _coerce_optional_float(value, field: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, f"{field} must be a number"


@router.post("/optiongreeks")
async def api_option_greeks(request: Request):
    """Calculate Option Greeks (Δ/Γ/Θ/V/ρ) and Implied Volatility.

    Request body:
      - apikey (str): API key (auth handled upstream)
      - symbol (str): Option symbol e.g. NIFTY28NOV2424000CE
      - exchange (str): NFO / BFO / CDS / MCX
      - interest_rate (float, optional): Risk-free rate %; defaults to 0.
      - forward_price (float, optional): Custom forward / synthetic futures price.
        If provided, the underlying fetch is skipped.
      - underlying_symbol (str, optional): Override underlying symbol used for
        the spot quote (e.g. "NIFTY28NOV24FUT").
      - underlying_exchange (str, optional): Override underlying exchange.
      - expiry_time (str, optional): Custom expiry time HH:MM (overrides the
        per-exchange default — 15:30 for NFO/BFO, 12:30 for CDS, 23:30 for MCX).
    """
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

    interest_rate, err = _coerce_optional_float(body.get("interest_rate"), "interest_rate")
    if err:
        return JSONResponse(content={"status": "error", "message": err}, status_code=400)
    forward_price, err = _coerce_optional_float(body.get("forward_price"), "forward_price")
    if err:
        return JSONResponse(content={"status": "error", "message": err}, status_code=400)

    underlying_symbol = body.get("underlying_symbol")
    underlying_exchange = body.get("underlying_exchange")
    expiry_time = body.get("expiry_time")

    success, response_data, status_code = get_option_greeks(
        option_symbol=symbol,
        exchange=exchange,
        interest_rate=interest_rate,
        forward_price=forward_price,
        underlying_symbol=underlying_symbol,
        underlying_exchange=underlying_exchange,
        expiry_time=expiry_time,
        auth_token=auth_token, broker=broker_name, config=config,
    )
    return JSONResponse(content=response_data, status_code=status_code)
