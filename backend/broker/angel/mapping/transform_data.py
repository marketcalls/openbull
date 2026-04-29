"""
Angel One order data transformation.
Maps OpenBull order format to Angel SmartAPI placeOrder format and back.
Adapted from OpenAlgo's angel transform_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache

logger = logging.getLogger(__name__)


def _br_symbol(symbol: str, exchange: str) -> str:
    """Look up broker symbol from the in-memory cache (shared with upstox)."""
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def transform_data(data: dict, token: str) -> dict:
    """Transform OpenBull order request to Angel SmartAPI placeOrder format."""
    symbol = _br_symbol(data["symbol"], data["exchange"])
    transformed = {
        "apikey": data.get("apikey", ""),
        "variety": map_variety(data["pricetype"]),
        "tradingsymbol": symbol,
        "symboltoken": token,
        "transactiontype": data["action"].upper(),
        "exchange": data["exchange"],
        "ordertype": map_order_type(data["pricetype"]),
        "producttype": map_product_type(data["product"]),
        "duration": "DAY",
        "price": data.get("price", "0"),
        "squareoff": "0",
        "stoploss": data.get("trigger_price", "0"),
        "disclosedquantity": data.get("disclosed_quantity", "0"),
        "quantity": data["quantity"],
    }
    transformed["triggerprice"] = data.get("trigger_price", "0")
    return transformed


def transform_modify_order_data(data: dict, token: str) -> dict:
    """Transform OpenBull modify order to Angel modifyOrder format."""
    return {
        "variety": map_variety(data["pricetype"]),
        "orderid": data["orderid"],
        "ordertype": map_order_type(data["pricetype"]),
        "producttype": map_product_type(data["product"]),
        "duration": "DAY",
        "price": data["price"],
        "quantity": data["quantity"],
        "tradingsymbol": data["symbol"],
        "symboltoken": token,
        "exchange": data["exchange"],
        "disclosedquantity": data.get("disclosed_quantity", "0"),
        "stoploss": data.get("trigger_price", "0"),
    }


def map_order_type(pricetype: str) -> str:
    """Map OpenBull pricetype to Angel order type."""
    order_type_mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "STOPLOSS_LIMIT",
        "SL-M": "STOPLOSS_MARKET",
    }
    return order_type_mapping.get(pricetype, "MARKET")


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Angel product type."""
    product_type_mapping = {
        "CNC": "DELIVERY",
        "NRML": "CARRYFORWARD",
        "MIS": "INTRADAY",
    }
    return product_type_mapping.get(product, "INTRADAY")


def map_variety(pricetype: str) -> str:
    """Map pricetype to Angel order variety."""
    variety_mapping = {
        "MARKET": "NORMAL",
        "LIMIT": "NORMAL",
        "SL": "STOPLOSS",
        "SL-M": "STOPLOSS",
    }
    return variety_mapping.get(pricetype, "NORMAL")


def reverse_map_product_type(product: str) -> str | None:
    """Reverse map Angel product type to OpenBull product type."""
    reverse_product_type_mapping = {
        "DELIVERY": "CNC",
        "CARRYFORWARD": "NRML",
        "INTRADAY": "MIS",
    }
    return reverse_product_type_mapping.get(product)
