"""
Zerodha data transformation - maps OpenBull order format to Zerodha Kite API format.
Adapted from OpenAlgo's zerodha transform_data.py.
"""

import logging

logger = logging.getLogger(__name__)


def _get_br_symbol(symbol: str, exchange: str) -> str:
    """Look up broker symbol from in-memory cache."""
    from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def transform_data(data: dict) -> dict:
    """Transform OpenBull order data to Zerodha Kite API format."""
    symbol = _get_br_symbol(data["symbol"], data["exchange"])

    return {
        "tradingsymbol": symbol,
        "exchange": data["exchange"],
        "transaction_type": data["action"].upper(),
        "order_type": data["pricetype"],
        "quantity": data["quantity"],
        "product": data["product"],
        "price": data.get("price", "0"),
        "trigger_price": data.get("trigger_price", "0"),
        "disclosed_quantity": data.get("disclosed_quantity", "0"),
        "validity": "DAY",
        "market_protection": "-1",
        "tag": "openbull",
    }


def transform_modify_order_data(data: dict) -> dict:
    """Transform modify order data to Zerodha Kite API format."""
    return {
        "order_type": map_order_type(data["pricetype"]),
        "quantity": data["quantity"],
        "price": data["price"],
        "trigger_price": data.get("trigger_price", "0"),
        "disclosed_quantity": data.get("disclosed_quantity", "0"),
        "validity": "DAY",
    }


def map_order_type(pricetype: str) -> str:
    """Map OpenBull pricetype to Zerodha order_type."""
    order_type_mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "SL",
        "SL-M": "SL-M",
    }
    return order_type_mapping.get(pricetype, "MARKET")


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Zerodha product type (1:1 mapping)."""
    product_type_mapping = {
        "CNC": "CNC",
        "NRML": "NRML",
        "MIS": "MIS",
    }
    return product_type_mapping.get(product, "MIS")


def reverse_map_product_type(exchange: str, product: str) -> str | None:
    """Reverse map Zerodha product type to OpenBull product type (1:1 for Zerodha)."""
    mapping = {
        "CNC": "CNC",
        "NRML": "NRML",
        "MIS": "MIS",
    }
    return mapping.get(product)
