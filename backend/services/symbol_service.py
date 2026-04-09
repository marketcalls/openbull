"""
Symbol service - orchestrates master contract download and symbol search.
"""

import importlib
import logging
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


def search_symbols(query: str, exchange: str, broker_name: str = "upstox") -> list[dict]:
    """Search for symbols in the symtoken table.

    The symtoken table is shared across brokers (each download replaces all rows),
    so broker_name determines which module's search_symbols to call. In practice
    the search logic is the same SQL query for all brokers.

    Args:
        query: Symbol search string (e.g. "NIFTY")
        exchange: Exchange filter (e.g. "NFO", "NSE")
        broker_name: Broker whose module to use for the search

    Returns:
        List of matching symbol dicts
    """
    try:
        module_path = f"backend.broker.{broker_name}.database.master_contract_db"
        module = importlib.import_module(module_path)
        return module.search_symbols(query, exchange)
    except ImportError:
        logger.error("Master contract module not found for broker: %s", broker_name)
        return []
    except Exception as e:
        logger.error("Symbol search failed: %s", e)
        return []
