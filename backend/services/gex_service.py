"""
GEX (Gamma Exposure) service - per-strike gamma exposure from live OI.

Mirrors openalgo's services/gex_service.py exactly: same inputs, same response
shape (status / underlying / spot_price / futures_price / lot_size /
atm_strike / expiry_date / pcr_oi / total_*_oi / total_*_gex / total_net_gex /
chain: [{strike, ce_oi, pe_oi, ce_gamma, pe_gamma, ce_gex, pe_gex, net_gex}]).

GEX = gamma * open_interest * lot_size
Net GEX = CE GEX - PE GEX
"""

import logging
from typing import Any

from backend.services.oi_tracker_service import _get_futures_price
from backend.services.option_chain_service import get_option_chain
from backend.services.option_greeks_service import calculate_greeks

logger = logging.getLogger(__name__)


def get_gex_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Compute Gamma Exposure (GEX) per strike from the option chain.

    Strike window sized to 23 either side of ATM (47 strikes = 94
    symbols). A wider window pushes the request past the Fyers
    multiquote OI cap (100 symbols, see broker/fyers/api/data.py),
    OI silently falls back to 0 for every row, and gamma * OI per
    strike collapses to 0 — the GEX page renders empty. Same
    constraint as OI Tracker / Max Pain.
    """
    try:
        ok, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=23,
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

        # Resolve options exchange for symbol parsing in calculate_greeks.
        options_exchange = exchange.upper()
        if options_exchange in ("NSE_INDEX", "NSE"):
            options_exchange = "NFO"
        elif options_exchange in ("BSE_INDEX", "BSE"):
            options_exchange = "BFO"

        lot_size: int | None = None
        gex_chain: list[dict] = []

        for item in full_chain:
            strike = item["strike"]
            ce = item.get("ce")
            pe = item.get("pe")

            ce_oi = 0
            pe_oi = 0
            ce_gex = 0.0
            pe_gex = 0.0
            ce_gamma = 0.0
            pe_gamma = 0.0
            current_lotsize = 1

            if ce and ce.get("symbol"):
                ce_oi = int(ce.get("oi", 0) or 0)
                ce_ltp = float(ce.get("ltp", 0) or 0)
                current_lotsize = int(ce.get("lotsize", 1) or 1)
                if lot_size is None:
                    lot_size = current_lotsize

                if ce_ltp > 0 and ce_oi > 0:
                    try:
                        ok_g, gresp, _ = calculate_greeks(
                            option_symbol=ce["symbol"],
                            exchange=options_exchange,
                            spot_price=spot_price,
                            option_price=ce_ltp,
                        )
                        if ok_g and gresp.get("status") == "success":
                            ce_gamma = float(gresp.get("greeks", {}).get("gamma", 0) or 0)
                            ce_gex = ce_gamma * ce_oi * current_lotsize
                    except Exception as e:
                        logger.warning("Failed to compute greeks for CE %s: %s", ce.get("symbol"), e)

            if pe and pe.get("symbol"):
                pe_oi = int(pe.get("oi", 0) or 0)
                pe_ltp = float(pe.get("ltp", 0) or 0)
                current_lotsize = int(pe.get("lotsize", 1) or 1)
                if lot_size is None:
                    lot_size = current_lotsize

                if pe_ltp > 0 and pe_oi > 0:
                    try:
                        ok_g, gresp, _ = calculate_greeks(
                            option_symbol=pe["symbol"],
                            exchange=options_exchange,
                            spot_price=spot_price,
                            option_price=pe_ltp,
                        )
                        if ok_g and gresp.get("status") == "success":
                            pe_gamma = float(gresp.get("greeks", {}).get("gamma", 0) or 0)
                            pe_gex = pe_gamma * pe_oi * current_lotsize
                    except Exception as e:
                        logger.warning("Failed to compute greeks for PE %s: %s", pe.get("symbol"), e)

            net_gex = ce_gex - pe_gex
            gex_chain.append({
                "strike": strike,
                "ce_oi": ce_oi,
                "pe_oi": pe_oi,
                "ce_gamma": round(ce_gamma, 6),
                "pe_gamma": round(pe_gamma, 6),
                "ce_gex": round(ce_gex, 2),
                "pe_gex": round(pe_gex, 2),
                "net_gex": round(net_gex, 2),
            })

        # Matching-expiry futures price (for display alongside spot).
        futures_price: float | None = None
        try:
            options_exch_for_fut = options_exchange
            futures_price = _get_futures_price(
                base_symbol=str(chain_response.get("underlying", underlying)).upper(),
                options_exchange=options_exch_for_fut,
                expiry_ddmmmyy=str(expiry_date).upper(),
                auth_token=auth_token, broker=broker, config=config,
            )
        except Exception as e:
            logger.debug("Could not resolve futures price for GEX: %s", e)
            futures_price = None

        # Totals.
        total_ce_oi = sum(item["ce_oi"] for item in gex_chain)
        total_pe_oi = sum(item["pe_oi"] for item in gex_chain)
        total_ce_gex = sum(item["ce_gex"] for item in gex_chain)
        total_pe_gex = sum(item["pe_gex"] for item in gex_chain)
        total_net_gex = sum(item["net_gex"] for item in gex_chain)
        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        return True, {
            "status": "success",
            "underlying": chain_response.get("underlying", underlying),
            "spot_price": spot_price,
            "futures_price": futures_price,
            "lot_size": lot_size or 1,
            "atm_strike": atm_strike,
            "expiry_date": expiry_date,
            "pcr_oi": pcr_oi,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "total_ce_gex": round(total_ce_gex, 2),
            "total_pe_gex": round(total_pe_gex, 2),
            "total_net_gex": round(total_net_gex, 2),
            "chain": gex_chain,
        }, 200

    except Exception as e:
        logger.exception("Error in get_gex_data: %s", e)
        return False, {"status": "error", "message": "Error fetching GEX data"}, 500
