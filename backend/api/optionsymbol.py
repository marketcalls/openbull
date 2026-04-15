"""
External API - Option symbol resolver.
Resolves underlying + expiry + offset (ATM/ITMn/OTMn) + option_type into a tradable symbol.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/optionsymbol")
async def api_option_symbol(request: Request):
    """Resolve option symbol from underlying spec."""
    from backend.dependencies import get_api_user, get_db
    from backend.services.option_symbol_service import get_option_symbol

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
        logger.exception("Unexpected error in optionsymbol endpoint")
        return JSONResponse(
            content={"status": "error", "message": "An unexpected error occurred"},
            status_code=500,
        )

    user_id, auth_token, broker_name, config = api_user

    try:
        body = await request.json()
    except Exception:
        body = {}

    underlying = body.get("underlying")
    exchange = body.get("exchange")
    offset = body.get("offset")
    option_type = body.get("option_type")
    expiry_date = body.get("expiry_date")

    missing = [
        name for name, val in [
            ("underlying", underlying),
            ("exchange", exchange),
            ("offset", offset),
            ("option_type", option_type),
        ] if not val
    ]
    if missing:
        return JSONResponse(
            content={"status": "error", "message": f"Missing mandatory field(s): {', '.join(missing)}"},
            status_code=400,
        )

    if option_type.upper() not in ("CE", "PE"):
        return JSONResponse(
            content={"status": "error", "message": "option_type must be CE or PE"},
            status_code=400,
        )

    success, response_data, status_code = get_option_symbol(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        offset=offset,
        option_type=option_type,
        auth_token=auth_token,
        broker=broker_name,
        config=config,
    )

    return JSONResponse(content=response_data, status_code=status_code)
