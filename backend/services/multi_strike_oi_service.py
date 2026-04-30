"""
Multi-strike OI service — per-leg Open Interest time series for a Strategy
Builder leg set, plus the underlying close as an overlay.

Modeled on :mod:`strategy_chart_service` — same per-leg history fan-out,
same intersection rule on timestamps, same trading-window resolution. The
only structural difference is the value we extract from each candle: we
keep the broker-reported ``oi`` field instead of the close, and we don't
sum legs (the chart overlays each leg as its own line).

A leg whose broker doesn't ship historical OI ends up with an all-zero
series — the response flags ``has_oi=false`` so the UI can warn the user
but still draws the underlying overlay.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from backend.services.history_service import get_history_with_auth
from backend.services.option_greeks_service import get_underlying_exchange
from backend.services.quotes_service import get_quotes_with_auth
from backend.services.straddle_chart_service import _cap_last_n_trading_dates

logger = logging.getLogger(__name__)


def _candle_oi_map(candles: list[dict]) -> dict[int, float]:
    """{unix_seconds -> oi} for a candle list. Skips Nones; keeps zeros (a
    legitimate OI reading at start-of-day is 0)."""
    out: dict[int, float] = {}
    for c in candles:
        ts = c.get("timestamp")
        oi = c.get("oi")
        if ts is None:
            continue
        if oi is None:
            oi = 0
        try:
            oi_f = float(oi)
        except (TypeError, ValueError):
            continue
        out[int(ts)] = oi_f
    return out


def get_multi_strike_oi_data(
    legs: list[dict],
    underlying: str,
    exchange: str | None,
    interval: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    options_exchange: str | None = None,
    days: int = 5,
    include_underlying: bool = True,
) -> tuple[bool, dict[str, Any], int]:
    """Fetch per-leg OI time series + underlying close.

    Args:
        legs: list of dicts — ``symbol``, ``action`` (BUY/SELL), ``lots``,
            ``lot_size``. Optional ``exchange``, ``strike``, ``option_type``,
            ``expiry_date`` are passed through to the response so the chart
            can label the lines.
        underlying: base symbol (used as the y2 overlay).
        exchange: spot exchange. Auto-resolved if None.
        interval: broker-specific interval string.
        days: trim the result to the last N distinct trading dates.
        include_underlying: skip the underlying fetch when False.
    """
    if not legs:
        return False, {"status": "error", "message": "At least one leg is required"}, 400
    if days < 1 or days > 60:
        return False, {"status": "error", "message": "days must be between 1 and 60"}, 400

    for idx, leg in enumerate(legs):
        if not leg.get("symbol"):
            return False, {
                "status": "error",
                "message": f"Leg {idx + 1}: 'symbol' is required",
            }, 400

    base_symbol = underlying.upper()
    underlying_exchange = (
        exchange or get_underlying_exchange(base_symbol, "NFO")
    ).upper()
    default_opt_exch = (options_exchange or "NFO").upper()

    today = datetime.now().date()
    start_date = (today - timedelta(days=max(1, days * 2 + 2))).isoformat()
    end_date = today.isoformat()

    # 1) Underlying history (optional overlay).
    underlying_series: list[dict] = []
    underlying_available = False
    if include_underlying:
        ok_u, resp_u, _ = get_history_with_auth(
            symbol=base_symbol,
            exchange=underlying_exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        if ok_u:
            for c in resp_u.get("data", []) or []:
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
                # Frontend expects ``value`` here (matches openalgo's shape
                # so the chart component can be ported 1:1).
                underlying_series.append({"time": int(ts), "value": round(close_f, 2)})
            underlying_available = bool(underlying_series)
        else:
            logger.info(
                "Multi-strike OI: underlying history unavailable for %s on %s: %s",
                base_symbol, underlying_exchange, resp_u.get("message"),
            )

    # 2) Per-leg OI history. Dedupe by (symbol, exchange) since two template
    #    legs at the same strike+expiry+option_type would both want the same
    #    candle stream — fetch once.
    oi_lookup: dict[tuple[str, str], dict[int, float]] = {}
    leg_outputs: list[dict] = []

    for idx, leg in enumerate(legs):
        symbol = leg["symbol"]
        leg_exchange = (leg.get("exchange") or default_opt_exch).upper()
        action = (leg.get("action") or "").upper()

        leg_out: dict[str, Any] = {
            "index": idx,
            "symbol": symbol,
            "exchange": leg_exchange,
            "action": action,
            "strike": leg.get("strike"),
            "option_type": leg.get("option_type"),
            "expiry": leg.get("expiry_date") or leg.get("expiry"),
            "has_oi": False,
            "series": [],
        }

        key = (symbol, leg_exchange)
        if key not in oi_lookup:
            ok, resp, _ = get_history_with_auth(
                symbol=symbol,
                exchange=leg_exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                auth_token=auth_token,
                broker=broker,
                config=config,
            )
            oi_lookup[key] = (
                _candle_oi_map(resp.get("data", []) or []) if ok else {}
            )
            if not ok:
                leg_out["error"] = resp.get("message", "history unavailable")

        oi_map = oi_lookup[key]
        if oi_map:
            sorted_pts = [
                {"time": ts, "value": round(oi, 2)}
                for ts, oi in sorted(oi_map.items())
            ]
            leg_out["series"] = sorted_pts
            leg_out["has_oi"] = any(p["value"] > 0 for p in sorted_pts)

        leg_outputs.append(leg_out)

    # 3) Trim to the last N trading dates so a "5 days" knob really shows 5.
    if leg_outputs:
        # Find the union of leg timestamps to do the cap, then re-filter
        # each leg by the cutoff. Underlying gets the same cutoff.
        all_ts: set[int] = set()
        for leg_out in leg_outputs:
            for p in leg_out["series"]:
                all_ts.add(p["time"])
        if all_ts:
            cap_input = [{"time": ts, "value": 0} for ts in sorted(all_ts)]
            capped = _cap_last_n_trading_dates(cap_input, days)
            if capped:
                cutoff = capped[0]["time"]
                underlying_series = [p for p in underlying_series if p["time"] >= cutoff]
                for leg_out in leg_outputs:
                    leg_out["series"] = [
                        p for p in leg_out["series"] if p["time"] >= cutoff
                    ]

    # 4) Underlying LTP for the header card.
    underlying_ltp = 0.0
    ok_q, qresp, _ = get_quotes_with_auth(
        symbol=base_symbol,
        exchange=underlying_exchange,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if ok_q:
        try:
            underlying_ltp = float(qresp.get("data", {}).get("ltp") or 0)
        except (TypeError, ValueError):
            underlying_ltp = 0.0

    return True, {
        "status": "success",
        "data": {
            "underlying": base_symbol,
            "underlying_ltp": round(underlying_ltp, 2),
            "exchange": underlying_exchange,
            "interval": interval,
            "days": days,
            "underlying_available": underlying_available,
            "underlying_series": underlying_series,
            "legs": leg_outputs,
        },
    }, 200
