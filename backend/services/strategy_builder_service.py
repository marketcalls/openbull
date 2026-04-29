"""
Strategy Builder snapshot service.

Single round-trip helper that powers the Strategy Builder live view: fetch
the underlying spot once, fetch every leg's LTP in one multi-quotes call,
solve IV and Black-76 Greeks per leg using the existing pure-math
implementation in :mod:`backend.services.option_greeks_service`, then return
per-leg detail plus position-aggregated Greeks and net premium.

Why this lives apart from option_chain_service / option_greeks_service:
the chain/greeks services are strike-centred ("give me strikes around ATM"
or "give me the Greeks for *this one* contract"). The Strategy Builder
needs the *user's leg set* — heterogeneous strikes, mixed CE/PE, mixed
BUY/SELL, occasionally mixed expiries (calendar/diagonal) — priced in one
shot and folded into a position summary. That's a different shape, and
fanning it out into N quote calls + N greeks calls would burn the broker's
rate budget for no gain.

Aggregation conventions (match openalgo's Strategy Builder):

* Leg sign  = +1 for BUY, -1 for SELL.
* Per-leg position multiplier = ``sign * lots * lot_size``.
* Position Greek = sum over legs of ``multiplier * leg_greek``.
* Premium paid (>0 = net debit, <0 = net credit) = sum of ``multiplier * ltp``.
* If ``entry_price`` is supplied per leg the response also returns
  ``unrealized_pnl`` so the Portfolio view can show open-position P&L
  without re-doing the math on the client.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from backend.services.option_greeks_service import (
    DEFAULT_INTEREST_RATES,
    calculate_greeks,
    get_underlying_exchange,
    parse_option_symbol,
)
from backend.services.quotes_service import (
    get_multi_quotes_with_auth,
    get_quotes_with_auth,
)

logger = logging.getLogger(__name__)


# IST is +05:30 from UTC. We stamp ``as_of`` in IST so the UI can show a
# human-readable timestamp without needing to convert in the browser.
_IST = timezone(timedelta(hours=5, minutes=30))


def _action_sign(action: str) -> int:
    return 1 if action.upper() == "BUY" else -1


def _resolve_spot(
    underlying: str,
    exchange: str | None,
    auth_token: str,
    broker: str,
    config: dict | None,
) -> tuple[float, str] | tuple[None, str]:
    """Fetch spot LTP for the underlying. Returns (price, exchange) or (None, error_msg)."""
    spot_exchange = (exchange or get_underlying_exchange(underlying.upper(), "NFO")).upper()
    ok, qresp, _ = get_quotes_with_auth(
        symbol=underlying.upper(),
        exchange=spot_exchange,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return None, qresp.get("message", "Failed to fetch underlying price")
    ltp = qresp.get("data", {}).get("ltp")
    if ltp is None:
        return None, "Underlying LTP not available"
    try:
        return float(ltp), spot_exchange
    except (TypeError, ValueError):
        return None, f"Underlying LTP invalid: {ltp!r}"


def _ltp_from_multi(results: list[dict], symbol: str, exchange: str) -> float | None:
    """Pull a leg's LTP out of the multi-quotes result list."""
    for r in results:
        if r.get("symbol") == symbol and r.get("exchange") == exchange:
            data = r.get("data") if isinstance(r.get("data"), dict) else r
            ltp = data.get("ltp")
            try:
                return float(ltp) if ltp is not None else None
            except (TypeError, ValueError):
                return None
    return None


