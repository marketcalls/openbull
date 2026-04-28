"""
Option symbol service - resolves an option symbol from underlying, expiry,
offset (ATM/ITMn/OTMn) and option_type using actual strikes from symtoken.
"""

import asyncio
import concurrent.futures
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)

# Cache of sorted strike lists keyed by (base_symbol, expiry, option_type, exchange)
_STRIKES_CACHE: dict[tuple[str, str, str, str], list[float]] = {}


async def _query_db(query_str: str, params: dict) -> list:
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(text(query_str), params)
            return result.fetchall()
    finally:
        await engine.dispose()


def _run_query(query_str: str, params: dict) -> list:
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _query_db(query_str, params)).result()


def _parse_underlying(underlying: str) -> tuple[str, str | None]:
    """Extract base symbol and optional embedded expiry (e.g. NIFTY28APR26FUT -> NIFTY, 28APR26)."""
    m = re.match(r"^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(?:FUT)?$", underlying.upper())
    if m:
        return m.group(1), m.group(2)
    return underlying.upper(), None


def _option_exchange_for(quote_exchange: str) -> str:
    eu = quote_exchange.upper()
    if eu in ("NSE", "NSE_INDEX"):
        return "NFO"
    if eu in ("BSE", "BSE_INDEX"):
        return "BFO"
    if eu == "MCX":
        return "MCX"
    if eu == "CDS":
        return "CDS"
    return "NFO"


def _quote_exchange_for(base_symbol: str, requested_exchange: str) -> str:
    eu = requested_exchange.upper()
    if eu in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE", "MCX", "CDS"):
        return eu
    if eu in ("NFO", "BFO"):
        if base_symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "INDIAVIX"}:
            return "NSE_INDEX"
        if base_symbol in {"SENSEX", "BANKEX", "SENSEX50"}:
            return "BSE_INDEX"
        return "NSE" if eu == "NFO" else "BSE"
    return eu


def _fetch_available_strikes(
    base_symbol: str, expiry_ddmmmyy: str, option_type: str, exchange: str
) -> list[float]:
    """Load distinct strikes for (underlying, expiry, type) sorted ascending. Cached in-memory."""
    cache_key = (base_symbol.upper(), expiry_ddmmmyy.upper(), option_type.upper(), exchange.upper())
    if cache_key in _STRIKES_CACHE:
        return _STRIKES_CACHE[cache_key]

    expiry_formatted = f"{expiry_ddmmmyy[:2]}-{expiry_ddmmmyy[2:5]}-{expiry_ddmmmyy[5:]}".upper()
    rows = _run_query(
        "SELECT DISTINCT strike FROM symtoken "
        "WHERE symbol LIKE :prefix AND symbol LIKE :suffix "
        "AND expiry = :expiry AND instrumenttype = :itype AND exchange = :exch "
        "AND strike IS NOT NULL ORDER BY strike",
        {
            "prefix": f"{base_symbol}{expiry_ddmmmyy}%",
            "suffix": f"%{option_type.upper()}",
            "expiry": expiry_formatted,
            "itype": option_type.upper(),
            "exch": exchange.upper(),
        },
    )
    strikes = [row[0] for row in rows if row[0] is not None]
    _STRIKES_CACHE[cache_key] = strikes
    logger.info(
        "Cached %d strikes for %s %s %s on %s",
        len(strikes), base_symbol, expiry_ddmmmyy, option_type, exchange,
    )
    return strikes


def _find_near_month_futures(base_symbol: str, exchange: str) -> dict | None:
    """Pick the nearest non-expired FUT contract for a base symbol on an exchange.

    Used as the ATM-pricing source for option chains on exchanges that don't
    have a tradable spot (MCX, CDS) — callers pass ``underlying="CRUDEOIL"``
    and we resolve ``CRUDEOIL{DDMMMYY}FUT`` for the soonest expiry that hasn't
    rolled off yet.
    """
    rows = _run_query(
        "SELECT symbol, exchange, expiry FROM symtoken "
        "WHERE symbol LIKE :pattern AND exchange = :exch AND instrumenttype = 'FUT' "
        "AND expiry IS NOT NULL AND expiry != ''",
        {"pattern": f"{base_symbol.upper()}%FUT", "exch": exchange.upper()},
    )
    if not rows:
        return None

    today = datetime.now().date()

    def parse_exp(expiry_str: str):
        try:
            return datetime.strptime(expiry_str, "%d-%b-%y").date()
        except (ValueError, TypeError):
            return None

    candidates = []
    for sym, exch, exp in rows:
        d = parse_exp(exp or "")
        if d is None:
            continue
        if d >= today:
            candidates.append((d, sym, exch))

    if not candidates:
        # All FUT contracts have expired — fall back to the latest known one
        # so the chain page still renders something instead of 404'ing.
        for sym, exch, exp in rows:
            d = parse_exp(exp or "")
            if d is not None:
                candidates.append((d, sym, exch))
        if not candidates:
            return None

    candidates.sort(key=lambda c: c[0])
    _date, sym, exch = candidates[0]
    return {"symbol": sym, "exchange": exch}


