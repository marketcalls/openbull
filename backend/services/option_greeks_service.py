"""
Option Greeks service - Black-76 model (options on futures, used for Indian F&O).
Pure-math implementation: no scipy/py_vollib dependency.

Greeks:
  Delta = e^(-rT) * N(d1)              [call]
        = -e^(-rT) * N(-d1)            [put]
  Gamma = e^(-rT) * phi(d1) / (F*sigma*sqrt(T))
  Vega  = F * e^(-rT) * phi(d1) * sqrt(T)         (per 1 vol unit; we report per 1%)
  Theta = -F*e^(-rT)*phi(d1)*sigma/(2*sqrt(T)) - r*premium      (per year; we report per day)
  Rho   = -T * premium                 (Black-76 rho)

IV solved via bisection (robust, no derivative needed).
"""

import logging
import math
import re
from datetime import datetime, time
from typing import Any

from backend.services.market_data_service import _run_query
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)

EXCHANGE_EXPIRY_TIME = {
    "NFO": time(15, 30),
    "BFO": time(15, 30),
    "CDS": time(17, 0),
    "MCX": time(23, 30),
}
DEFAULT_INTEREST_RATE_PCT = 0.0  # match OpenAlgo default; user can override
INDEX_TO_OPTIONS_EXCHANGE = {
    "NFO": ("NSE_INDEX", "NSE"),
    "BFO": ("BSE_INDEX", "BSE"),
}

NSE_INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "INDIAVIX"}
BSE_INDEX_UNDERLYINGS = {"SENSEX", "BANKEX", "SENSEX50"}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _black76_price(F: float, K: float, T: float, r: float, sigma: float, flag: str) -> float:
    if T <= 0 or sigma <= 0:
        intrinsic = max(F - K, 0) if flag == "c" else max(K - F, 0)
        return intrinsic * math.exp(-r * T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)
    if flag == "c":
        return disc * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
    return disc * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))


def _implied_vol(price: float, F: float, K: float, T: float, r: float, flag: str) -> float | None:
    """Bisection over sigma in [1e-6, 5.0]. Returns None if not bracketed."""
    intrinsic = max(F - K, 0) if flag == "c" else max(K - F, 0)
    discounted_intrinsic = intrinsic * math.exp(-r * T)
    if price <= discounted_intrinsic + 1e-9:
        return None  # caller should treat as deep-ITM-no-time-value

    lo, hi = 1e-6, 5.0
    p_lo = _black76_price(F, K, T, r, lo, flag)
    p_hi = _black76_price(F, K, T, r, hi, flag)
    if not (p_lo <= price <= p_hi):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        p_mid = _black76_price(F, K, T, r, mid, flag)
        if abs(p_mid - price) < 1e-6:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _greeks(F: float, K: float, T: float, r: float, sigma: float, flag: str, premium: float) -> dict:
    if T <= 0 or sigma <= 0:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}

    sqrtT = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)

    if flag == "c":
        delta = disc * _norm_cdf(d1)
        theta_year = -F * disc * pdf_d1 * sigma / (2 * sqrtT) - r * premium
    else:
        delta = -disc * _norm_cdf(-d1)
        theta_year = -F * disc * pdf_d1 * sigma / (2 * sqrtT) - r * premium

    gamma = disc * pdf_d1 / (F * sigma * sqrtT)
    vega_per_unit = F * disc * pdf_d1 * sqrtT
    rho = -T * premium  # Black-76 rho approximation

    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta_year / 365.0, 6),  # per calendar day
        "vega": round(vega_per_unit / 100.0, 6),  # per 1% vol move
        "rho": round(rho / 100.0, 6),
    }


def _parse_option_symbol(symbol: str) -> tuple[str, str, float, str] | None:
    """NIFTY28APR2624250CE -> (NIFTY, 28APR26, 24250.0, CE)."""
    m = re.match(r"^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$", symbol.upper())
    if not m:
        return None
    return m.group(1), m.group(2), float(m.group(3)), m.group(4)


def _expiry_datetime(expiry_ddmmmyy: str, exchange: str) -> datetime:
    expiry_date = datetime.strptime(expiry_ddmmmyy, "%d%b%y").date()
    expiry_time = EXCHANGE_EXPIRY_TIME.get(exchange.upper(), time(15, 30))
    return datetime.combine(expiry_date, expiry_time)


def _quote_exchange_for_underlying(base_symbol: str, options_exchange: str) -> str:
    if base_symbol in NSE_INDEX_UNDERLYINGS:
        return "NSE_INDEX"
    if base_symbol in BSE_INDEX_UNDERLYINGS:
        return "BSE_INDEX"
    return "NSE" if options_exchange.upper() == "NFO" else "BSE"


def _lookup_token_meta(symbol: str, exchange: str) -> dict | None:
    rows = _run_query(
        "SELECT symbol, lotsize, tick_size FROM symtoken WHERE symbol=:s AND exchange=:e",
        {"s": symbol, "e": exchange},
    )
    if not rows:
        return None
    return {"symbol": rows[0][0], "lotsize": rows[0][1], "tick_size": rows[0][2]}


def get_option_greeks(
    symbol: str,
    exchange: str,
    interest_rate: float | None,
    spot_price: float | None,
    option_price: float | None,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Compute Black-76 Greeks + IV for an option symbol."""
    try:
        parsed = _parse_option_symbol(symbol)
        if not parsed:
            return False, {"status": "error", "message": f"Cannot parse option symbol: {symbol}"}, 400

        base_symbol, expiry_ddmmmyy, strike, opt_type = parsed
        meta = _lookup_token_meta(symbol, exchange)
        if not meta:
            return False, {"status": "error", "message": f"Symbol {symbol} not found in {exchange}"}, 404

        expiry_dt = _expiry_datetime(expiry_ddmmmyy, exchange)
        now = datetime.now()
        seconds_to_expiry = (expiry_dt - now).total_seconds()
        if seconds_to_expiry <= 0:
            return False, {
                "status": "error",
                "message": f"Option already expired on {expiry_dt.strftime('%d-%b-%Y')}",
            }, 400
        T_years = seconds_to_expiry / (365.0 * 24 * 3600)

        # Fetch spot if not provided
        if spot_price is None:
            quote_exchange = _quote_exchange_for_underlying(base_symbol, exchange)
            ok, qdata, status_code = get_quotes_with_auth(
                symbol=base_symbol, exchange=quote_exchange,
                auth_token=auth_token, broker=broker, config=config,
            )
            if not ok:
                return False, {
                    "status": "error",
                    "message": f"Failed to fetch spot for {base_symbol}: {qdata.get('message', 'unknown')}",
                }, status_code
            spot_price = qdata.get("data", {}).get("ltp")
            if spot_price is None:
                return False, {"status": "error", "message": "Spot LTP unavailable"}, 500

        # Fetch option price if not provided
        if option_price is None:
            ok, qdata, status_code = get_quotes_with_auth(
                symbol=symbol, exchange=exchange,
                auth_token=auth_token, broker=broker, config=config,
            )
            if not ok:
                return False, {
                    "status": "error",
                    "message": f"Failed to fetch option price for {symbol}: {qdata.get('message', 'unknown')}",
                }, status_code
            option_price = qdata.get("data", {}).get("ltp")
            if option_price is None:
                return False, {"status": "error", "message": "Option LTP unavailable"}, 500

        spot_price = float(spot_price)
        option_price = float(option_price)
        if spot_price <= 0 or option_price <= 0:
            return False, {"status": "error", "message": "spot_price and option_price must be positive"}, 400

        r = (interest_rate if interest_rate is not None else DEFAULT_INTEREST_RATE_PCT) / 100.0
        flag = "c" if opt_type == "CE" else "p"

        intrinsic = max(spot_price - strike, 0) if opt_type == "CE" else max(strike - spot_price, 0)
        time_value = option_price - intrinsic

        if time_value <= 0.01 and intrinsic > 0:
            return True, {
                "status": "success",
                "symbol": symbol, "exchange": exchange,
                "underlying": base_symbol,
                "strike": strike, "option_type": opt_type,
                "expiry_date": expiry_dt.strftime("%d-%b-%Y"),
                "days_to_expiry": round(seconds_to_expiry / 86400.0, 4),
                "spot_price": round(spot_price, 2), "option_price": round(option_price, 2),
                "intrinsic_value": round(intrinsic, 2),
                "time_value": round(max(time_value, 0), 2),
                "interest_rate": round(r * 100, 2),
                "implied_volatility": 0.0,
                "greeks": {
                    "delta": 1.0 if opt_type == "CE" else -1.0,
                    "gamma": 0, "theta": 0, "vega": 0, "rho": 0,
                },
                "note": "Deep ITM with no time value - theoretical greeks returned",
            }, 200

        iv = _implied_vol(option_price, spot_price, strike, T_years, r, flag)
        if iv is None:
            return False, {
                "status": "error",
                "message": "Could not solve implied volatility (price out of model bounds)",
            }, 500

        greeks = _greeks(spot_price, strike, T_years, r, iv, flag, option_price)

        return True, {
            "status": "success",
            "symbol": symbol, "exchange": exchange,
            "underlying": base_symbol,
            "strike": strike, "option_type": opt_type,
            "expiry_date": expiry_dt.strftime("%d-%b-%Y"),
            "days_to_expiry": round(seconds_to_expiry / 86400.0, 4),
            "spot_price": round(spot_price, 2),
            "option_price": round(option_price, 2),
            "intrinsic_value": round(intrinsic, 2),
            "time_value": round(time_value, 2),
            "interest_rate": round(r * 100, 2),
            "implied_volatility": round(iv * 100, 2),
            "greeks": greeks,
        }, 200

    except Exception as e:
        logger.exception("Error in get_option_greeks: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
