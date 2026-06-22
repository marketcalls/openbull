"""Symbol resolver for the strategy module.

Two resolution paths:

1. **ATM mode** — delegates to :func:`backend.services.option_symbol_service.get_option_symbol`.
   The existing service already handles the FUT-as-underlying logic for MCX
   and the spot-index logic for NSE/BSE indices, so this is just a thin
   passthrough that exists so engine code (Phase 4+) doesn't import from
   `option_symbol_service` directly — keeps coupling at one point.

2. **Direct strike mode** — builds the OpenAlgo symbol per
   ``docs/design/symbol-format.md`` (``{base}{DDMMMYY}{strike}{CE|PE}``)
   and validates it exists in ``symtoken``. The user picked a specific
   strike from the wizard's strike picker, so the price is known good;
   we just confirm the contract is tradable.

Expiry-rank resolution (``weekly`` / ``monthly`` / ``current`` / ``next``)
also lives here — every consumer of "what's the rank-1 weekly expiry for
NIFTY" calls into this module so the rule is defined exactly once.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from backend.services.market_data_service import get_expiry_dates
from backend.services.option_symbol_service import (
    _apply_atm_percent,
    _apply_straddle_width,
    _fetch_available_strikes,
    _format_strike,
    _find_atm,
    _find_near_month_futures,
    _lookup_option_in_db,
    _option_exchange_for,
    _parse_underlying,
    _quote_exchange_for,
    _straddle_premium_target,
    _underlying_percent_premium,
    get_option_symbol,
)
from backend.services.quotes_service import get_multi_quotes_with_auth, get_quotes_with_auth

logger = logging.getLogger(__name__)


ExpiryRank = str  # "weekly" | "monthly" | "current" | "next"


def option_exchange_for(underlying_exchange: str) -> str:
    """Map the underlying's exchange to the option-chain exchange.

    NSE_INDEX/NSE → NFO, BSE_INDEX/BSE → BFO, MCX → MCX. Wraps the existing
    helper so callers don't reach into option_symbol_service privates.
    """
    return _option_exchange_for(underlying_exchange)


def _parse_iso_expiry(s: str) -> Optional[datetime]:
    """Parse a ``DD-MMM-YY`` (DB) or ``DDMMMYY`` (symbol) expiry string."""
    s = s.upper()
    for fmt in ("%d-%b-%y", "%d%b%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_last_of_calendar_month(target: datetime, all_dates: list[datetime]) -> bool:
    """True if `target` is the last expiry in its (year, month) within `all_dates`."""
    same_month = [d for d in all_dates if d.year == target.year and d.month == target.month]
    return bool(same_month) and target == max(same_month)


def resolve_expiry_rank(
    rank: ExpiryRank, sorted_dates: list[str]
) -> tuple[Optional[str], list[str]]:
    """Resolve a rank to a concrete ``DD-MMM-YY`` expiry from a sorted list.

    The list is the output of :func:`get_expiry_dates` — already filtered to
    non-expired entries and sorted ascending. Returns
    ``(resolved_or_None, all_input_dates)``.

    Canonical ranks (preferred):
      * ``current_week``  — rank-1 entry (the nearest expiry)
      * ``next_week``     — rank-2 entry
      * ``current_month`` — first entry that's the last expiry of its month
      * ``next_month``    — second entry that's the last expiry of its month

    Legacy aliases (kept so existing DB rows keep working):
      * ``weekly``  ≡ ``current_week``
      * ``current`` ≡ ``current_month`` (MCX-tab convention pre-2026-05)
      * ``next``    ≡ ``next_month``    (MCX-tab convention pre-2026-05)
      * ``monthly`` ≡ ``current_month``
    """
    if not sorted_dates:
        return None, sorted_dates

    parsed = [(_parse_iso_expiry(s), s) for s in sorted_dates]
    parsed_valid = [(d, s) for d, s in parsed if d is not None]

    # Weekly ranks: rank-1 / rank-2 of the full list (weekly+monthly mixed).
    if rank in ("current_week", "weekly"):
        return sorted_dates[0], sorted_dates

    if rank == "next_week":
        return (
            sorted_dates[1] if len(sorted_dates) >= 2 else sorted_dates[0]
        ), sorted_dates

    # Monthly ranks: first / second "last-of-calendar-month" entry.
    if rank in ("current_month", "monthly", "current"):
        all_dates = [d for d, _ in parsed_valid]
        for d, s in parsed_valid:
            if _is_last_of_calendar_month(d, all_dates):
                return s, sorted_dates
        # Fallback: no classifiable monthly (shouldn't happen on real data).
        return sorted_dates[0], sorted_dates

    if rank in ("next_month", "next"):
        all_dates = [d for d, _ in parsed_valid]
        monthlies = [
            s for d, s in parsed_valid if _is_last_of_calendar_month(d, all_dates)
        ]
        if len(monthlies) >= 2:
            return monthlies[1], sorted_dates
        if len(monthlies) == 1:
            # Only one monthly known — degrade to rank-2 to give the caller
            # *something* useable rather than a hard failure.
            return (
                sorted_dates[1] if len(sorted_dates) >= 2 else monthlies[0]
            ), sorted_dates
        return sorted_dates[0], sorted_dates

    return None, sorted_dates


def resolve_atm(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    atm_offset: str,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve an ATM-relative leg to a tradable symbol.

    Wraps the existing service. Engine (Phase 4+) calls into this so all
    strategy-module symbol resolution flows through one boundary.

    Expiry normalization: callers pass the DB-format ``DD-MMM-YY`` (from
    ``list_expiries`` / ``get_expiry_dates``). The downstream
    ``get_option_symbol`` and its ``_fetch_available_strikes`` lookup
    both expect the symbol-embedded compact format ``DDMMMYY`` (no
    hyphens) - the legacy strategy-builder always passed compact, so
    that's the contract. Strip the hyphens here, mirroring what
    ``resolve_direct_strike`` already does below.
    """
    expiry_compact = expiry_date.replace("-", "").upper()
    return get_option_symbol(
        underlying=underlying,
        exchange=underlying_exchange,
        expiry_date=expiry_compact,
        offset=atm_offset,
        option_type=option_type,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )


