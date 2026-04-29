"""
Fyers data transformation - maps OpenBull order format to Fyers API format.
Adapted from OpenAlgo's fyers transform_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache

logger = logging.getLogger(__name__)


def _br_symbol(symbol: str, exchange: str) -> str:
    """Look up Fyers broker symbol from in-memory cache; fallback to raw."""
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def transform_data(data: dict) -> dict:
    """Transform OpenBull order data to the Fyers API order payload."""
    symbol = _br_symbol(data["symbol"], data["exchange"])

    quantity = int(data["quantity"])
    price = float(data.get("price", 0) or 0)
    trigger_price = float(data.get("trigger_price", 0) or 0)
    disclosed_quantity = int(data.get("disclosed_quantity", 0) or 0)

    return {
        "symbol": symbol,
        "qty": quantity,
        "type": map_order_type(data["pricetype"]),
        "side": map_action(data["action"]),
        "productType": map_product_type(data["product"]),
        "limitPrice": price,
        "stopPrice": trigger_price,
        "validity": "DAY",
        "disclosedQty": disclosed_quantity,
        "offlineOrder": False,
        "stopLoss": 0,
        "takeProfit": 0,
        "orderTag": "openbull",
    }


def transform_modify_order_data(data: dict) -> dict:
    """Transform modify-order payload to Fyers API format with safe coercion."""
    order_id = data.get("orderid", "N/A")

    try:
        quantity = int(data.get("quantity", 0) or 0)
    except (ValueError, TypeError) as e:
        logger.warning(
            "Could not parse quantity for order modification %s. Defaulting to 0. Error: %s",
            order_id, e,
        )
        quantity = 0

    try:
        price = float(data.get("price", 0)) if data.get("price") else 0.0
    except (ValueError, TypeError) as e:
        logger.warning(
            "Could not parse price for order modification %s. Defaulting to 0.0. Error: %s",
            order_id, e,
        )
        price = 0.0

    try:
        trigger_price = (
            float(data.get("trigger_price", 0)) if data.get("trigger_price") else 0.0
        )
    except (ValueError, TypeError) as e:
        logger.warning(
            "Could not parse trigger_price for order modification %s. Defaulting to 0.0. Error: %s",
            order_id, e,
        )
        trigger_price = 0.0

    return {
        "id": data["orderid"],
        "qty": quantity,
        "type": map_order_type(data.get("pricetype", "")),
        "limitPrice": price,
        "stopPrice": trigger_price,
    }


def map_order_type(pricetype: str) -> int:
    """Map OpenBull pricetype to Fyers order type integer."""
    order_type_mapping = {"MARKET": 2, "LIMIT": 1, "SL": 4, "SL-M": 3}
    order_type = order_type_mapping.get(pricetype)
    if order_type is None:
        logger.warning("Unknown pricetype '%s' received. Defaulting to MARKET (2).", pricetype)
        return 2
    return order_type


def map_action(action: str) -> int | None:
    """Map OpenBull action to Fyers side integer (1=BUY, -1=SELL)."""
    action_mapping = {"BUY": 1, "SELL": -1}
    side = action_mapping.get(action)
    if side is None:
        logger.warning("Unknown action '%s' received. Cannot map to a side.", action)
    return side


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Fyers product type."""
    product_type_mapping = {
        "CNC": "CNC",
        "NRML": "MARGIN",
        "MIS": "INTRADAY",
        "CO": "CO",
        "BO": "BO",
    }
    fyers_product = product_type_mapping.get(product)
    if fyers_product is None:
        logger.warning("Unknown product type '%s' received. Defaulting to INTRADAY.", product)
        return "INTRADAY"
    return fyers_product


def reverse_map_product_type(product: str) -> str | None:
    """Reverse map Fyers product type to OpenBull product type."""
    reverse_product_mapping = {
        "CNC": "CNC",
        "MARGIN": "NRML",
        "INTRADAY": "MIS",
        "CO": "CO",
        "BO": "BO",
    }
    oa_product = reverse_product_mapping.get(product)
    if oa_product is None:
        logger.warning(
            "Unknown Fyers product type '%s' received. Cannot map to OpenBull product type.",
            product,
        )
    return oa_product
