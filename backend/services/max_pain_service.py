"""
Max Pain service - the strike at which total option-buyer loss is minimized
on expiry, computed from current OI snapshot.

For each candidate strike k:
    pain[k] = sum_i max(k - K_i, 0) * ce_oi_i  +  sum_i max(K_i - k, 0) * pe_oi_i

The candidate with the smallest total pain is the "max pain" strike — the
expiry settle most adverse to net option buyers (and most favourable to net
sellers).

Reuses get_option_chain (23 strikes around ATM = 47 strikes, 94 symbols)
so the request fits inside the Fyers multiquote OI bucket (<=100 symbols).
A wider window pushes the symbol count past the threshold, the broker
skips OI fetch entirely, and every pain row collapses to 0 — the page
then renders empty. Same constraint as the OI Tracker; matches openalgo.
"""

import logging
from typing import Any

from backend.services.option_chain_service import get_option_chain

logger = logging.getLogger(__name__)

_DEFAULT_STRIKE_HALF_WINDOW = 23


def _build_pain_curve(chain: list[dict]) -> list[dict]:
    """Return [{strike, ce_oi, pe_oi, total_pain}] sorted by strike."""
    strikes: list[float] = []
    ce_oi: dict[float, float] = {}
    pe_oi: dict[float, float] = {}

    for row in chain:
        k = row.get("strike")
        if k is None:
            continue
        strikes.append(k)
        ce = (row.get("ce") or {}).get("oi") or 0
        pe = (row.get("pe") or {}).get("oi") or 0
        ce_oi[k] = float(ce)
        pe_oi[k] = float(pe)

    out: list[dict] = []
    for candidate in strikes:
        pain = 0.0
        for held_strike in strikes:
            # CE writers lose (candidate - K) per unit when settle > K
            if candidate > held_strike:
                pain += (candidate - held_strike) * ce_oi[held_strike]
            # PE writers lose (K - candidate) per unit when settle < K
            if candidate < held_strike:
                pain += (held_strike - candidate) * pe_oi[held_strike]
        out.append({
            "strike": candidate,
            "ce_oi": ce_oi[candidate],
            "pe_oi": pe_oi[candidate],
            "total_pain": round(pain, 2),
        })

    out.sort(key=lambda r: r["strike"])
    return out


def get_max_pain_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Build the Max Pain payload for a (underlying, exchange, expiry).

    Strategy: 23 strikes either side of ATM (94 symbols total) — the
    largest window that still gets per-symbol OI populated under Fyers'
    100-symbol multiquote OI cap.
    """
    try:
        ok, chain_resp, status = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=_DEFAULT_STRIKE_HALF_WINDOW,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        if not ok:
            return False, chain_resp, status

        full_chain = chain_resp.get("chain", [])
        if not full_chain:
            return False, {"status": "error", "message": "Option chain is empty"}, 404

        atm_strike = chain_resp.get("atm_strike")
        spot_price = chain_resp.get("underlying_ltp")
        base_symbol = chain_resp.get("underlying", underlying).upper()
        quote_symbol = chain_resp.get("quote_symbol")
        quote_exchange = chain_resp.get("quote_exchange")

        pain_rows = _build_pain_curve(full_chain)
        if not pain_rows:
            return False, {"status": "error", "message": "Could not compute pain curve"}, 500

        max_pain_row = min(pain_rows, key=lambda r: r["total_pain"])
        max_pain_strike = max_pain_row["strike"]

        total_ce_oi = sum(r["ce_oi"] for r in pain_rows)
        total_pe_oi = sum(r["pe_oi"] for r in pain_rows)
        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

        return True, {
            "status": "success",
            "underlying": base_symbol,
            "spot_price": spot_price,
            "quote_symbol": quote_symbol,
            "quote_exchange": quote_exchange,
            "atm_strike": atm_strike,
            "max_pain_strike": max_pain_strike,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "pcr_oi": pcr_oi,
            "expiry_date": expiry_date,
            "chain": pain_rows,
        }, 200

    except Exception as e:
        logger.exception("Error in get_max_pain_data: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
