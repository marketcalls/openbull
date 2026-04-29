"""
Dhan order data transformation - maps OpenBull order format to Dhan v2 API format.
Adapted verbatim from OpenAlgo's dhan transform_data.py.
"""

import logging

logger = logging.getLogger(__name__)


def transform_data(data: dict, token: str) -> dict:
    """Transform OpenBull order data to Dhan v2 API format.

    Mirrors openalgo behavior: uses dhan_client_id from data when provided,
    falls back to apikey for backwards compatibility.
    """
    transformed = {
        "dhanClientId": data.get("dhan_client_id", data.get("apikey", "")),
        "transactionType": data["action"].upper(),
        "exchangeSegment": map_exchange_type(data["exchange"]),
        "productType": map_product_type(data["product"]),
        "orderType": map_order_type(data["pricetype"]),
        "validity": "DAY",
        "securityId": str(token) if token is not None else "",
        "quantity": int(data["quantity"]),
    }

    correlation_id = data.get("correlation_id", "")
    if correlation_id:
        transformed["correlationId"] = correlation_id

    if data["pricetype"] != "MARKET":
        price = float(data.get("price", 0) or 0)
        transformed["price"] = float(price)

    disclosed_qty = int(data.get("disclosed_quantity", 0) or 0)
    if disclosed_qty > 0:
        transformed["disclosedQuantity"] = disclosed_qty

    if data["pricetype"] in ["SL", "SL-M"]:
        trigger_price = float(data.get("trigger_price", 0) or 0)
        if trigger_price > 0:
            transformed["triggerPrice"] = float(trigger_price)
        else:
            raise ValueError("Trigger price is required for Stop Loss orders")

    after_market = data.get("after_market_order", False)
    if after_market:
        transformed["afterMarketOrder"] = True
        amo_time = data.get("amo_time", "")
        if amo_time in ["PRE_OPEN", "OPEN", "OPEN_30", "OPEN_60"]:
            transformed["amoTime"] = amo_time

    if data.get("product") == "BO":
        bo_profit = data.get("bo_profit_value")
        bo_stop_loss = data.get("bo_stop_loss_value")
        if bo_profit:
            transformed["boProfitValue"] = float(bo_profit)
        if bo_stop_loss:
            transformed["boStopLossValue"] = float(bo_stop_loss)

    if data.get("validity") == "IOC":
        transformed["validity"] = "IOC"

    return transformed


def transform_modify_order_data(data: dict) -> dict:
    """Transform modify order data to Dhan v2 API format."""
    modified = {
        "dhanClientId": data.get("dhan_client_id", data.get("apikey", "")),
        "orderId": data["orderid"],
        "orderType": map_order_type(data["pricetype"]),
        "legName": "ENTRY_LEG",
        "quantity": int(data["quantity"]),
        "validity": "DAY",
    }

    if data.get("pricetype") != "MARKET":
        modified["price"] = float(data["price"])

    disclosed_qty = int(data.get("disclosed_quantity", 0) or 0)
    if disclosed_qty > 0:
        modified["disclosedQuantity"] = disclosed_qty

    if data["pricetype"] in ["SL", "SL-M"]:
        trigger_price = float(data.get("trigger_price", 0) or 0)
        if trigger_price > 0:
            modified["triggerPrice"] = float(trigger_price)

    return modified


def map_order_type(pricetype: str) -> str:
    """Map OpenBull pricetype to Dhan order type."""
    order_type_mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "STOP_LOSS",
        "SL-M": "STOP_LOSS_MARKET",
    }
    return order_type_mapping.get(pricetype, "MARKET")


def map_exchange_type(exchange: str) -> str | None:
    """Map OpenBull exchange to Dhan exchangeSegment."""
    exchange_mapping = {
        "NSE": "NSE_EQ",
        "BSE": "BSE_EQ",
        "CDS": "NSE_CURRENCY",
        "NFO": "NSE_FNO",
        "BFO": "BSE_FNO",
        "BCD": "BSE_CURRENCY",
        "MCX": "MCX_COMM",
        "NSE_INDEX": "IDX_I",
        "BSE_INDEX": "IDX_I",
    }
    return exchange_mapping.get(exchange)


def map_exchange(brexchange: str) -> str | None:
    """Map Dhan exchangeSegment to OpenBull exchange."""
    exchange_mapping = {
        "NSE_EQ": "NSE",
        "BSE_EQ": "BSE",
        "NSE_CURRENCY": "CDS",
        "NSE_FNO": "NFO",
        "BSE_FNO": "BFO",
        "BSE_CURRENCY": "BCD",
        "MCX_COMM": "MCX",
        "IDX_I": "NSE_INDEX",
    }
    return exchange_mapping.get(brexchange)


def map_product_type(product: str) -> str:
    """Map OpenBull product type to Dhan product type."""
    product_type_mapping = {
        "CNC": "CNC",
        "NRML": "MARGIN",
        "MIS": "INTRADAY",
    }
    return product_type_mapping.get(product, "INTRADAY")


def reverse_map_product_type(product: str) -> str | None:
    """Reverse map Dhan product type to OpenBull product type."""
    product_mapping = {"CNC": "CNC", "MARGIN": "NRML", "INTRADAY": "MIS"}
    return product_mapping.get(product)
