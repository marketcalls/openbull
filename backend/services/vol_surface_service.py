"""
Volatility Surface service - rectangular IV grid across (strikes × expiries).

Mirrors openalgo's services/vol_surface_service.py exactly: same inputs, same
response shape (status / data: {underlying, underlying_ltp, atm_strike,
strikes, expiries: [{date, dte}], surface: [[iv...]]}), and same OTM
convention — CE IV for strikes >= ATM, PE IV for strikes < ATM.
"""

import logging
from datetime import datetime
from typing import Any

from backend.services.option_greeks_service import calculate_greeks, parse_option_symbol
from backend.services.option_symbol_service import (
    _fetch_available_strikes,
    _find_atm,
    _format_strike,
    _option_exchange_for,
    _quote_exchange_for,
)
from backend.services.quotes_service import get_multi_quotes_with_auth, get_quotes_with_auth

logger = logging.getLogger(__name__)


# Index symbols whose spot quote lives on NSE_INDEX / BSE_INDEX.
_NSE_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "NIFTYIT", "NIFTYPHARMA", "NIFTYBANK",
}
_BSE_INDEX_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}


def _resolve_quote_exchange(base_symbol: str, exchange: str) -> str:
    """Pick the exchange to fetch the underlying spot from."""
    if base_symbol in _NSE_INDEX_SYMBOLS:
        return "NSE_INDEX"
    if base_symbol in _BSE_INDEX_SYMBOLS:
        return "BSE_INDEX"
    eu = exchange.upper()
    if eu in ("NFO", "BFO"):
        return "NSE" if eu == "NFO" else "BSE"
    return eu


def _build_option_symbol(base: str, expiry_ddmmmyy: str, strike: float, opt_type: str) -> str:
    return f"{base}{expiry_ddmmmyy}{_format_strike(strike)}{opt_type.upper()}"


def get_vol_surface_data(
    underlying: str,
    exchange: str,
    expiry_dates: list[str],
    strike_count: int,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Compute a vol surface across multiple expiries at the current instant."""
    try:
        if not expiry_dates:
            return False, {"status": "error", "message": "At least one expiry is required"}, 400

        base_symbol = underlying.upper()
        # Both quote and option exchanges resolved through openbull's existing helpers.
        quote_exchange = _resolve_quote_exchange(base_symbol, exchange)
        # _quote_exchange_for / _option_exchange_for keep the same NFO↔NSE mapping
        # the option chain service uses, so option symbols on the wire match.
        option_chain_quote_exchange = _quote_exchange_for(base_symbol, exchange)
        options_exchange = _option_exchange_for(option_chain_quote_exchange)

        # 1. Underlying LTP — one fetch.
        ok, qresp, status_code = get_quotes_with_auth(
            symbol=base_symbol, exchange=quote_exchange,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok:
            return False, {
                "status": "error",
                "message": f"Failed to fetch LTP for {base_symbol}: {qresp.get('message', '')}",
            }, status_code
        underlying_ltp = qresp.get("data", {}).get("ltp")
        if not underlying_ltp:
            return False, {"status": "error", "message": f"No LTP for {base_symbol}"}, 500
        underlying_ltp = float(underlying_ltp)

        # 2. Per-expiry strike grid + ATM.
        expiry_strike_data: list[dict] = []
        for expiry in expiry_dates:
            strikes = _fetch_available_strikes(base_symbol, expiry.upper(), "CE", options_exchange)
            if not strikes:
                logger.warning("No strikes for %s %s on %s, skipping", base_symbol, expiry, options_exchange)
                continue
            atm = _find_atm(underlying_ltp, strikes)
            if atm is None:
                continue
            atm_idx = strikes.index(atm)
            start = max(0, atm_idx - strike_count)
            end = min(len(strikes), atm_idx + strike_count + 1)
            selected = strikes[start:end]
            expiry_strike_data.append({
                "expiry": expiry.upper(),
                "strikes": selected,
                "atm": atm,
            })

        if not expiry_strike_data:
            return False, {"status": "error", "message": "No valid expiry data found"}, 404

        # 3. Common-strike intersection across expiries → rectangular grid.
        strike_sets = [set(e["strikes"]) for e in expiry_strike_data]
        common_strikes = sorted(strike_sets[0].intersection(*strike_sets[1:]))
        if len(common_strikes) < 3:
            common_strikes = sorted(expiry_strike_data[0]["strikes"])

        atm_strike = expiry_strike_data[0]["atm"]

        # 4. Per-expiry: build OTM-leg symbol list, batch-quote, compute IV.
        surface: list[list[float | None]] = []
        expiry_info: list[dict] = []

        for ed in expiry_strike_data:
            expiry = ed["expiry"]

            symbols_to_fetch: list[dict] = []
            for strike in common_strikes:
                opt_type = "CE" if strike >= atm_strike else "PE"
                sym = _build_option_symbol(base_symbol, expiry, strike, opt_type)
                symbols_to_fetch.append({"symbol": sym, "exchange": options_exchange})

            ok_q, qresp, _ = get_multi_quotes_with_auth(
                symbols_list=symbols_to_fetch,
                auth_token=auth_token, broker=broker, config=config,
            )
            quotes_map: dict[str, float] = {}
            if ok_q:
                for r in qresp.get("results", []):
                    sym = r.get("symbol")
                    if not sym:
                        continue
                    data = r.get("data") or {}
                    # Off-hours: LTP is 0 but close/prev_close still carry the
                    # last traded price. Fall back so the surface has values
                    # outside market hours.
                    px = (
                        data.get("ltp")
                        or data.get("close")
                        or data.get("prev_close")
                        or 0
                    )
                    quotes_map[sym] = float(px or 0)

            iv_row: list[float | None] = []
            for strike in common_strikes:
                opt_type = "CE" if strike >= atm_strike else "PE"
                sym = _build_option_symbol(base_symbol, expiry, strike, opt_type)
                option_ltp = quotes_map.get(sym, 0)
                if not option_ltp or option_ltp <= 0:
                    iv_row.append(None)
                    continue
                try:
                    ok_g, gresp, _ = calculate_greeks(
                        option_symbol=sym,
                        exchange=options_exchange,
                        spot_price=underlying_ltp,
                        option_price=option_ltp,
                    )
                    if ok_g and gresp.get("status") == "success":
                        iv_val = gresp.get("implied_volatility")
                        iv_row.append(round(iv_val, 2) if iv_val and iv_val > 0 else None)
                    else:
                        iv_row.append(None)
                except Exception:
                    iv_row.append(None)

            surface.append(iv_row)

            # DTE — parse one of the symbols to get the expiry datetime.
            try:
                test_sym = _build_option_symbol(base_symbol, expiry, common_strikes[0], "CE")
                _, expiry_dt, _, _ = parse_option_symbol(test_sym, options_exchange)
                dte = max(0, (expiry_dt - datetime.now()).total_seconds() / 86400)
                expiry_info.append({"date": expiry, "dte": round(dte, 1)})
            except Exception:
                expiry_info.append({"date": expiry, "dte": 0})

        return True, {
            "status": "success",
            "data": {
                "underlying": base_symbol,
                "underlying_ltp": underlying_ltp,
                "atm_strike": atm_strike,
                "strikes": common_strikes,
                "expiries": expiry_info,
                "surface": surface,
            },
        }, 200

    except Exception as e:
        logger.exception("Error computing vol surface: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
