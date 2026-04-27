"""
Symbol service - orchestrates master contract download and symbol search.
"""

import importlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def download_master_contracts(broker_name: str, auth_token: str | None = None) -> dict[str, Any]:
    """Download master contracts for the given broker.

    Args:
        broker_name: Name of the broker (upstox, zerodha, etc.)
        auth_token: Broker auth token (required for some brokers like Zerodha)

    Returns:
        dict with status/message/count
    """
    try:
        module_path = f"backend.broker.{broker_name}.database.master_contract_db"
        module = importlib.import_module(module_path)
    except ImportError as e:
        logger.error("Failed to import master contract module for %s: %s", broker_name, e)
        return {"status": "error", "message": f"Broker '{broker_name}' master contract module not found"}

    try:
        if broker_name == "zerodha":
            return module.master_contract_download(auth_token=auth_token)
        else:
            return module.master_contract_download()
    except Exception as e:
        logger.error("Master contract download failed for %s: %s", broker_name, e)
        return {"status": "error", "message": str(e)}


_OPTION_SYMBOL_PREFIX_RE = re.compile(r"^([A-Z0-9]+?)(\d{2}[A-Z]{3}\d{2})\d")


def get_option_underlyings(exchange: str) -> list[dict]:
    """Return distinct option underlyings for an exchange as {symbol, name} pairs.

    The option-chain API needs the base ticker (the part of the option symbol
    before the expiry — e.g. "RELIANCE" from "RELIANCE28APR262500CE", "360ONE"
    from "360ONE28APR2620CE"), not the company name from the `name` column. We
    derive it by parsing the option symbol with a regex so the result is always
    correct, regardless of whether the broker stored the company name or the
    ticker in `name`.

    Each row also carries the human-readable name so the UI can show a friendly
    label while submitting the API-correct symbol.
    """
    from backend.services.market_data_service import _run_query

    rows = _run_query(
        "SELECT MIN(symbol), name FROM symtoken "
        "WHERE exchange = :exch AND instrumenttype IN ('CE','PE') "
        "AND symbol IS NOT NULL AND symbol != '' "
        "GROUP BY name "
        "ORDER BY name",
        {"exch": exchange.upper()},
    )
    out: list[dict] = []
    seen: set[str] = set()
    for sample_symbol, name in rows:
        if not sample_symbol:
            continue
        m = _OPTION_SYMBOL_PREFIX_RE.match(sample_symbol.upper())
        if not m:
            continue
        prefix = m.group(1)
        # Skip exchange test instruments that the broker dumps into the master.
        if "NSETEST" in prefix or "BSETEST" in prefix:
            continue
        if prefix in seen:
            continue
        seen.add(prefix)
        out.append({"symbol": prefix, "name": (name or prefix).strip()})
    out.sort(key=lambda r: r["symbol"])
    return out


async def search_symbols(query: str, exchange: str, broker_name: str = "upstox") -> list[dict]:
    """Search for symbols in the symtoken table.

    The symtoken table is shared across brokers (each download replaces all rows),
    so broker_name determines which module's search_symbols to call.

    Args:
        query: Symbol search string (e.g. "NIFTY")
        exchange: Exchange filter (e.g. "NFO", "NSE")
        broker_name: Broker whose module to use for the search

    Returns:
        List of matching symbol dicts. Empty list only when the broker plugin
        is missing; genuine search failures are re-raised so callers (and the
        error-log sink) see them instead of being hidden behind ``[]``.
    """
    try:
        module_path = f"backend.broker.{broker_name}.database.master_contract_db"
        module = importlib.import_module(module_path)
    except ImportError:
        logger.error("Master contract module not found for broker: %s", broker_name)
        return []

    return await module.search_symbols(query, exchange)
