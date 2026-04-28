"""
IV Smile service - Implied Volatility per strike for a single expiry.

Mirrors openalgo's services/iv_smile_service.py exactly: same inputs, same
response shape, same skew approximation (5% OTM proxy for 25-delta put-call
skew).

Uses Black-76 IV from the snapshot greeks service for each leg in the chain.
"""

import logging
from typing import Any

from backend.services.option_chain_service import get_option_chain
from backend.services.option_greeks_service import calculate_greeks

logger = logging.getLogger(__name__)


def get_iv_smile_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Build the IV smile payload (CE IV, PE IV per strike) for an expiry."""
    try:
        ok, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=25,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        if not ok:
            return False, chain_response, status_code

        full_chain = chain_response.get("chain", [])
        atm_strike = chain_response.get("atm_strike")
        spot_price = chain_response.get("underlying_ltp")

        if not spot_price or spot_price <= 0:
            return False, {"status": "error", "message": "Could not determine spot price"}, 500

        # Resolve options exchange (for symbol parsing in calculate_greeks).
        options_exchange = exchange.upper()
        if options_exchange in ("NSE_INDEX", "NSE"):
            options_exchange = "NFO"
        elif options_exchange in ("BSE_INDEX", "BSE"):
            options_exchange = "BFO"

        iv_chain: list[dict] = []
        atm_ce_iv: float | None = None
        atm_pe_iv: float | None = None

        for item in full_chain:
            strike = item["strike"]
            ce = item.get("ce")
            pe = item.get("pe")

            ce_iv: float | None = None
            pe_iv: float | None = None

            if ce and ce.get("symbol"):
                ce_ltp = ce.get("ltp", 0) or 0
                if ce_ltp > 0:
                    try:
                        ok_g, gresp, _ = calculate_greeks(
                            option_symbol=ce["symbol"],
                            exchange=options_exchange,
                            spot_price=spot_price,
                            option_price=ce_ltp,
                        )
                        if ok_g and gresp.get("status") == "success":
                            v = gresp.get("implied_volatility", 0)
                            if v and v > 0:
                                ce_iv = round(v, 2)
                    except Exception:
                        pass

            if pe and pe.get("symbol"):
                pe_ltp = pe.get("ltp", 0) or 0
                if pe_ltp > 0:
                    try:
                        ok_g, gresp, _ = calculate_greeks(
                            option_symbol=pe["symbol"],
                            exchange=options_exchange,
                            spot_price=spot_price,
                            option_price=pe_ltp,
                        )
                        if ok_g and gresp.get("status") == "success":
                            v = gresp.get("implied_volatility", 0)
                            if v and v > 0:
                                pe_iv = round(v, 2)
                    except Exception:
                        pass

            if strike == atm_strike:
                atm_ce_iv = ce_iv
                atm_pe_iv = pe_iv

            iv_chain.append({"strike": strike, "ce_iv": ce_iv, "pe_iv": pe_iv})

        # ATM IV — average of CE and PE at ATM.
        atm_iv: float | None = None
        if atm_ce_iv is not None and atm_pe_iv is not None:
            atm_iv = round((atm_ce_iv + atm_pe_iv) / 2, 2)
        elif atm_ce_iv is not None:
            atm_iv = atm_ce_iv
        elif atm_pe_iv is not None:
            atm_iv = atm_pe_iv

        # Skew — 25-delta proxy: PE IV at ATM-5% minus CE IV at ATM+5%.
        skew: float | None = None
        if atm_strike and iv_chain:
            otm_distance = atm_strike * 0.05

            put_iv_for_skew: float | None = None
            for it in sorted(iv_chain, key=lambda x: abs(x["strike"] - (atm_strike - otm_distance))):
                if it["strike"] < atm_strike and it["pe_iv"] is not None:
                    put_iv_for_skew = it["pe_iv"]
                    break

            call_iv_for_skew: float | None = None
            for it in sorted(iv_chain, key=lambda x: abs(x["strike"] - (atm_strike + otm_distance))):
                if it["strike"] > atm_strike and it["ce_iv"] is not None:
                    call_iv_for_skew = it["ce_iv"]
                    break

            if put_iv_for_skew is not None and call_iv_for_skew is not None:
                skew = round(put_iv_for_skew - call_iv_for_skew, 2)

        return True, {
            "status": "success",
            "underlying": chain_response.get("underlying", underlying),
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "atm_iv": atm_iv,
            "skew": skew,
            "expiry_date": expiry_date,
            "chain": iv_chain,
        }, 200

    except Exception as e:
        logger.exception("Error in get_iv_smile_data: %s", e)
        return False, {"status": "error", "message": "Error fetching IV Smile data"}, 500
