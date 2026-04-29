"""
Strategy Chart service — historical combined-premium time series for an
arbitrary leg list.

Generalises :mod:`backend.services.straddle_chart_service`: that one walks
the underlying and recomputes ATM per candle, so the strikes vary along the
time axis. The Strategy Builder by contrast has *fixed* legs — the user
chose them — so the algorithm reduces to "fetch each leg's history,
intersect timestamps, sum signed contributions per candle".

Output series:

* ``combined_series[*].value`` — position premium at that historical close,
  signed the same way the snapshot endpoint signs ``position_premium``:
  ``sum_legs( sign * lots * lot_size * close )`` where ``sign = +1`` for
  BUY, ``-1`` for SELL. Positive = net debit, negative = net credit.
* ``combined_series[*].pnl`` — present only when every leg carries an
  ``entry_price``. Equals ``value(t) - value(entry)`` so the Portfolio
  view can render an MTM curve without re-doing the math.
* Per-leg series and (optional) underlying overlay are returned alongside
  so the chart can offer "show legs" / "show underlying" toggles without
  another round-trip.

Critical correctness call: timestamps where *any* leg lacks a close are
dropped (intersection, not union). Without this you'd see phantom dips
when the broker returns a stale candle for one leg but not another. This
matches openalgo's design.

Fallback: when the broker can't deliver intraday history for the
underlying (Zerodha indices on 1m, for example), ``underlying_available``
becomes ``false`` and ``underlying_series`` is empty — the chart still
draws the combined premium and per-leg curves.
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


def _action_sign(action: str) -> int:
    return 1 if action.upper() == "BUY" else -1


def _candle_close_map(candles: list[dict]) -> dict[int, float]:
    """Build {unix_seconds -> close} for a candle list. Skips zeros/Nones."""
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


def get_strategy_chart_data(
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
    """Fetch history for every leg and the underlying, then intersect.

    Args:
        legs: list of dicts — ``symbol``, ``action`` (BUY/SELL), ``lots``,
            ``lot_size``. Optional ``exchange`` per-leg overrides
            ``options_exchange``. Optional ``entry_price`` enables the PnL
            series.
        underlying: base symbol (used as the y2 overlay).
        exchange: spot/forward exchange for the underlying. Auto-resolved
            if None.
        interval: broker-specific interval string ("1m", "5m", "1d", …).
        days: trim the result to the last N distinct trading dates so
            reload-on-change feels fast at the cost of horizon.
        include_underlying: set to false to skip the underlying fetch
            entirely (e.g. when the chart's "underlying overlay" toggle is
            off — saves one broker call).
    """
    if not legs:
        return False, {"status": "error", "message": "At least one leg is required"}, 400
    if days < 1 or days > 60:
        return False, {"status": "error", "message": "days must be between 1 and 60"}, 400

    # Validate leg shape up front so we don't fan out a partial fetch.
    for idx, leg in enumerate(legs):
        for key in ("symbol", "action", "lots", "lot_size"):
            if leg.get(key) in (None, ""):
                return False, {
                    "status": "error",
                    "message": f"Leg {idx + 1}: '{key}' is required",
                }, 400

    base_symbol = underlying.upper()
    underlying_exchange = (
        exchange or get_underlying_exchange(base_symbol, "NFO")
    ).upper()
    default_opt_exch = (options_exchange or "NFO").upper()

    # Calendar window: pad with a weekend cushion so a "1 day" request still
    # reaches the most recent trading day on a Monday morning.
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
                underlying_series.append({"time": int(ts), "close": round(close_f, 2)})
            underlying_available = bool(underlying_series)
            if not underlying_available:
                logger.info(
                    "Underlying %s on %s returned no candles for %s/%s",
                    base_symbol, underlying_exchange, interval, days,
                )
        else:
            # Broker doesn't support intraday history for this underlying
            # (e.g. Zerodha indices on 1m). Continue with legs-only — chart
            # tab will hide the y2 axis based on `underlying_available`.
            logger.info(
                "Underlying history unavailable for %s on %s: %s",
                base_symbol, underlying_exchange, resp_u.get("message"),
            )

    # 2) Per-leg history.
    leg_outputs: list[dict] = []
    leg_close_maps: list[dict[int, float]] = []
    leg_multipliers: list[float] = []
    leg_entries: list[float | None] = []

    for idx, leg in enumerate(legs):
        symbol = leg["symbol"]
        action = leg["action"].upper()
        lots = int(leg["lots"])
        lot_size = int(leg["lot_size"])
        leg_exchange = (leg.get("exchange") or default_opt_exch).upper()
        sign = _action_sign(action)
        multiplier = sign * lots * lot_size

        leg_out: dict[str, Any] = {
            "index": idx,
            "symbol": symbol,
            "exchange": leg_exchange,
            "action": action,
            "lots": lots,
            "lot_size": lot_size,
            "series": [],
        }

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
        if not ok:
            leg_out["error"] = resp.get("message", "history unavailable")
            leg_close_maps.append({})
            leg_multipliers.append(multiplier)
            leg_entries.append(_safe_float(leg.get("entry_price")))
            leg_outputs.append(leg_out)
            continue

        cmap = _candle_close_map(resp.get("data", []) or [])
        leg_out["series"] = [
            {"time": ts, "close": round(close, 2)} for ts, close in sorted(cmap.items())
        ]

        entry = _safe_float(leg.get("entry_price"))
        if entry is not None:
            leg_out["entry_price"] = round(entry, 2)

        leg_close_maps.append(cmap)
        leg_multipliers.append(multiplier)
        leg_entries.append(entry)
        leg_outputs.append(leg_out)

    # 3) Intersect leg timestamps. A timestamp where any leg lacks a close
    #    is dropped — otherwise the combined curve dips spuriously when one
    #    broker candle is late.
    populated_maps = [m for m in leg_close_maps if m]
    if not populated_maps:
        return False, {
            "status": "error",
            "message": "No leg history available — every leg returned empty",
        }, 404

    common_ts: set[int] = set(populated_maps[0].keys())
    for m in populated_maps[1:]:
        common_ts &= set(m.keys())

    if not common_ts:
        return False, {
            "status": "error",
            "message": "Legs have no overlapping timestamps — pick a longer interval or different days",
        }, 404

    # 4) Build combined series (and PnL series if every leg has entry_price).
    all_have_entries = all(e is not None for e in leg_entries)
    entry_premium = (
        sum(mult * (entry or 0.0) for mult, entry in zip(leg_multipliers, leg_entries))
        if all_have_entries
        else None
    )

    combined_series: list[dict] = []
    for ts in sorted(common_ts):
        value = 0.0
        valid = True
        for cmap, mult in zip(leg_close_maps, leg_multipliers):
            close = cmap.get(ts)
            if close is None:
                # Should not happen given the intersection above, but the
                # leg-with-no-history case slips through with an empty map
                # that's not in `populated_maps`. Defensive skip.
                valid = False
                break
            value += mult * close
        if not valid:
            continue
        entry = {"time": ts, "value": round(value, 2)}
        if entry_premium is not None:
            entry["pnl"] = round(value - entry_premium, 2)
        combined_series.append(entry)

    if not combined_series:
        return False, {
            "status": "error",
            "message": "No combined series — leg histories did not intersect",
        }, 404

    # 5) Trim to the last N trading dates so a "5 days" knob really shows 5.
    combined_series = _cap_last_n_trading_dates(combined_series, days)
    if combined_series:
        cutoff_ts = combined_series[0]["time"]
        underlying_series = [p for p in underlying_series if p["time"] >= cutoff_ts]
        for leg_out in leg_outputs:
            leg_out["series"] = [
                p for p in leg_out["series"] if p["time"] >= cutoff_ts
            ]

    # 6) Current LTP for the header card.
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
            "underlying_available": underlying_available and bool(underlying_series),
            "underlying_series": underlying_series,
            "leg_series": leg_outputs,
            "combined_series": combined_series,
            "entry_premium": (
                round(entry_premium, 2) if entry_premium is not None else None
            ),
        },
    }, 200


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
