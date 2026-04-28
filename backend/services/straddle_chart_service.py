"""
Straddle Chart service - dynamic ATM straddle time series with synthetic
futures overlay.

Mirrors openalgo's services/straddle_chart_service.py exactly: same inputs,
same response shape (status / data: {underlying, underlying_ltp, expiry_date,
interval, days_to_expiry, series: [{time, spot, atm_strike, ce_price,
pe_price, straddle, synthetic_future}]}).

For each underlying candle the ATM strike is recomputed from the close price,
then we look up the matching CE/PE candle to compute Straddle = CE+PE and
Synthetic Future = K + CE - PE.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from backend.services.history_service import get_history_with_auth
from backend.services.option_symbol_service import (
    _fetch_available_strikes,
    _find_atm,
    _format_strike,
    _option_exchange_for,
    _quote_exchange_for,
)
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)


_NSE_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "NIFTYIT", "NIFTYPHARMA", "NIFTYBANK",
}
_BSE_INDEX_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}


def _get_quote_exchange(base_symbol: str, exchange: str) -> str:
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


def _calculate_days_to_expiry(expiry_date_str: str) -> int:
    """Calendar days from today to expiry parsed from DDMMMYY (15:30 IST close)."""
    try:
        expiry_dt = datetime.strptime(expiry_date_str.upper(), "%d%b%y")
        expiry_dt = expiry_dt.replace(hour=15, minute=30)
        delta = expiry_dt - datetime.now()
        return max(0, delta.days)
    except Exception:
        return 0


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


def get_straddle_chart_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    interval: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    days: int = 5,
) -> tuple[bool, dict[str, Any], int]:
    """Compute the dynamic-ATM straddle + synthetic-future time series."""
    try:
        if days < 1 or days > 30:
            return False, {"status": "error", "message": "days must be between 1 and 30"}, 400

        base_symbol = underlying.upper()
        quote_exchange = _get_quote_exchange(base_symbol, exchange)
        # Resolve options exchange via the same path the chain service uses.
        option_chain_quote_exchange = _quote_exchange_for(base_symbol, exchange)
        options_exchange = _option_exchange_for(option_chain_quote_exchange)

        expiry_ddmmmyy = expiry_date.upper()

        # 1. Available strikes (any CE).
        available_strikes = _fetch_available_strikes(
            base_symbol, expiry_ddmmmyy, "CE", options_exchange,
        )
        if not available_strikes:
            return False, {
                "status": "error",
                "message": f"No strikes found for {base_symbol} {expiry_ddmmmyy} on {options_exchange}",
            }, 404

        # 2. Calendar window — pad with weekend cushion so a 1-day request still
        #    reaches the most recent trading day on Mondays.
        today = datetime.now().date()
        start_date = (today - timedelta(days=max(1, days * 2 + 2))).isoformat()
        end_date = today.isoformat()

        # 3. Underlying history.
        ok_u, resp_u, _ = get_history_with_auth(
            symbol=base_symbol, exchange=quote_exchange, interval=interval,
            start_date=start_date, end_date=end_date,
            auth_token=auth_token, broker=broker, config=config,
        )
        if not ok_u:
            return False, {
                "status": "error",
                "message": f"Failed to fetch underlying history: {resp_u.get('message', 'Unknown error')}",
            }, 400

        underlying_candles = resp_u.get("data", []) or []
        if not underlying_candles:
            return False, {"status": "error", "message": "No underlying history data available"}, 404

        # 4. Per-candle ATM. Capture distinct strikes that show up.
        ts_to_close: dict[int, float] = {}
        ts_to_atm: dict[int, float] = {}
        unique_strikes: set[float] = set()
        for c in underlying_candles:
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
            atm = _find_atm(close_f, available_strikes)
            if atm is None:
                continue
            ts_to_close[int(ts)] = close_f
            ts_to_atm[int(ts)] = atm
            unique_strikes.add(atm)

        if not unique_strikes:
            return False, {"status": "error", "message": "Could not determine any ATM strikes"}, 400

        logger.info(
            "Straddle: %d unique ATM strikes for %s — fetching CE+PE history per strike",
            len(unique_strikes), base_symbol,
        )

        # 5. Fetch CE + PE history per unique strike.
        strike_data: dict[float, dict[str, dict[int, float]]] = {}
        for strike in sorted(unique_strikes):
            ce_symbol = _build_option_symbol(base_symbol, expiry_ddmmmyy, strike, "CE")
            pe_symbol = _build_option_symbol(base_symbol, expiry_ddmmmyy, strike, "PE")

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

            ce_map = _candle_close_map(resp_ce.get("data", [])) if ok_ce else {}
            pe_map = _candle_close_map(resp_pe.get("data", [])) if ok_pe else {}
            strike_data[strike] = {"ce": ce_map, "pe": pe_map}

        # 6. Walk underlying candles in order and build the series.
        series: list[dict] = []
        for ts in sorted(ts_to_close):
            spot = ts_to_close[ts]
            atm = ts_to_atm[ts]
            sd = strike_data.get(atm)
            if not sd:
                continue
            ce_price = sd["ce"].get(ts)
            pe_price = sd["pe"].get(ts)
            if ce_price is None or pe_price is None:
                continue
            straddle = round(ce_price + pe_price, 2)
            synthetic_future = round(atm + ce_price - pe_price, 2)
            series.append({
                "time": ts,
                "spot": round(spot, 2),
                "atm_strike": atm,
                "ce_price": round(ce_price, 2),
                "pe_price": round(pe_price, 2),
                "straddle": straddle,
                "synthetic_future": synthetic_future,
            })

        if not series:
            return False, {
                "status": "error",
                "message": "No straddle data available (option history may be missing)",
            }, 404

        # 7. Trim to last `days` distinct trading dates that actually have data.
        series = _cap_last_n_trading_dates(series, days)

        # 8. Current LTP for display.
        ok_q, qresp, _ = get_quotes_with_auth(
            symbol=base_symbol, exchange=quote_exchange,
            auth_token=auth_token, broker=broker, config=config,
        )
        underlying_ltp = qresp.get("data", {}).get("ltp", 0) if ok_q else 0

        return True, {
            "status": "success",
            "data": {
                "underlying": base_symbol,
                "underlying_ltp": underlying_ltp,
                "expiry_date": expiry_ddmmmyy,
                "interval": interval,
                "days_to_expiry": _calculate_days_to_expiry(expiry_date),
                "series": series,
            },
        }, 200

    except Exception as e:
        logger.exception("Error calculating straddle chart data: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def _cap_last_n_trading_dates(series: list[dict], n: int) -> list[dict]:
    """Keep only points from the last `n` distinct trading dates."""
    if not series or n <= 0:
        return series
    dates_seen: list[str] = []
    seen_set: set[str] = set()
    # Walk most-recent-first to find the cutoff date.
    for entry in reversed(series):
        ts = entry.get("time")
        if ts is None:
            continue
        d = datetime.fromtimestamp(ts).date().isoformat()
        if d not in seen_set:
            seen_set.add(d)
            dates_seen.append(d)
            if len(dates_seen) >= n:
                break
    if not dates_seen:
        return series
    cutoff_date = dates_seen[-1]
    cutoff_ts = int(datetime.fromisoformat(cutoff_date).timestamp())
    return [e for e in series if e.get("time", 0) >= cutoff_ts]
