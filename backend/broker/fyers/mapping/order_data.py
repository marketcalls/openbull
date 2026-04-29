"""
Fyers order data mapping - transforms broker response data to OpenBull standard format.
Adapted from OpenAlgo's fyers order_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_symbol_from_brsymbol_cache

logger = logging.getLogger(__name__)


# Mapping of (Exchange Code, Segment Code) to OpenBull exchange string.
# Source: Fyers API docs.
EXCHANGE_MAP = {
    (10, 10): "NSE",
    (10, 11): "NFO",
    (10, 12): "CDS",
    (12, 10): "BSE",
    (12, 11): "BFO",
    (11, 20): "MCX",
}


def get_exchange(exchange_code, segment_code) -> str:
    """Return the OpenBull exchange string for the given Fyers exchange/segment codes."""
    return EXCHANGE_MAP.get((exchange_code, segment_code), "Unknown Exchange")


def _resolve_oa_symbol(brsymbol: str | None, exchange: str) -> str | None:
    """Best-effort lookup of OpenBull symbol given a Fyers broker symbol."""
    if not brsymbol:
        return None
    return get_symbol_from_brsymbol_cache(brsymbol, exchange)


def map_order_data(order_data: dict) -> list[dict]:
    """Process Fyers raw orderBook response into a list with normalized symbol/exchange.

    Mutates each order dict in place to set:
        ``symbol`` -> OpenBull symbol (when found in cache)
        ``exchange`` -> OpenBull exchange string
    """
    if not order_data or order_data.get("orderBook") is None:
        logger.debug("No order data available in 'orderBook'.")
        return []

    order_list = order_data["orderBook"]
    for order in order_list:
        exchange_code = order.get("exchange")
        segment_code = order.get("segment")
        exchange = get_exchange(exchange_code, segment_code)
        brsymbol = order.get("symbol")

        oa_symbol = _resolve_oa_symbol(brsymbol, exchange)
        if oa_symbol:
            order["symbol"] = oa_symbol
            order["exchange"] = exchange
        else:
            order["exchange"] = exchange if exchange != "Unknown Exchange" else order.get("exchange", "")

    return order_list


def calculate_order_statistics(order_data: list[dict]) -> dict:
    """Calculate order statistics from order data."""
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    if not order_data:
        return {
            "total_buy_orders": 0,
            "total_sell_orders": 0,
            "total_completed_orders": 0,
            "total_open_orders": 0,
            "total_rejected_orders": 0,
        }

    for order in order_data:
        if order.get("side") == 1:
            total_buy_orders += 1
        elif order.get("side") == -1:
            total_sell_orders += 1

        status = order.get("status")
        if status == 2:
            total_completed_orders += 1
        elif status == 6:
            total_open_orders += 1
        elif status == 5:
            total_rejected_orders += 1

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


_STATUS_MAP = {2: "complete", 5: "rejected", 4: "trigger pending", 6: "open", 1: "cancelled"}
_SIDE_MAP = {1: "BUY", -1: "SELL"}
_TYPE_MAP = {1: "LIMIT", 2: "MARKET", 3: "SL-M", 4: "SL"}
_PRODUCT_MAP = {"CNC": "CNC", "INTRADAY": "MIS", "MARGIN": "NRML", "CO": "CO", "BO": "BO"}


def transform_order_data(orders) -> list[dict]:
    """Transform Fyers order data to OpenBull standard format.

    Numeric fields (price, trigger_price, quantity) are returned as numbers
    so the frontend can apply ``.toFixed(2)`` directly.
    """
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders = []
    for order in orders:
        if not isinstance(order, dict):
            logger.warning("Expected a dict, but found %s. Skipping this item.", type(order))
            continue

        order_status_code = order.get("status")
        order_status = _STATUS_MAP.get(order_status_code, "unknown")
        action = _SIDE_MAP.get(order.get("side"), "unknown")
        ordertype = _TYPE_MAP.get(order.get("type"), "unknown")
        producttype = _PRODUCT_MAP.get(order.get("productType"), "unknown")

        transformed_orders.append({
            "symbol": order.get("symbol", ""),
            "exchange": order.get("exchange", ""),
            "action": action,
            "quantity": int(order.get("qty", 0) or 0),
            "price": float(order.get("limitPrice", 0.0) or 0.0),
            "trigger_price": float(order.get("stopPrice", 0.0) or 0.0),
            "pricetype": ordertype,
            "product": producttype,
            "orderid": order.get("id", ""),
            "order_status": order_status,
            "timestamp": order.get("orderDateTime", ""),
        })

    return transformed_orders


def map_trade_data(trade_data: dict) -> list[dict]:
    """Process Fyers raw tradeBook into normalized list (in-place mutate)."""
    if not trade_data or trade_data.get("tradeBook") is None:
        logger.debug("No trade data available in 'tradeBook'.")
        return []

    trade_list = trade_data["tradeBook"]
    for trade in trade_list:
        exchange_code = trade.get("exchange")
        segment_code = trade.get("segment")
        exchange = get_exchange(exchange_code, segment_code)
        brsymbol = trade.get("symbol")

        oa_symbol = _resolve_oa_symbol(brsymbol, exchange)
        if oa_symbol:
            trade["symbol"] = oa_symbol
            trade["exchange"] = exchange
        else:
            trade["exchange"] = exchange if exchange != "Unknown Exchange" else trade.get("exchange", "")

    return trade_list


def transform_tradebook_data(tradebook_data: list[dict]) -> list[dict]:
    """Transform tradebook data to OpenBull standard format."""
    transformed_data = []
    for trade in tradebook_data:
        action = _SIDE_MAP.get(trade.get("side"), "unknown")
        producttype = _PRODUCT_MAP.get(trade.get("productType"), "unknown")

        quantity = int(trade.get("tradedQty", 0) or 0)
        average_price = float(trade.get("tradePrice", 0.0) or 0.0)
        trade_value_raw = trade.get("tradeValue")
        if trade_value_raw is None:
            trade_value = quantity * average_price
        else:
            trade_value = float(trade_value_raw or 0.0)

        transformed_data.append({
            "symbol": trade.get("symbol", ""),
            "exchange": trade.get("exchange", ""),
            "product": producttype,
            "action": action,
            "quantity": quantity,
            "average_price": average_price,
            "trade_value": trade_value,
            "orderid": trade.get("orderNumber", ""),
            "timestamp": trade.get("orderDateTime", ""),
        })
    return transformed_data


def map_position_data(position_data: dict) -> list[dict]:
    """Process Fyers raw netPositions into normalized list (in-place mutate)."""
    if not position_data or position_data.get("netPositions") is None:
        logger.debug("No position data available in 'netPositions'.")
        return []

    position_list = position_data["netPositions"]
    for position in position_list:
        exchange_code = position.get("exchange")
        segment_code = position.get("segment")
        exchange = get_exchange(exchange_code, segment_code)
        brsymbol = position.get("symbol")

        oa_symbol = _resolve_oa_symbol(brsymbol, exchange)
        if oa_symbol:
            position["symbol"] = oa_symbol
            position["exchange"] = exchange
        else:
            position["exchange"] = exchange if exchange != "Unknown Exchange" else position.get("exchange", "")

    return position_list


def transform_positions_data(positions_data: list[dict]) -> list[dict]:
    """Transform positions data to OpenBull standard format. Returns numbers, not strings."""
    transformed_data = []
    for position in positions_data:
        producttype = _PRODUCT_MAP.get(position.get("productType"), "")

        transformed_data.append({
            "symbol": position.get("symbol", ""),
            "exchange": position.get("exchange", ""),
            "product": producttype,
            "quantity": int(position.get("netQty", 0) or 0),
            "average_price": float(position.get("netAvg", 0.0) or 0.0),
            "ltp": float(position.get("ltp", 0.0) or 0.0),
            "pnl": float(position.get("pl", 0.0) or 0.0),
        })
    return transformed_data


def map_portfolio_data(portfolio_data: dict) -> list[dict]:
    """Process Fyers raw holdings into normalized list (in-place mutate)."""
    if not portfolio_data or portfolio_data.get("holdings") is None:
        logger.debug("No portfolio data available in 'holdings'.")
        return []

    portfolio_list = portfolio_data["holdings"]
    for portfolio in portfolio_list:
        if portfolio.get("holdingType") in ("HLD", "T1"):
            portfolio["holdingType"] = "CNC"

        exchange_code = portfolio.get("exchange")
        segment_code = portfolio.get("segment")
        exchange = get_exchange(exchange_code, segment_code)
        brsymbol = portfolio.get("symbol")

        oa_symbol = _resolve_oa_symbol(brsymbol, exchange)
        if oa_symbol:
            portfolio["symbol"] = oa_symbol
            portfolio["exchange"] = exchange
        else:
            portfolio["exchange"] = exchange if exchange != "Unknown Exchange" else portfolio.get("exchange", "")

    return portfolio_list


def transform_holdings_data(holdings_data: list[dict]) -> list[dict]:
    """Transform holdings data to OpenBull standard format. Returns numbers, not strings."""
    transformed_data = []
    for holdings in holdings_data:
        cost_price = float(holdings.get("costPrice", 0.0) or 0.0)
        ltp = float(holdings.get("ltp", 0.0) or 0.0)
        pnl = float(holdings.get("pl", 0.0) or 0.0)

        if cost_price and cost_price != 0:
            pnlpercent = round((ltp - cost_price) / cost_price * 100, 2)
        else:
            pnlpercent = 0.0

        transformed_data.append({
            "symbol": holdings.get("symbol", ""),
            "exchange": holdings.get("exchange", ""),
            "quantity": int(holdings.get("quantity", 0) or 0),
            "product": holdings.get("holdingType", ""),
            "average_price": cost_price,
            "ltp": ltp,
            "pnl": pnl,
            "pnlpercent": pnlpercent,
        })
    return transformed_data


def calculate_portfolio_statistics(holdings_data: list[dict]) -> dict:
    """Calculate portfolio statistics from raw holdings (uses Fyers raw field names)."""
    totalholdingvalue = sum(
        float(item.get("ltp", 0.0) or 0.0) * float(item.get("quantity", 0) or 0)
        for item in holdings_data
    )
    totalinvvalue = sum(
        float(item.get("costPrice", 0.0) or 0.0) * float(item.get("quantity", 0) or 0)
        for item in holdings_data
    )
    totalprofitandloss = sum(float(item.get("pl", 0.0) or 0.0) for item in holdings_data)

    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0
    totalpnlpercentage = round(totalpnlpercentage, 2)

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }
