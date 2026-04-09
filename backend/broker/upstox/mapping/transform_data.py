"""
Upstox data transformation - maps OpenBull order format to Upstox API format.
Adapted from OpenAlgo's upstox transform_data.py.
"""

import logging

logger = logging.getLogger(__name__)


def transform_data(data: dict, token: str) -> dict:
    """Transform OpenBull order data to Upstox API format."""
    return {
        "quantity": data["quantity"],
        "product": map_product_type(data["product"]),
        "validity": "DAY",
        "price": data.get("price", "0"),
        "tag": "string",
        "instrument_token": token,
        "order_type": map_order_type(data["pricetype"]),
        "transaction_type": data["action"].upper(),
        "disclosed_quantity": data.get("disclosed_quantity", "0"),
        "trigger_price": data.get("trigger_price", "0"),
        "is_amo": "false",
    }


def transform_modify_order_data(data: dict) -> dict:
    """Transform modify order data to Upstox API format."""
    return {
        "quantity": data["quantity"],
        "validity": "DAY",
        "price": data["price"],
        "order_id": data["orderid"],
        "order_type": map_order_type(data["pricetype"]),
        "disclosed_quantity": data.get("disclosed_quantity", "0"),
        "trigger_price": data.get("trigger_price", "0"),
    }


def map_order_type(pricetype: str) -> str:
    """Map OpenBull pricetype to Upstox order_type."""
    order_type_mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "SL",
        "SL-M": "SL-M",
    }
    if pricetype not in order_type_mapping:
        logger.warning("Unknown pricetype '%s'. Defaulting to 'MARKET'.", pricetype)
        return "MARKET"
    return order_type_mapping[pricetype]


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Upstox product type."""
    product_type_mapping = {
        "CNC": "D",
        "NRML": "D",
        "MIS": "I",
    }
    if product not in product_type_mapping:
        logger.warning("Unknown product type '%s'. Defaulting to 'I' (Intraday).", product)
        return "I"
    return product_type_mapping[product]


def reverse_map_product_type(exchange: str, product: str) -> str | None:
    """Reverse map Upstox product type to OpenBull product type."""
    exchange_mapping_for_d = {
        "NSE": "CNC",
        "BSE": "CNC",
        "NFO": "NRML",
        "BFO": "NRML",
        "MCX": "NRML",
        "CDS": "NRML",
    }
    if product == "D":
        return exchange_mapping_for_d.get(exchange)
    elif product == "I":
        return "MIS"
    else:
        logger.warning("Unknown product type '%s' for reverse mapping.", product)
        return None