def _find_atm(ltp: float, strikes: list[float]) -> float | None:
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - ltp))


def _apply_offset(atm: float, offset: str, option_type: str, strikes: list[float]) -> float | None:
    offset = offset.upper()
    option_type = option_type.upper()

    if atm not in strikes:
        return None
    atm_idx = strikes.index(atm)

    if offset == "ATM":
        return atm

    if offset.startswith("ITM"):
        n = int(offset[3:])
        target_idx = atm_idx - n if option_type == "CE" else atm_idx + n
    elif offset.startswith("OTM"):
        n = int(offset[3:])
        target_idx = atm_idx + n if option_type == "CE" else atm_idx - n
    else:
        return None

    if target_idx < 0 or target_idx >= len(strikes):
        return None
    return strikes[target_idx]


def _format_strike(strike: float) -> str:
    return str(int(strike)) if strike == int(strike) else str(strike)


def _lookup_option_in_db(symbol: str, exchange: str) -> dict | None:
    rows = _run_query(
        "SELECT symbol, brsymbol, exchange, token, expiry, strike, lotsize, "
        "instrumenttype, tick_size FROM symtoken WHERE symbol = :sym AND exchange = :exch",
        {"sym": symbol, "exch": exchange.upper()},
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "symbol": row[0],
        "brsymbol": row[1],
        "exchange": row[2],
        "token": row[3],
        "expiry": row[4],
        "strike": row[5],
        "lotsize": row[6],
        "instrumenttype": row[7],
        "tick_size": row[8],
    }


def get_option_symbol(
    underlying: str,
    exchange: str,
    expiry_date: str | None,
    offset: str,
    option_type: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve option symbol from underlying+expiry+offset+type."""
    try:
        base_symbol, embedded_expiry = _parse_underlying(underlying)
        final_expiry = (expiry_date or embedded_expiry or "").upper()
        if not final_expiry:
            return False, {
                "status": "error",
                "message": "Expiry date required. Provide via expiry_date or embed in underlying (e.g. NIFTY28APR26FUT).",
            }, 400

        quote_exchange = _quote_exchange_for(base_symbol, exchange)
        options_exchange = _option_exchange_for(quote_exchange)

        quote_symbol = base_symbol if quote_exchange in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE") else underlying.upper()
        ok, quote_data, status_code = get_quotes_with_auth(
            symbol=quote_symbol, exchange=quote_exchange,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok:
            return False, {
                "status": "error",
                "message": f"Failed to fetch LTP for {quote_symbol}: {quote_data.get('message', 'unknown error')}",
            }, status_code

        ltp = quote_data.get("data", {}).get("ltp")
        if ltp is None:
            return False, {"status": "error", "message": f"LTP not available for {quote_symbol}"}, 500

        strikes = _fetch_available_strikes(base_symbol, final_expiry, option_type, options_exchange)
        if not strikes:
            return False, {
                "status": "error",
                "message": f"No strikes found for {base_symbol} {final_expiry} on {options_exchange}.",
            }, 404

        atm = _find_atm(float(ltp), strikes)
        target_strike = _apply_offset(atm, offset, option_type, strikes)
        if target_strike is None:
            return False, {
                "status": "error",
                "message": f"Offset {offset} out of range for available strikes.",
            }, 400

        option_symbol = f"{base_symbol}{final_expiry}{_format_strike(target_strike)}{option_type.upper()}"
        details = _lookup_option_in_db(option_symbol, options_exchange)
        if not details:
            return False, {
                "status": "error",
                "message": f"Option {option_symbol} not found on {options_exchange}.",
            }, 404

        return True, {
            "status": "success",
            "symbol": details["symbol"],
            "exchange": details["exchange"],
            "lotsize": details["lotsize"],
            "tick_size": details["tick_size"],
            "strike": details["strike"],
            "expiry": details["expiry"],
            "underlying_ltp": float(ltp),
        }, 200

    except Exception as e:
        logger.exception("Error in get_option_symbol: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def clear_strikes_cache() -> None:
    _STRIKES_CACHE.clear()
