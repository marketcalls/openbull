"""
Zerodha order data mapping - transforms broker response data to OpenBull standard format.
Adapted from OpenAlgo's zerodha order_data.py.
"""

import logging

logger = logging.getLogger(__name__)


def _get_oa_symbol(brsymbol: str, exchange: str) -> str:
    """Look up OpenBull symbol from broker symbol using in-memory cache."""
    from backend.broker.upstox.mapping.order_data import get_symbol_from_brsymbol_cache
    return get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol


def map_order_data(order_data: dict) -> list[dict]:
    """Map Zerodha order data, converting broker symbols to OpenBull symbols."""
    if order_data.get("data") is None:
        logger.debug("No order data available to map.")
        return []

    data = order_data["data"]

    for order in data:
        exchange = order.get("exchange", "")
        symbol = order.get("tradingsymbol", "")
        if symbol:
            order["tradingsymbol"] = _get_oa_symbol(brsymbol=symbol, exchange=exchange)

    return data


def calculate_order_statistics(order_data: list[dict]) -> dict:
    """Calculate order statistics from order data."""
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    for order in order_data:
        if order.get("transaction_type") == "BUY":
            total_buy_orders += 1
        elif order.get("transaction_type") == "SELL":
            total_sell_orders += 1

        status = order.get("status", "").upper()
        if status == "COMPLETE":
            total_completed_orders += 1
        elif status == "OPEN":
            total_open_orders += 1
        elif status == "REJECTED":
            total_rejected_orders += 1

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def transform_order_data(orders) -> list[dict]:
    """Transform Zerodha order data to OpenBull standard format."""
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders = []
    for order in orders:
        if not isinstance(order, dict):
            continue

        raw_status = order.get("status", "").upper()
        status_map = {
            "COMPLETE": "complete",
            "REJECTED": "rejected",
            "TRIGGER PENDING": "trigger pending",
            "OPEN": "open",
            "CANCELLED": "cancelled",
        }
        order_status = status_map.get(raw_status, raw_status.lower())

        transformed_orders.append({
            "symbol": order.get("tradingsymbol", ""),
            "exchange": order.get("exchange", ""),
            "action": order.get("transaction_type", ""),
            "quantity": order.get("quantity", 0),
            "price": order.get("price", 0.0),
            "trigger_price": order.get("trigger_price", 0.0),
            "pricetype": order.get("order_type", ""),
            "product": order.get("product", ""),
            "orderid": order.get("order_id", ""),
            "order_status": order_status,
            "timestamp": order.get("order_timestamp", ""),
        })

    return transformed_orders


def map_trade_data(trade_data: dict) -> list[dict]:
    """Map trade data (same as order data)."""
    return map_order_data(trade_data)


def transform_tradebook_data(tradebook_data: list[dict]) -> list[dict]:
    """Transform tradebook data to OpenBull standard format."""
    transformed_data = []
    for trade in tradebook_data:
        transformed_data.append({
            "symbol": trade.get("tradingsymbol", ""),
            "exchange": trade.get("exchange", ""),
            "product": trade.get("product", ""),
            "action": trade.get("transaction_type", ""),
            "quantity": trade.get("quantity", 0),
            "average_price": trade.get("average_price", 0.0),
            "trade_value": trade.get("quantity", 0) * trade.get("average_price", 0.0),
            "orderid": trade.get("order_id", ""),
            "timestamp": trade.get("order_timestamp", ""),
        })
    return transformed_data


def map_position_data(position_data: dict) -> list[dict]:
    """Map Zerodha position data (net positions)."""
    if position_data.get("data") is None or position_data["data"].get("net") is None:
        logger.debug("No position data available to map.")
        return []

    data = position_data["data"]["net"]

    for position in data:
        exchange = position.get("exchange", "")
        symbol = position.get("tradingsymbol", "")
        if symbol:
            position["tradingsymbol"] = _get_oa_symbol(brsymbol=symbol, exchange=exchange)

    return data


def transform_positions_data(positions_data: list[dict]) -> list[dict]:
    """Transform positions data to OpenBull standard format.

    Numeric fields must be returned as numbers (not pre-formatted strings) —
    the frontend calls .toFixed(2) on average_price / ltp / pnl, which fails
    on strings.
    """
    transformed_data = []
    for position in positions_data:
        avg_price_raw = position.get("average_price", 0.0)
        try:
            average_price = float(avg_price_raw) if avg_price_raw is not None else 0.0
        except (TypeError, ValueError):
            average_price = 0.0

        try:
            quantity = int(position.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0

        transformed_data.append({
            "symbol": position.get("tradingsymbol", ""),
            "exchange": position.get("exchange", ""),
            "product": position.get("product", ""),
            "quantity": quantity,
            "pnl": round(float(position.get("pnl", 0.0) or 0.0), 2),
            "average_price": average_price,
            "ltp": round(float(position.get("last_price", 0.0) or 0.0), 2),
        })
    return transformed_data


def map_portfolio_data(portfolio_data: dict) -> list[dict]:
    """Map Zerodha portfolio/holdings data."""
    if portfolio_data.get("data") is None:
        logger.debug("No portfolio data available to map.")
        return []

    data = portfolio_data["data"]
    for item in data:
        if item.get("product") == "CNC":
            item["product"] = "CNC"
    return data


def transform_holdings_data(holdings_data: list[dict]) -> list[dict]:
    """Transform holdings data to OpenBull standard format."""
    transformed_data = []
    for holding in holdings_data:
        average_price = float(holding.get("average_price") or 0.0)
        if average_price == 0:
            pnlpercent = 0.0
        else:
            pnlpercent = round(
                (holding.get("last_price", 0) - average_price) / average_price * 100, 2
            )

        transformed_data.append({
            "symbol": holding.get("tradingsymbol", ""),
            "exchange": holding.get("exchange", ""),
            "quantity": holding.get("quantity", 0),
            "product": holding.get("product", ""),
            "average_price": average_price,
            "ltp": holding.get("last_price", 0.0),
            "pnl": round(holding.get("pnl", 0.0), 2),
            "pnlpercent": pnlpercent,
        })
    return transformed_data


def calculate_portfolio_statistics(holdings_data: list[dict]) -> dict:
    """Calculate portfolio statistics from holdings data."""
    totalholdingvalue = sum(item.get("last_price", 0) * item.get("quantity", 0) for item in holdings_data)
    totalinvvalue = sum(item.get("average_price", 0) * item.get("quantity", 0) for item in holdings_data)
    totalprofitandloss = sum(item.get("pnl", 0) for item in holdings_data)
    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }
