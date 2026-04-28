"""
IV Chart service - intraday Implied Volatility + Greeks time series for the
ATM CE and PE of a given underlying/expiry.

Pulls OHLCV candles for the underlying spot AND for the ATM CE/PE legs at the
chosen interval, aligns them on common timestamps, and at each timestamp
solves IV (bisection, Black-76) and computes Δ/Γ/Θ/V using the same pure-math
helpers as the snapshot Greeks endpoint.

NSE/BSE only: assumes a tradable spot for the underlying.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from backend.services.history_service import get_history_with_auth
from backend.services.option_greeks_service import (
    BSE_INDEX_UNDERLYINGS,
    DEFAULT_INTEREST_RATE_PCT,
    NSE_INDEX_UNDERLYINGS,
    _expiry_datetime,
    _greeks,
    _implied_vol,
)
from backend.services.option_symbol_service import (
    _fetch_available_strikes,
    _find_atm,
    _format_strike,
)
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)


_SUPPORTED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "D"}


def _quote_exchange_for_underlying(base_symbol: str, options_exchange: str) -> str:
    if base_symbol in NSE_INDEX_UNDERLYINGS:
        return "NSE_INDEX"
    if base_symbol in BSE_INDEX_UNDERLYINGS:
        return "BSE_INDEX"
    return "NSE" if options_exchange.upper() == "NFO" else "BSE"


def _option_exchange_for(base_exchange: str) -> str:
    eu = base_exchange.upper()
    if eu in ("NFO", "NSE", "NSE_INDEX"):
        return "NFO"
    if eu in ("BFO", "BSE", "BSE_INDEX"):
        return "BFO"
    return "NFO"


def _candle_close_map(candles: list[dict]) -> dict[int, float]:
    """timestamp(int seconds) -> close. Skips zero/None closes."""
    out: dict[int, float] = {}
    for c in candles:
        ts = c.get("timestamp")
        close = c.get("close")
        if ts is None or close is None:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if close_f <= 0:
            continue
        out[int(ts)] = close_f
    return out


def get_iv_chart_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    interval: str,
    days: int,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    interest_rate: float | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Build the IV/Greeks time-series payload for ATM CE & PE.

    `exchange` is the *options* exchange (NFO or BFO). The underlying spot is
    auto-resolved (NSE_INDEX / BSE_INDEX for indices, NSE / BSE for equities).
    """
    try:
        if interval not in _SUPPORTED_INTERVALS:
            return False, {
                "status": "error",
                "message": f"Unsupported interval '{interval}'. Use one of {sorted(_SUPPORTED_INTERVALS)}",
            }, 400

        if days < 1 or days > 30:
            return False, {"status": "error", "message": "days must be between 1 and 30"}, 400

        options_exchange = _option_exchange_for(exchange)
        if options_exchange not in ("NFO", "BFO"):
            return False, {
                "status": "error",
                "message": "exchange must be NFO or BFO (Greeks history is NSE/BSE-only)",
            }, 400

        base_symbol = underlying.upper()
        quote_exchange = _quote_exchange_for_underlying(base_symbol, options_exchange)
        expiry_ddmmmyy = expiry_date.upper()

        # 1. Underlying spot — picks ATM strike for "today's" chart.
        ok, qdata, status_code = get_quotes_with_auth(
            symbol=base_symbol, exchange=quote_exchange,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok:
            return False, {
                "status": "error",
                "message": f"Failed to fetch underlying quote: {qdata.get('message', 'unknown')}",
            }, status_code
        underlying_ltp = qdata.get("data", {}).get("ltp")
        if underlying_ltp is None or underlying_ltp <= 0:
            return False, {"status": "error", "message": "Underlying LTP unavailable"}, 500

        # 2. ATM strike from the actual strike grid.
        strikes = _fetch_available_strikes(base_symbol, expiry_ddmmmyy, "CE", options_exchange)
        if not strikes:
            return False, {
                "status": "error",
                "message": f"No strikes found for {base_symbol} {expiry_ddmmmyy} on {options_exchange}",
            }, 404
        atm_strike = _find_atm(float(underlying_ltp), strikes)
        if atm_strike is None:
            return False, {"status": "error", "message": "Could not determine ATM strike"}, 500

        ce_symbol = f"{base_symbol}{expiry_ddmmmyy}{_format_strike(atm_strike)}CE"
        pe_symbol = f"{base_symbol}{expiry_ddmmmyy}{_format_strike(atm_strike)}PE"

        # 3. Date window. The history fetcher will skip non-trading days so we
        #    just ask for `days` calendar days; CE/PE alignment falls out of the
        #    timestamp intersection.
        today = datetime.now().date()
        start_date = (today - timedelta(days=days)).isoformat()
        end_date = today.isoformat()

        # 4. Fetch all three histories (underlying, CE, PE).
        ok_u, resp_u, _ = get_history_with_auth(
            symbol=base_symbol, exchange=quote_exchange, interval=interval,
            start_date=start_date, end_date=end_date,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_u:
            return False, {
                "status": "error",
                "message": f"Failed to fetch underlying history: {resp_u.get('message', 'unknown')}",
            }, 500

        ok_ce, resp_ce, _ = get_history_with_auth(
            symbol=ce_symbol, exchange=options_exchange, interval=interval,
            start_date=start_date, end_date=end_date,
            auth_token=auth_token, broker=broker, config=config,
        )
        ok_pe, resp_pe, _ = get_history_with_auth(
            symbol=pe_symbol, exchange=options_exchange, interval=interval,
            start_date=start_date, end_date=end_date,
            auth_token=auth_token, broker=broker, config=config,
        )

        underlying_candles = resp_u.get("data", []) if isinstance(resp_u.get("data"), list) else []
        ce_candles = resp_ce.get("data", []) if (ok_ce and isinstance(resp_ce.get("data"), list)) else []
        pe_candles = resp_pe.get("data", []) if (ok_pe and isinstance(resp_pe.get("data"), list)) else []

        if not underlying_candles:
            return False, {"status": "error", "message": "No underlying candles in window"}, 404

        u_map = _candle_close_map(underlying_candles)
        ce_map = _candle_close_map(ce_candles)
        pe_map = _candle_close_map(pe_candles)

        # 5. Compute series. Black-76 expiry datetime (15:30 IST for NFO/BFO).
        expiry_dt = _expiry_datetime(expiry_ddmmmyy, options_exchange)
        rate_pct = interest_rate if interest_rate is not None else DEFAULT_INTEREST_RATE_PCT
        r = rate_pct / 100.0

        ce_series = _build_series(
            opt_map=ce_map, u_map=u_map, strike=float(atm_strike),
            expiry_dt=expiry_dt, r=r, flag="c",
        )
        pe_series = _build_series(
            opt_map=pe_map, u_map=u_map, strike=float(atm_strike),
            expiry_dt=expiry_dt, r=r, flag="p",
        )

        if not ce_series and not pe_series:
            return False, {
                "status": "error",
                "message": "No overlapping candles between underlying and option legs",
            }, 404

        # Response shape mirrors openalgo's iv_chart_service: top-level
        # {status, data}, each series carries `iv_data` (not `data`).
        series_results: list[dict] = []
        if ce_series:
            series_results.append({
                "symbol": ce_symbol,
                "option_type": "CE",
                "strike": float(atm_strike),
                "iv_data": ce_series,
            })
        if pe_series:
            series_results.append({
                "symbol": pe_symbol,
                "option_type": "PE",
                "strike": float(atm_strike),
                "iv_data": pe_series,
            })

        return True, {
            "status": "success",
            "data": {
                "underlying": base_symbol,
                "underlying_ltp": float(underlying_ltp),
                "atm_strike": float(atm_strike),
                "ce_symbol": ce_symbol,
                "pe_symbol": pe_symbol,
                "interval": interval,
                "days": days,
                "expiry_date": expiry_ddmmmyy,
                "interest_rate": round(rate_pct, 4),
                "series": series_results,
            },
        }, 200

    except Exception as e:
        logger.exception("Error in get_iv_chart_data: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def _build_series(
    opt_map: dict[int, float],
    u_map: dict[int, float],
    strike: float,
    expiry_dt: datetime,
    r: float,
    flag: str,
) -> list[dict]:
    """Compute IV + greeks at every timestamp present in both maps."""
    if not opt_map:
        return []

    common_ts = sorted(set(opt_map).intersection(u_map))
    if not common_ts:
        return []

    out: list[dict] = []
    for ts in common_ts:
        opt_close = opt_map[ts]
        u_close = u_map[ts]

        # Time to expiry from this candle's wall-clock
        candle_dt = datetime.fromtimestamp(ts)
        seconds_to_expiry = (expiry_dt - candle_dt).total_seconds()
        if seconds_to_expiry <= 0:
            continue
        T_years = seconds_to_expiry / (365.0 * 24 * 3600)

        iv_dec = _implied_vol(opt_close, u_close, strike, T_years, r, flag)
        if iv_dec is None or iv_dec <= 0:
            out.append({
                "time": ts,
                "iv": None,
                "delta": None, "gamma": None, "theta": None, "vega": None,
                "option_price": opt_close,
                "underlying_price": u_close,
            })
            continue

        gks = _greeks(u_close, strike, T_years, r, iv_dec, flag, opt_close)
        out.append({
            "time": ts,
            "iv": round(iv_dec * 100.0, 2),
            "delta": gks["delta"],
            "gamma": gks["gamma"],
            "theta": gks["theta"],
            "vega": gks["vega"],
            "option_price": opt_close,
            "underlying_price": u_close,
        })

    return out
