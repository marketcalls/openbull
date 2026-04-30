"""
OI Tracker service — Open Interest snapshot per strike for an expiry, plus
PCR and the matching-expiry futures price. Reuses the existing option chain
service for OI data.

Output shape mirrors OpenAlgo's `/oitracker` so the same UI patterns apply.
"""

import logging
import re
from datetime import datetime
from typing import Any

from backend.services.market_data_service import _run_query
from backend.services.option_chain_service import get_option_chain
from backend.services.quotes_service import get_quotes_with_auth

logger = logging.getLogger(__name__)


_FUT_PREFIX_RE = re.compile(r"^([A-Z0-9]+?)(\d{2}[A-Z]{3}\d{2})FUT$")


def _find_futures_symbol(
    base_symbol: str, options_exchange: str, expiry_ddmmmyy: str
) -> dict | None:
    """Find the futures contract for the same underlying + expiry.

    Looks for ``BASE{DDMMMYY}FUT`` on the options exchange first; falls back
    to picking the nearest-by-expiry FUT for the same base if no exact match.
    """
    expiry_db = f"{expiry_ddmmmyy[:2]}-{expiry_ddmmmyy[2:5]}-{expiry_ddmmmyy[5:]}".upper()
    expected_symbol = f"{base_symbol}{expiry_ddmmmyy}FUT".upper()

    rows = _run_query(
        "SELECT symbol, exchange, expiry FROM symtoken "
        "WHERE symbol = :sym AND exchange = :exch AND instrumenttype = 'FUT'",
        {"sym": expected_symbol, "exch": options_exchange.upper()},
    )
    if rows:
        sym, exch, _exp = rows[0]
        return {"symbol": sym, "exchange": exch}

    # Fall back to nearest-month FUT for the same base.
    rows = _run_query(
        "SELECT symbol, exchange, expiry FROM symtoken "
        "WHERE symbol LIKE :pattern AND exchange = :exch AND instrumenttype = 'FUT' "
        "AND expiry IS NOT NULL AND expiry != ''",
        {"pattern": f"{base_symbol}%FUT", "exch": options_exchange.upper()},
    )
    if not rows:
        return None

    def parse_exp(e: str) -> datetime:
        try:
            return datetime.strptime(e, "%d-%b-%y")
        except (ValueError, TypeError):
            return datetime.max

    rows.sort(key=lambda r: parse_exp(r[2] or ""))
    sym, exch, _exp = rows[0]
    logger.info(
        "Exact-expiry FUT %s not found, falling back to nearest %s on %s",
        expected_symbol, sym, exch,
    )
    return {"symbol": sym, "exchange": exch}


def _get_futures_price(
    base_symbol: str,
    options_exchange: str,
    expiry_ddmmmyy: str,
    auth_token: str,
    broker: str,
    config: dict | None,
) -> float | None:
    """Look up the matching-expiry futures contract and fetch its LTP."""
    fut = _find_futures_symbol(base_symbol, options_exchange, expiry_ddmmmyy)
    if not fut:
        logger.info("No FUT contract for %s %s on %s", base_symbol, expiry_ddmmmyy, options_exchange)
        return None
    ok, resp, _ = get_quotes_with_auth(
        symbol=fut["symbol"], exchange=fut["exchange"],
        auth_token=auth_token, broker=broker, config=config,
    )
    if not ok:
        return None
    return resp.get("data", {}).get("ltp")


def get_oi_tracker_data(
    underlying: str,
    exchange: str,
    expiry_date: str,
    auth_token: str,
    broker: str,
    config: dict | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """Build the OI Tracker payload for a (underlying, exchange, expiry).

    Strategy: pull an option chain (23 strikes each side of ATM = 47 strikes,
    94 symbols), then aggregate totals/PCR/per-strike OI and look up the
    matching FUT price. Sized to fit the Fyers multiquote OI bucket
    (<=100 symbols) so OI is populated instead of returning 0 for every strike.
    """
    try:
        ok, chain_resp, status = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=23,
            auth_token=auth_token,
            broker=broker,
            config=config,
        )
        if not ok:
            return False, chain_resp, status

        full_chain = chain_resp.get("chain", [])
        atm_strike = chain_resp.get("atm_strike")
        spot_price = chain_resp.get("underlying_ltp")
        base_symbol = chain_resp.get("underlying", underlying).upper()

        total_ce_oi = 0
        total_pe_oi = 0
        total_ce_volume = 0
        total_pe_volume = 0
        lot_size: int | None = None
        oi_chain: list[dict] = []

        for item in full_chain:
            ce = item.get("ce") or {}
            pe = item.get("pe") or {}
            ce_oi = ce.get("oi", 0) or 0
            pe_oi = pe.get("oi", 0) or 0
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi
            total_ce_volume += ce.get("volume", 0) or 0
            total_pe_volume += pe.get("volume", 0) or 0
            if lot_size is None:
                lot_size = ce.get("lotsize") or pe.get("lotsize")
            oi_chain.append({"strike": item["strike"], "ce_oi": ce_oi, "pe_oi": pe_oi})

        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0
        pcr_volume = round(total_pe_volume / total_ce_volume, 2) if total_ce_volume else 0

        # Resolve options exchange to use for FUT lookup. The chain service
        # already mapped index/equity exchanges → NFO/BFO for the OI side, so
        # we mirror that mapping here.
        options_exchange = exchange.upper()
        if options_exchange in ("NSE_INDEX", "NSE"):
            options_exchange = "NFO"
        elif options_exchange in ("BSE_INDEX", "BSE"):
            options_exchange = "BFO"

        # Use the same DDMMMYY format the chain accepted.
        m = _FUT_PREFIX_RE.match(f"{base_symbol}{expiry_date.upper()}FUT")
        # Don't fail OI tracker if FUT lookup misfires — just return None price.
        futures_price: float | None = None
        if m:
            futures_price = _get_futures_price(
                base_symbol=base_symbol,
                options_exchange=options_exchange,
                expiry_ddmmmyy=expiry_date.upper(),
                auth_token=auth_token, broker=broker, config=config,
            )

        return True, {
            "status": "success",
            "underlying": base_symbol,
            "spot_price": spot_price,
            "futures_price": futures_price,
            "lot_size": lot_size or 1,
            "pcr_oi": pcr_oi,
            "pcr_volume": pcr_volume,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "atm_strike": atm_strike,
            "expiry_date": expiry_date,
            "chain": oi_chain,
        }, 200

    except Exception as e:
        logger.exception("Error in get_oi_tracker_data: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