def resolve_future_based(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    atm_offset: str,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve ATM-relative options using the nearest FUT LTP as reference."""
    base = underlying.strip().upper()
    fut_exchange = option_exchange_for(underlying_exchange)
    fut = _find_near_month_futures(base, fut_exchange)
    if not fut:
        return False, {
            "status": "error",
            "message": f"No FUT contract found for {base} on {fut_exchange}",
        }, 404

    ok, quote_data, status_code = get_quotes_with_auth(
        symbol=fut["symbol"],
        exchange=fut["exchange"],
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": (
                f"Failed to fetch FUT LTP for {fut['symbol']}: "
                f"{quote_data.get('message', 'unknown error')}"
            ),
        }, status_code

    fut_ltp = quote_data.get("data", {}).get("ltp")
    if fut_ltp is None:
        return False, {"status": "error", "message": f"LTP not available for {fut['symbol']}"}, 500

    expiry_compact = expiry_date.replace("-", "").upper()
    return get_option_symbol(
        underlying=underlying,
        exchange=underlying_exchange,
        expiry_date=expiry_compact,
        offset=atm_offset,
        option_type=option_type,
        auth_token=auth_token,
        broker=broker,
        config=config,
        underlying_ltp=float(fut_ltp),
    )


def resolve_atm_percent(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    atm_percent: float,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve an option strike at ATM +/- percentage from the ATM strike."""
    base_symbol, embedded_expiry = _parse_underlying(underlying)
    final_expiry = (expiry_date or embedded_expiry or "").replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", final_expiry):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    option_type_u = option_type.upper()
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400

    quote_exchange = _quote_exchange_for(base_symbol, underlying_exchange)
    options_exchange = option_exchange_for(quote_exchange)
    if quote_exchange in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE"):
        quote_symbol, quote_exchange_for_ltp = base_symbol, quote_exchange
    elif embedded_expiry:
        quote_symbol, quote_exchange_for_ltp = underlying.upper(), quote_exchange
    else:
        fut = _find_near_month_futures(base_symbol, quote_exchange)
        if not fut:
            return False, {
                "status": "error",
                "message": f"No FUT contract found for {base_symbol} on {quote_exchange}",
            }, 404
        quote_symbol, quote_exchange_for_ltp = fut["symbol"], fut["exchange"]

    ok, quote_data, status_code = get_quotes_with_auth(
        symbol=quote_symbol,
        exchange=quote_exchange_for_ltp,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": f"Failed to fetch LTP for {quote_symbol}: {quote_data.get('message', 'unknown error')}",
        }, status_code

    ltp = quote_data.get("data", {}).get("ltp")
    if ltp is None:
        return False, {"status": "error", "message": f"LTP not available for {quote_symbol}"}, 500

    strikes = _fetch_available_strikes(base_symbol, final_expiry, option_type_u, options_exchange)
    if not strikes:
        return False, {
            "status": "error",
            "message": f"No strikes found for {base_symbol} {final_expiry} on {options_exchange}.",
        }, 404

    atm = _find_atm(float(ltp), strikes)
    if atm is None:
        return False, {"status": "error", "message": "Could not determine ATM strike"}, 404
    target_strike = _apply_atm_percent(atm, atm_percent, strikes)
    if target_strike is None:
        return False, {"status": "error", "message": "ATM percent strike out of range"}, 400

    option_symbol = f"{base_symbol}{final_expiry}{_format_strike(target_strike)}{option_type_u}"
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
        "atm_strike": atm,
        "atm_percent": atm_percent,
    }, 200


