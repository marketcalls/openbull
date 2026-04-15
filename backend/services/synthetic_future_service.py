"""
Synthetic future service.

A synthetic long future is built from BUY ATM CE + SELL ATM PE at the same strike.
Synthetic Price = Strike + Call LTP - Put LTP
Basis = Synthetic - Spot (cost of carry indicator).
"""

import logging
from typing import Any

from backend.services.option_symbol_service import get_option_symbol
from backend.services.quotes_service import get_multi_quotes_with_auth

logger = logging.getLogger(__name__)


def calculate_synthetic_future(
    underlying: str,
    exchange: str,
    expiry_date: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Calculate synthetic future from ATM CE + ATM PE."""
    try:
        ok_ce, call_resp, status_code = get_option_symbol(
            underlying=underlying, exchange=exchange, expiry_date=expiry_date,
            offset="ATM", option_type="CE",
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_ce:
            return False, call_resp, status_code

        ok_pe, put_resp, status_code = get_option_symbol(
            underlying=underlying, exchange=exchange, expiry_date=expiry_date,
            offset="ATM", option_type="PE",
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_pe:
            return False, put_resp, status_code

        call_symbol = call_resp["symbol"]
        put_symbol = put_resp["symbol"]
        atm_strike = float(call_resp["strike"])
        underlying_ltp = float(call_resp["underlying_ltp"])
        options_exchange = call_resp["exchange"]

        ok_q, mqdata, status_code = get_multi_quotes_with_auth(
            symbols_list=[
                {"symbol": call_symbol, "exchange": options_exchange},
                {"symbol": put_symbol, "exchange": options_exchange},
            ],
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_q:
            return False, mqdata, status_code

        call_ltp = put_ltp = None
        for q in mqdata.get("results", []):
            if q.get("symbol") == call_symbol:
                call_ltp = q.get("ltp")
            elif q.get("symbol") == put_symbol:
                put_ltp = q.get("ltp")

        if call_ltp is None:
            return False, {"status": "error", "message": f"LTP unavailable for {call_symbol}"}, 500
        if put_ltp is None:
            return False, {"status": "error", "message": f"LTP unavailable for {put_symbol}"}, 500

        synthetic_price = atm_strike + float(call_ltp) - float(put_ltp)
        basis = synthetic_price - underlying_ltp

        return True, {
            "status": "success",
            "underlying": underlying.upper(),
            "underlying_ltp": underlying_ltp,
            "expiry": expiry_date.upper() if expiry_date else call_resp.get("expiry"),
            "atm_strike": atm_strike,
            "call_symbol": call_symbol,
            "call_ltp": float(call_ltp),
            "put_symbol": put_symbol,
            "put_ltp": float(put_ltp),
            "synthetic_future_price": round(synthetic_price, 2),
            "basis": round(basis, 2),
        }, 200

    except Exception as e:
        logger.exception("Error in calculate_synthetic_future: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
