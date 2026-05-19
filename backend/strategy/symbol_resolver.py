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
    _format_strike,
    _lookup_option_in_db,
    _option_exchange_for,
    get_option_symbol,
)

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