def get_strategy_snapshot(
    legs: list[dict],
    underlying: str,
    exchange: str | None,
    auth_token: str,
    broker: str,
    config: dict | None = None,
    options_exchange: str | None = None,
    interest_rate: float | None = None,
    expiry_time: str | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Price every leg, solve Greeks, aggregate to position level.

    Args:
        legs: list of dicts with at least ``symbol``, ``action``, ``lots``,
            ``lot_size``. Optional: ``entry_price`` (enables ``unrealized_pnl``).
        underlying: base symbol (e.g. ``NIFTY``) — used as the F in Black-76.
        exchange: underlying exchange (e.g. ``NSE_INDEX``); auto-resolved if None.
        options_exchange: where the option contracts trade (NFO/BFO/CDS/MCX).
            If a leg's symbol parses to a different exchange, the leg's
            exchange wins for that leg.

    Returns:
        ``(success, payload, http_status)``. On success ``payload`` matches
        the docstring at the top of the module: ``status`` / ``underlying``
        / ``spot_price`` / ``as_of`` / ``legs`` / ``totals``. Per-leg errors
        are non-fatal — the leg appears in the response with ``"error":
        "..."`` and zero Greeks; the rest of the snapshot still renders.
    """
    if not legs:
        return False, {"status": "error", "message": "At least one leg is required"}, 400

    # Validate basic leg shape up front so we never fan out a partial fetch.
    for idx, leg in enumerate(legs):
        if not leg.get("symbol"):
            return False, {
                "status": "error",
                "message": f"Leg {idx + 1}: symbol is required",
            }, 400
        if not leg.get("action"):
            return False, {
                "status": "error",
                "message": f"Leg {idx + 1}: action is required",
            }, 400
        if not leg.get("lots") or not leg.get("lot_size"):
            return False, {
                "status": "error",
                "message": f"Leg {idx + 1}: lots and lot_size are required",
            }, 400

    spot_price, info = _resolve_spot(
        underlying, exchange, auth_token, broker, config
    )
    if spot_price is None:
        return False, {"status": "error", "message": info}, 502

    spot_exchange = info  # info is the exchange we actually used

    # Build the multi-quotes request: each leg's symbol + its options
    # exchange. ``options_exchange`` is the default; a leg can override via
    # leg["exchange"] (rare but supports cross-segment baskets).
    default_opt_exch = (options_exchange or "NFO").upper()
    quote_request: list[dict] = []
    for leg in legs:
        ex = (leg.get("exchange") or default_opt_exch).upper()
        quote_request.append({"symbol": leg["symbol"], "exchange": ex})

    # Some brokers reject duplicates; dedup while preserving order.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for q in quote_request:
        key = (q["symbol"], q["exchange"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)

    ok, mqresp, status_code = get_multi_quotes_with_auth(
        symbols_list=deduped,
        auth_token=auth_token,
        broker=broker,
        config=config,
    )
    if not ok:
        return False, {
            "status": "error",
            "message": f"Failed to fetch leg quotes: {mqresp.get('message', 'unknown')}",
        }, status_code

    multi_results = mqresp.get("results", []) or []

    # Per-leg pricing + Greeks
    leg_outputs: list[dict] = []
    totals = {
        "premium_paid": 0.0,
        "delta": 0.0,
        "gamma": 0.0,
        "theta": 0.0,
        "vega": 0.0,
        "rho": 0.0,
        "unrealized_pnl": 0.0,
    }
    has_entry_prices = False

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
        }

        # Try to extract metadata from the symbol so we always have
        # strike/option_type/expiry on the response, even if greeks fail.
        try:
            base_symbol, expiry_dt, strike, opt_type = parse_option_symbol(
                symbol, leg_exchange, expiry_time
            )
            leg_out.update({
                "underlying": base_symbol,
                "strike": round(strike, 2),
                "option_type": opt_type,
                "expiry_date": expiry_dt.strftime("%d-%b-%Y"),
            })
        except ValueError as e:
            leg_out["error"] = str(e)
            for k in ("delta", "gamma", "theta", "vega", "rho"):
                leg_out.setdefault("greeks", {})[k] = 0.0
            leg_outputs.append(leg_out)
            continue

        ltp = _ltp_from_multi(multi_results, symbol, leg_exchange)
        if ltp is None or ltp <= 0:
            leg_out["ltp"] = None
            leg_out["error"] = "LTP unavailable"
            leg_out["greeks"] = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
            leg_out["implied_volatility"] = 0.0
            leg_outputs.append(leg_out)
            continue

        # Solve IV + Greeks for this leg using the live spot. calculate_greeks
        # is pure-math: it never re-hits the broker — we already have every
        # input we need.
        rate = (
            interest_rate
            if interest_rate is not None
            else DEFAULT_INTEREST_RATES.get(leg_exchange, 0)
        )
        ok, gresp, _ = calculate_greeks(
            option_symbol=symbol,
            exchange=leg_exchange,
            spot_price=spot_price,
            option_price=ltp,
            interest_rate=rate,
            expiry_time=expiry_time,
        )

        leg_out["ltp"] = round(ltp, 2)
        if ok:
            gks = gresp.get("greeks", {})
            leg_out["implied_volatility"] = gresp.get("implied_volatility", 0.0)
            leg_out["days_to_expiry"] = gresp.get("days_to_expiry", 0.0)
            leg_out["greeks"] = gks
            if "note" in gresp:
                leg_out["note"] = gresp["note"]
            for k in ("delta", "gamma", "theta", "vega", "rho"):
                totals[k] += multiplier * float(gks.get(k, 0.0))
        else:
            leg_out["error"] = gresp.get("message", "Greeks calculation failed")
            leg_out["implied_volatility"] = 0.0
            leg_out["greeks"] = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

        # Premium signed by action: BUY adds to debit, SELL subtracts (credit).
        leg_premium = multiplier * ltp
        leg_out["position_premium"] = round(leg_premium, 2)
        totals["premium_paid"] += leg_premium

        # Optional unrealized P&L if the leg carries an entry price.
        entry_price = leg.get("entry_price")
        if entry_price is not None:
            has_entry_prices = True
            try:
                entry = float(entry_price)
                leg_out["entry_price"] = round(entry, 2)
                leg_pnl = (ltp - entry) * multiplier
                leg_out["unrealized_pnl"] = round(leg_pnl, 2)
                totals["unrealized_pnl"] += leg_pnl
            except (TypeError, ValueError):
                pass

        leg_outputs.append(leg_out)

    # Round totals for cleaner JSON.
    totals_rounded = {
        "premium_paid": round(totals["premium_paid"], 2),
        "delta": round(totals["delta"], 4),
        "gamma": round(totals["gamma"], 6),
        "theta": round(totals["theta"], 4),
        "vega": round(totals["vega"], 4),
        "rho": round(totals["rho"], 6),
    }
    if has_entry_prices:
        totals_rounded["unrealized_pnl"] = round(totals["unrealized_pnl"], 2)

    return True, {
        "status": "success",
        "underlying": underlying.upper(),
        "exchange": spot_exchange,
        "spot_price": round(spot_price, 2),
        "as_of": datetime.now(_IST).isoformat(timespec="seconds"),
        "legs": leg_outputs,
        "totals": totals_rounded,
    }, 200