def resolve_straddle_width(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    width: float,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve an option strike at ATM +/- ATM straddle premium * width."""
    base_symbol, embedded_expiry = _parse_underlying(underlying)
    final_expiry = (expiry_date or embedded_expiry or "").replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", final_expiry):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    option_type_u = option_type.upper()
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400

    quote_exchange = _quote_exchange_for(base_symbol, underlying_exchange)
    options_exchange = option_exchange_for(quote_exchange)
    if quote_exchange in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE"):
        quote_symbol, quote_exchange_for_ltp = base_symbol, quote_exchange
    elif embedded_expiry:
        quote_symbol, quote_exchange_for_ltp = underlying.upper(), quote_exchange
    else:
        fut = _find_near_month_futures(base_symbol, quote_exchange)
        if not fut:
            return False, {
                "status": "error",
                "message": f"No FUT contract found for {base_symbol} on {quote_exchange}",
            }, 404
        quote_symbol, quote_exchange_for_ltp = fut["symbol"], fut["exchange"]

    ok, quote_data, status_code = get_quotes_with_auth(
        symbol=quote_symbol,
        exchange=quote_exchange_for_ltp,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": f"Failed to fetch LTP for {quote_symbol}: {quote_data.get('message', 'unknown error')}",
        }, status_code

    ltp = quote_data.get("data", {}).get("ltp")
    if ltp is None:
        return False, {"status": "error", "message": f"LTP not available for {quote_symbol}"}, 500

    strikes = _fetch_available_strikes(base_symbol, final_expiry, option_type_u, options_exchange)
    if not strikes:
        return False, {
            "status": "error",
            "message": f"No strikes found for {base_symbol} {final_expiry} on {options_exchange}.",
        }, 404

    atm = _find_atm(float(ltp), strikes)
    if atm is None:
        return False, {"status": "error", "message": "Could not determine ATM strike"}, 404

    atm_symbols: list[dict[str, str]] = []
    details_by_symbol: dict[str, dict[str, Any]] = {}
    for atm_option_type in ("CE", "PE"):
        symbol = f"{base_symbol}{final_expiry}{_format_strike(atm)}{atm_option_type}"
        details = _lookup_option_in_db(symbol, options_exchange)
        if not details:
            return False, {
                "status": "error",
                "message": f"ATM {atm_option_type} option {symbol} not found on {options_exchange}.",
            }, 404
        details_by_symbol[details["symbol"]] = details
        atm_symbols.append({"symbol": details["symbol"], "exchange": details["exchange"]})

    ok, straddle_quotes, status_code = get_multi_quotes_with_auth(
        symbols_list=atm_symbols,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, straddle_quotes, status_code

    premiums: dict[str, float] = {}
    for row in straddle_quotes.get("results", []):
        symbol = _quote_symbol(row)
        ltp_value = _quote_ltp(row)
        details = details_by_symbol.get(symbol)
        if not symbol or ltp_value is None or not details:
            continue
        suffix = "CE" if symbol.upper().endswith("CE") else "PE"
        premiums[suffix] = ltp_value

    if "CE" not in premiums or "PE" not in premiums:
        return False, {
            "status": "error",
            "message": "ATM CE and PE premiums are required for straddle width selection.",
        }, 404

    straddle_premium = premiums["CE"] + premiums["PE"]
    target_strike = _apply_straddle_width(atm, straddle_premium, width, strikes)
    if target_strike is None:
        return False, {"status": "error", "message": "Straddle width strike out of range"}, 400

    option_symbol = f"{base_symbol}{final_expiry}{_format_strike(target_strike)}{option_type_u}"
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
        "atm_strike": atm,
        "straddle_width": width,
        "straddle_premium": straddle_premium,
        "atm_ce_premium": premiums["CE"],
        "atm_pe_premium": premiums["PE"],
    }, 200


def resolve_underlying_percent(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    percent: float,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Pick the option whose premium is closest to percent of underlying LTP."""
    base_symbol, embedded_expiry = _parse_underlying(underlying)
    final_expiry = (expiry_date or embedded_expiry or "").replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", final_expiry):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    option_type_u = option_type.upper()
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400

    quote_exchange = _quote_exchange_for(base_symbol, underlying_exchange)
    if quote_exchange in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE"):
        quote_symbol, quote_exchange_for_ltp = base_symbol, quote_exchange
    elif embedded_expiry:
        quote_symbol, quote_exchange_for_ltp = underlying.upper(), quote_exchange
    else:
        fut = _find_near_month_futures(base_symbol, quote_exchange)
        if not fut:
            return False, {
                "status": "error",
                "message": f"No FUT contract found for {base_symbol} on {quote_exchange}",
            }, 404
        quote_symbol, quote_exchange_for_ltp = fut["symbol"], fut["exchange"]

    ok, quote_data, status_code = get_quotes_with_auth(
        symbol=quote_symbol,
        exchange=quote_exchange_for_ltp,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": f"Failed to fetch LTP for {quote_symbol}: {quote_data.get('message', 'unknown error')}",
        }, status_code

    ltp = quote_data.get("data", {}).get("ltp")
    if ltp is None:
        return False, {"status": "error", "message": f"LTP not available for {quote_symbol}"}, 500

    target_premium = _underlying_percent_premium(float(ltp), percent)
    ok, data, status_code = resolve_premium_based(
        underlying=underlying,
        underlying_exchange=underlying_exchange,
        expiry_date=final_expiry,
        option_type=option_type_u,
        premium_value=target_premium,
        mode="premium_near",
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, data, status_code
    data["underlying_ltp"] = float(ltp)
    data["underlying_percent"] = percent
    data["target_premium"] = target_premium
    return True, data, 200


def resolve_straddle_premium(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    percent: float,
    match_mode: str,
    option_type: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Pick an option by premium target calculated from ATM straddle premium."""
    base_symbol, embedded_expiry = _parse_underlying(underlying)
    final_expiry = (expiry_date or embedded_expiry or "").replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", final_expiry):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    option_type_u = option_type.upper()
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400
    if match_mode not in ("premium_near", "premium_greater", "premium_lesser"):
        return False, {"status": "error", "message": "Invalid straddle premium match mode"}, 400

    quote_exchange = _quote_exchange_for(base_symbol, underlying_exchange)
    options_exchange = option_exchange_for(quote_exchange)
    if quote_exchange in ("NSE_INDEX", "BSE_INDEX", "NSE", "BSE"):
        quote_symbol, quote_exchange_for_ltp = base_symbol, quote_exchange
    elif embedded_expiry:
        quote_symbol, quote_exchange_for_ltp = underlying.upper(), quote_exchange
    else:
        fut = _find_near_month_futures(base_symbol, quote_exchange)
        if not fut:
            return False, {
                "status": "error",
                "message": f"No FUT contract found for {base_symbol} on {quote_exchange}",
            }, 404
        quote_symbol, quote_exchange_for_ltp = fut["symbol"], fut["exchange"]

    ok, quote_data, status_code = get_quotes_with_auth(
        symbol=quote_symbol,
        exchange=quote_exchange_for_ltp,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": f"Failed to fetch LTP for {quote_symbol}: {quote_data.get('message', 'unknown error')}",
        }, status_code

    ltp = quote_data.get("data", {}).get("ltp")
    if ltp is None:
        return False, {"status": "error", "message": f"LTP not available for {quote_symbol}"}, 500

    strikes = _fetch_available_strikes(base_symbol, final_expiry, option_type_u, options_exchange)
    if not strikes:
        return False, {
            "status": "error",
            "message": f"No strikes found for {base_symbol} {final_expiry} on {options_exchange}.",
        }, 404

    atm = _find_atm(float(ltp), strikes)
    if atm is None:
        return False, {"status": "error", "message": "Could not determine ATM strike"}, 404

    atm_symbols: list[dict[str, str]] = []
    details_by_symbol: dict[str, dict[str, Any]] = {}
    for atm_option_type in ("CE", "PE"):
        symbol = f"{base_symbol}{final_expiry}{_format_strike(atm)}{atm_option_type}"
        details = _lookup_option_in_db(symbol, options_exchange)
        if not details:
            return False, {
                "status": "error",
                "message": f"ATM {atm_option_type} option {symbol} not found on {options_exchange}.",
            }, 404
        details_by_symbol[details["symbol"]] = details
        atm_symbols.append({"symbol": details["symbol"], "exchange": details["exchange"]})

    ok, straddle_quotes, status_code = get_multi_quotes_with_auth(
        symbols_list=atm_symbols,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, straddle_quotes, status_code

    premiums: dict[str, float] = {}
    for row in straddle_quotes.get("results", []):
        symbol = _quote_symbol(row)
        ltp_value = _quote_ltp(row)
        details = details_by_symbol.get(symbol)
        if not symbol or ltp_value is None or not details:
            continue
        suffix = "CE" if symbol.upper().endswith("CE") else "PE"
        premiums[suffix] = ltp_value

    if "CE" not in premiums or "PE" not in premiums:
        return False, {
            "status": "error",
            "message": "ATM CE and PE premiums are required for straddle premium selection.",
        }, 404

    straddle_premium = premiums["CE"] + premiums["PE"]
    target_premium = _straddle_premium_target(straddle_premium, percent)
    ok, data, status_code = resolve_premium_based(
        underlying=underlying,
        underlying_exchange=underlying_exchange,
        expiry_date=final_expiry,
        option_type=option_type_u,
        premium_value=target_premium,
        mode=match_mode,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, data, status_code

    data["underlying_ltp"] = float(ltp)
    data["atm_strike"] = atm
    data["straddle_premium"] = straddle_premium
    data["straddle_premium_percent"] = percent
    data["straddle_premium_match"] = match_mode
    data["target_premium"] = target_premium
    data["atm_ce_premium"] = premiums["CE"]
    data["atm_pe_premium"] = premiums["PE"]
    return True, data, 200


def resolve_direct_strike(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    strike: float,
    option_type: str,
) -> tuple[bool, dict[str, Any], int]:
    """Resolve a direct-strike leg to a tradable symbol.

    The user picked the strike from the wizard's strike picker, which queried
    `symtoken` for available strikes — so the contract should exist. We
    rebuild the OpenAlgo symbol per `docs/design/symbol-format.md` and
    confirm. Returns the same shape as `resolve_atm` so callers can branch
    on `strike_mode` and otherwise treat them uniformly.
    """
    base = underlying.strip().upper()
    opt_exchange = option_exchange_for(underlying_exchange)

    # Accept either format on input; normalize to symbol-embedded form.
    expiry_compact = expiry_date.replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_compact):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    option_type_u = option_type.upper()
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400

    symbol = f"{base}{expiry_compact}{_format_strike(strike)}{option_type_u}"
    details = _lookup_option_in_db(symbol, opt_exchange)
    if not details:
        return False, {
            "status": "error",
            "message": f"Option {symbol} not found on {opt_exchange}",
        }, 404

    return True, {
        "status": "success",
        "symbol": details["symbol"],
        "exchange": details["exchange"],
        "lotsize": details["lotsize"],
        "tick_size": details["tick_size"],
        "strike": details["strike"],
        "expiry": details["expiry"],
        "underlying_ltp": None,  # not fetched in direct-strike mode
    }, 200


def _quote_ltp(row: dict[str, Any]) -> Optional[float]:
    data = row.get("data") if isinstance(row.get("data"), dict) else row
    value = data.get("ltp") if isinstance(data, dict) else None
    if value is None:
        value = row.get("ltp")
    try:
        ltp = float(value)
    except (TypeError, ValueError):
        return None
    return ltp if ltp > 0 else None


def _quote_symbol(row: dict[str, Any]) -> Optional[str]:
    symbol = row.get("symbol")
    if symbol:
        return str(symbol)
    data = row.get("data")
    if isinstance(data, dict) and data.get("symbol"):
        return str(data["symbol"])
    return None


def resolve_premium_based(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    option_type: str,
    premium_value: float,
    mode: str,
    auth_token: str,
    broker: str,
    config: Optional[dict] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Pick the option whose live premium matches near/greater/lesser mode."""
    base = underlying.strip().upper()
    opt_exchange = option_exchange_for(underlying_exchange)
    expiry_compact = expiry_date.replace("-", "").upper()
    option_type_u = option_type.upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_compact):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400
    if option_type_u not in ("CE", "PE"):
        return False, {"status": "error", "message": "option_type must be CE or PE"}, 400

    strikes = _fetch_available_strikes(base, expiry_compact, option_type_u, opt_exchange)
    if not strikes:
        return False, {
            "status": "error",
            "message": f"No strikes found for {base} {expiry_compact} on {opt_exchange}.",
        }, 404

    details_by_symbol: dict[str, dict[str, Any]] = {}
    symbols_list = []
    for strike in strikes:
        symbol = f"{base}{expiry_compact}{_format_strike(strike)}{option_type_u}"
        details = _lookup_option_in_db(symbol, opt_exchange)
        if not details:
            continue
        details_by_symbol[details["symbol"]] = details
        symbols_list.append({"symbol": details["symbol"], "exchange": details["exchange"]})

    if not symbols_list:
        return False, {
            "status": "error",
            "message": f"No tradable option symbols found for {base} {expiry_compact} {option_type_u}.",
        }, 404

    ok, quote_data, status_code = get_multi_quotes_with_auth(
        symbols_list=symbols_list,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, quote_data, status_code

    candidates = []
    for row in quote_data.get("results", []):
        symbol = _quote_symbol(row)
        details = details_by_symbol.get(symbol)
        ltp = _quote_ltp(row)
        if not details or ltp is None:
            continue
        candidates.append((details, ltp))

    if mode == "premium_greater":
        candidates = [(d, l) for d, l in candidates if l >= premium_value]
        key = lambda item: (item[1] - premium_value, item[0]["strike"])
    elif mode == "premium_lesser":
        candidates = [(d, l) for d, l in candidates if l <= premium_value]
        key = lambda item: (premium_value - item[1], -float(item[0]["strike"]))
    else:
        key = lambda item: (abs(item[1] - premium_value), item[0]["strike"])

    if not candidates:
        return False, {
            "status": "error",
            "message": f"No option premium matched {mode.replace('_', ' ')} {premium_value}.",
        }, 404

    details, ltp = min(candidates, key=key)
    return True, {
        "status": "success",
        "symbol": details["symbol"],
        "exchange": details["exchange"],
        "lotsize": details["lotsize"],
        "tick_size": details["tick_size"],
        "strike": details["strike"],
        "expiry": details["expiry"],
        "underlying_ltp": None,
        "option_ltp": ltp,
    }, 200


def list_strikes(
    *,
    underlying: str,
    underlying_exchange: str,
    expiry_date: str,
    option_type: str,
) -> tuple[bool, dict[str, Any], int]:
    """Return the sorted list of tradable strikes for an underlying/expiry/type.

    Backed by the existing in-memory cache in option_symbol_service. The
    wizard's strike picker calls this endpoint when the user opens the
    direct-strike dropdown.
    """
    from backend.services.option_symbol_service import _fetch_available_strikes

    base = underlying.strip().upper()
    opt_exchange = option_exchange_for(underlying_exchange)
    expiry_compact = expiry_date.replace("-", "").upper()
    if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_compact):
        return False, {"status": "error", "message": f"Invalid expiry: {expiry_date}"}, 400

    strikes = _fetch_available_strikes(
        base, expiry_compact, option_type.upper(), opt_exchange,
    )
    return True, {
        "status": "success",
        "strikes": [float(s) for s in strikes],
        "underlying": base,
        "exchange": opt_exchange,
        "expiry": expiry_date,
        "option_type": option_type.upper(),
    }, 200


def list_underlyings_for_tab(universe_tab: str) -> tuple[bool, dict[str, Any], int]:
    """Return the dropdown source for a universe tab.

    NSE/BSE indices and the F&O stocks list come from `symtoken`; MCX comes
    from `symtoken`. Hardcoded indices are used only for the two index tabs
    where the universe is known and stable.
    """
    from backend.services.option_symbol_service import _run_query

    if universe_tab == "weekly_monthly":
        # Indices that have weekly + monthly expiries.
        return True, {
            "status": "success",
            "underlyings": [
                {"symbol": "NIFTY", "name": "Nifty 50", "exchange": "NSE_INDEX"},
                {"symbol": "SENSEX", "name": "BSE Sensex", "exchange": "BSE_INDEX"},
            ],
        }, 200

    if universe_tab == "monthly_only":
        return True, {
            "status": "success",
            "underlyings": [
                {"symbol": "BANKNIFTY", "name": "Nifty Bank", "exchange": "NSE_INDEX"},
                {"symbol": "FINNIFTY", "name": "Nifty Fin Service", "exchange": "NSE_INDEX"},
                {"symbol": "MIDCPNIFTY", "name": "Nifty Midcap Select", "exchange": "NSE_INDEX"},
                {"symbol": "BANKEX", "name": "BSE Bankex", "exchange": "BSE_INDEX"},
            ],
        }, 200

    if universe_tab == "stocks_fno":
        rows = _run_query(
            "SELECT REGEXP_REPLACE(symbol, '(\\d{2}[A-Z]{3}\\d{2})FUT$', '') AS base, "
            "MIN(name) AS display_name "
            "FROM symtoken "
            "WHERE exchange = 'NFO' "
            "AND instrumenttype = 'FUT' "
            "AND expiry IS NOT NULL AND expiry != '' "
            "AND TO_DATE(expiry, 'DD-Mon-YY') >= CURRENT_DATE "
            "GROUP BY base "
            "ORDER BY base",
            {},
        )
        return True, {
            "status": "success",
            "underlyings": [
                {"symbol": row[0], "name": row[1] or row[0], "exchange": "NSE"}
                for row in rows
                if row[0]
            ],
        }, 200

    if universe_tab == "mcx":
        rows = _run_query(
            "SELECT REGEXP_REPLACE(symbol, '(\\d{2}[A-Z]{3}\\d{2})FUT$', '') AS base, "
            "MIN(name) AS display_name "
            "FROM symtoken "
            "WHERE exchange = 'MCX' "
            "AND instrumenttype = 'FUT' "
            "AND expiry IS NOT NULL AND expiry != '' "
            "AND TO_DATE(expiry, 'DD-Mon-YY') >= CURRENT_DATE "
            "GROUP BY base "
            "ORDER BY base",
            {},
        )
        return True, {
            "status": "success",
            "underlyings": [
                {"symbol": row[0], "name": row[1] or row[0], "exchange": "MCX"}
                for row in rows
                if row[0]
            ],
        }, 200

    return False, {
        "status": "error",
        "message": f"Unknown universe_tab: {universe_tab}",
    }, 400


def list_expiries(
    underlying: str, underlying_exchange: str, instrument: str = "options"
) -> tuple[bool, dict[str, Any], int]:
    """Return sorted expiry dates for an underlying. Thin wrapper over the
    existing market_data_service helper, with the option-exchange mapping
    applied so callers can pass the underlying's exchange directly."""
    base = underlying.strip().upper()
    opt_exchange = option_exchange_for(underlying_exchange)
    return get_expiry_dates(base, opt_exchange, instrument)
