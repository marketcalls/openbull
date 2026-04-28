"""
Option Greeks service - Black-76 model (options on futures, used for Indian F&O).

API surface mirrors openalgo's services/option_greeks_service.py: same
parameter names (option_symbol, forward_price, underlying_symbol,
underlying_exchange, expiry_time, interest_rate), same response shape, and
same deep-ITM theoretical-Greeks fallback when IV solving fails.

Implementation is a pure-math Black-76 (no py_vollib / scipy / numba):
  Delta = e^(-rT) * N(d1)              [call]
        = -e^(-rT) * N(-d1)            [put]
  Gamma = e^(-rT) * phi(d1) / (F*sigma*sqrt(T))
  Vega  = F * e^(-rT) * phi(d1) * sqrt(T)         (per 1 vol unit; we report per 1%)
  Theta = -F*e^(-rT)*phi(d1)*sigma/(2*sqrt(T)) - r*premium      (per year; we report per day)
  Rho   = -T * premium                 (Black-76 rho approximation)

IV solved via bisection (robust, no derivative needed).
"""

import logging
import math
import re
from datetime import datetime
from typing import Any

from backend.services.market_data_service import _run_query  # noqa: F401  (re-export for compat)
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)


# ── Exchange-specific symbol sets (mirrors openalgo) ──────────────────

NSE_INDEX_SYMBOLS = {
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
    "NIFTYIT",
    "NIFTYPHARMA",
    "NIFTYBANK",
}
BSE_INDEX_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
CURRENCY_SYMBOLS = {"USDINR", "EURINR", "GBPINR", "JPYINR"}
COMMODITY_SYMBOLS = {
    "GOLD", "GOLDM", "GOLDPETAL",
    "SILVER", "SILVERM", "SILVERMIC",
    "CRUDEOIL", "CRUDEOILM",
    "NATURALGAS",
    "COPPER", "ZINC", "LEAD", "ALUMINIUM", "NICKEL",
    "COTTONCANDY", "MENTHAOIL",
}

# Backwards-compat aliases used by other modules in openbull.
NSE_INDEX_UNDERLYINGS = NSE_INDEX_SYMBOLS
BSE_INDEX_UNDERLYINGS = BSE_INDEX_SYMBOLS

# Default interest rates by exchange (annualized %). Match openalgo: 0 unless
# the caller explicitly overrides. Users should supply interest_rate when
# precision matters.
DEFAULT_INTEREST_RATES = {
    "NFO": 0,
    "BFO": 0,
    "CDS": 0,
    "MCX": 0,
}
DEFAULT_INTEREST_RATE_PCT = 0.0  # backwards-compat re-export

# Default expiry time per exchange (NFO/BFO: 15:30, CDS: 12:30, MCX: 23:30).
EXCHANGE_EXPIRY_TIME_DEFAULT = {
    "NFO": (15, 30),
    "BFO": (15, 30),
    "CDS": (12, 30),
    "MCX": (23, 30),
}


# ── Pure-math Black-76 ────────────────────────────────────────────────

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
        return None
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
    d2 = d1 - sigma * sqrtT  # noqa: F841 (kept for parity with classic derivation)
    disc = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)
    if flag == "c":
        delta = disc * _norm_cdf(d1)
    else:
        delta = -disc * _norm_cdf(-d1)
    theta_year = -F * disc * pdf_d1 * sigma / (2 * sqrtT) - r * premium
    gamma = disc * pdf_d1 / (F * sigma * sqrtT)
    vega_per_unit = F * disc * pdf_d1 * sqrtT
    rho = -T * premium  # Black-76 rho approximation
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta_year / 365.0, 4),  # per calendar day
        "vega": round(vega_per_unit / 100.0, 4),  # per 1% vol move
        "rho": round(rho / 100.0, 6),
    }


# ── Symbol parsing & exchange resolution (mirrors openalgo) ───────────

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_option_symbol(
    symbol: str, exchange: str, custom_expiry_time: str | None = None,
) -> tuple[str, datetime, float, str]:
    """Parse `NIFTY28APR2624000CE` → (NIFTY, expiry-datetime, 24000.0, "CE").

    `custom_expiry_time` (e.g. "15:30") overrides the per-exchange default
    expiry hour/minute. Strike supports decimals for currency options.
    """
    match = re.match(r"([A-Z]+)(\d{2})([A-Z]{3})(\d{2})([\d.]+)(CE|PE)", symbol.upper())
    if not match:
        raise ValueError(f"Invalid option symbol format: {symbol}")
    base_symbol, day, month_str, year, strike_str, opt_type = match.groups()

    if custom_expiry_time:
        parts = custom_expiry_time.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid expiry_time format: {custom_expiry_time}. Use HH:MM (e.g. '15:30')"
            )
        try:
            expiry_hour = int(parts[0])
            expiry_minute = int(parts[1])
        except ValueError:
            raise ValueError(
                f"Invalid expiry_time values: {custom_expiry_time}. Hour 0-23, minute 0-59"
            )
        if not (0 <= expiry_hour <= 23) or not (0 <= expiry_minute <= 59):
            raise ValueError(
                f"Invalid expiry_time values: {custom_expiry_time}. Hour 0-23, minute 0-59"
            )
    else:
        expiry_hour, expiry_minute = EXCHANGE_EXPIRY_TIME_DEFAULT.get(
            exchange.upper(), (15, 30)
        )

    expiry = datetime(
        int("20" + year), _MONTH_MAP[month_str], int(day), expiry_hour, expiry_minute
    )
    strike = float(strike_str)
    return base_symbol, expiry, strike, opt_type.upper()


def get_underlying_exchange(base_symbol: str, options_exchange: str) -> str:
    """Determine the exchange to fetch the underlying spot/forward from."""
    if base_symbol in NSE_INDEX_SYMBOLS:
        return "NSE_INDEX"
    if base_symbol in BSE_INDEX_SYMBOLS:
        return "BSE_INDEX"
    if base_symbol in CURRENCY_SYMBOLS or options_exchange.upper() == "CDS":
        return "CDS"
    if base_symbol in COMMODITY_SYMBOLS or options_exchange.upper() == "MCX":
        return "MCX"
    return "NSE"


def calculate_time_to_expiry(expiry: datetime) -> tuple[float, float]:
    """Years and days from now to expiry. Floors at 0.0001 years (~52 minutes)."""
    now = datetime.now()
    if expiry < now:
        return 0.0, 0.0
    delta = expiry - now
    days = delta.total_seconds() / (60 * 60 * 24)
    years = days / 365.0
    if years < 0.0001:
        years = 0.0001
        days = years * 365.0
    return years, days


# ── Snapshot Greeks calculation ───────────────────────────────────────

def _deep_itm_response(
    option_symbol: str, exchange: str, base_symbol: str, expiry: datetime,
    strike: float, opt_type: str, time_to_expiry_days: float,
    spot_price: float, option_price: float, intrinsic_value: float,
    time_value: float, interest_rate_pct: float, note: str,
) -> dict:
    return {
        "status": "success",
        "symbol": option_symbol,
        "exchange": exchange,
        "underlying": base_symbol,
        "strike": round(strike, 2),
        "option_type": opt_type,
        "expiry_date": expiry.strftime("%d-%b-%Y"),
        "days_to_expiry": round(time_to_expiry_days, 4),
        "spot_price": round(spot_price, 2),
        "option_price": round(option_price, 2),
        "intrinsic_value": round(intrinsic_value, 2),
        "time_value": round(max(time_value, 0), 2),
        "interest_rate": round(interest_rate_pct, 2),
        "implied_volatility": 0,
        "greeks": {
            "delta": 1.0 if opt_type == "CE" else -1.0,
            "gamma": 0,
            "theta": 0,
            "vega": 0,
            "rho": 0,
        },
        "note": note,
    }


def calculate_greeks(
    option_symbol: str,
    exchange: str,
    spot_price: float,
    option_price: float,
    interest_rate: float | None = None,
    expiry_time: str | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Calculate Black-76 IV + Greeks given prices. Pure math; no broker calls."""
    try:
        base_symbol, expiry, strike, opt_type = parse_option_symbol(
            option_symbol, exchange, expiry_time
        )
        years, days = calculate_time_to_expiry(expiry)
        if years <= 0:
            return False, {
                "status": "error",
                "message": f"Option has expired on {expiry.strftime('%d-%b-%Y')}",
            }, 400

        if interest_rate is None:
            interest_rate = DEFAULT_INTEREST_RATES.get(exchange.upper(), 0)
        r = float(interest_rate) / 100.0

        if spot_price <= 0 or option_price <= 0:
            return False, {
                "status": "error",
                "message": "Spot price and option price must be positive",
            }, 400
        if strike <= 0:
            return False, {"status": "error", "message": "Strike price must be positive"}, 400

        flag = "c" if opt_type == "CE" else "p"
        intrinsic = max(spot_price - strike, 0) if opt_type == "CE" else max(strike - spot_price, 0)
        time_value = option_price - intrinsic

        # Deep ITM with no time value → theoretical Greeks (matches openalgo).
        if time_value <= 0 or (intrinsic > 0 and time_value < 0.01):
            return True, _deep_itm_response(
                option_symbol, exchange, base_symbol, expiry, strike, opt_type, days,
                spot_price, option_price, intrinsic, time_value, float(interest_rate),
                "Deep ITM option with no time value - theoretical Greeks returned",
            ), 200

        iv_decimal = _implied_vol(option_price, spot_price, strike, years, r, flag)
        if iv_decimal is None or iv_decimal <= 0:
            # IV not bracketed → fall back to theoretical deep-ITM Greeks.
            return True, _deep_itm_response(
                option_symbol, exchange, base_symbol, expiry, strike, opt_type, days,
                spot_price, option_price, intrinsic, time_value, float(interest_rate),
                "IV calculation not possible - theoretical deep ITM Greeks returned",
            ), 200

        gks = _greeks(spot_price, strike, years, r, iv_decimal, flag, option_price)

        return True, {
            "status": "success",
            "symbol": option_symbol,
            "exchange": exchange,
            "underlying": base_symbol,
            "strike": round(strike, 2),
            "option_type": opt_type,
            "expiry_date": expiry.strftime("%d-%b-%Y"),
            "days_to_expiry": round(days, 4),
            "spot_price": round(spot_price, 2),
            "option_price": round(option_price, 2),
            "interest_rate": round(float(interest_rate), 2),
            "implied_volatility": round(iv_decimal * 100, 2),
            "greeks": gks,
        }, 200

    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.exception("Unexpected error in calculate_greeks: %s", e)
        return False, {"status": "error", "message": f"Failed to calculate option Greeks: {e}"}, 500


def get_option_greeks(
    option_symbol: str,
    exchange: str,
    interest_rate: float | None = None,
    forward_price: float | None = None,
    underlying_symbol: str | None = None,
    underlying_exchange: str | None = None,
    expiry_time: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Fetch live prices and compute Black-76 Greeks + IV.

    Mirrors openalgo's get_option_greeks signature and behavior:
      - `forward_price` overrides the underlying fetch entirely.
      - `underlying_symbol` / `underlying_exchange` override the auto-resolved
        spot lookup (useful for `NIFTY28NOV24FUT` synthetic forward).
      - `expiry_time` overrides the per-exchange default expiry HH:MM.
    """
    try:
        base_symbol, _expiry, _strike, _opt_type = parse_option_symbol(
            option_symbol, exchange, expiry_time
        )

        # Resolve the forward / spot price to use as F.
        if forward_price is not None:
            spot_price = float(forward_price)
        else:
            spot_symbol = underlying_symbol or base_symbol
            spot_exchange = underlying_exchange or get_underlying_exchange(base_symbol, exchange)
            ok, qresp, status_code = get_quotes_with_auth(
                symbol=spot_symbol, exchange=spot_exchange,
                auth_token=auth_token, broker=broker, config=config,
            )
            if not ok:
                return False, {
                    "status": "error",
                    "message": f"Failed to fetch underlying price: {qresp.get('message', 'Unknown error')}",
                }, status_code
            ltp = qresp.get("data", {}).get("ltp")
            if not ltp:
                return False, {"status": "error", "message": "Underlying LTP not available"}, 404
            spot_price = float(ltp)

        # Option LTP.
        ok, oresp, status_code = get_quotes_with_auth(
            symbol=option_symbol, exchange=exchange,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok:
            return False, {
                "status": "error",
                "message": f"Failed to fetch option price: {oresp.get('message', 'Unknown error')}",
            }, status_code
        option_ltp = oresp.get("data", {}).get("ltp")
        if not option_ltp:
            return False, {"status": "error", "message": "Option LTP not available"}, 404

        return calculate_greeks(
            option_symbol=option_symbol,
            exchange=exchange,
            spot_price=spot_price,
            option_price=float(option_ltp),
            interest_rate=interest_rate,
            expiry_time=expiry_time,
        )

    except ValueError as e:
        return False, {"status": "error", "message": str(e)}, 400
    except Exception as e:
        logger.exception("Error in get_option_greeks: %s", e)
        return False, {"status": "error", "message": f"Failed to get option Greeks: {e}"}, 500


# ── Backwards-compat aliases for callers still using old names ────────

# iv_chart_service imports `_expiry_datetime`; provide a thin shim.
def _expiry_datetime(expiry_ddmmmyy: str, exchange: str) -> datetime:
    """Deprecated: use parse_option_symbol() instead."""
    expiry_date = datetime.strptime(expiry_ddmmmyy, "%d%b%y").date()
    h, m = EXCHANGE_EXPIRY_TIME_DEFAULT.get(exchange.upper(), (15, 30))
    return datetime.combine(expiry_date, datetime.min.time()).replace(hour=h, minute=m)


# Old helper name retained for any internal callers.
_quote_exchange_for_underlying = get_underlying_exchange
