"""
Angel One order data mapping.
Transforms broker response data to OpenBull standard format.
Adapted from OpenAlgo's angel order_data.py.

Numeric-type rules in transform_* functions: numbers stay numbers (frontend
calls .toFixed(2)). Defensive casts use float(value or 0.0) / int(value or 0).
"""

import logging

from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_symbol_from_brsymbol_cache,
)

logger = logging.getLogger(__name__)


def _get_oa_symbol_from_token(token: str, exchange: str) -> str | None:
    """Resolve OpenBull symbol from Angel symboltoken (uses shared cache).

    The cache is keyed by token alone; ``exchange`` is kept in the signature
    for future proofing and so callers don't need to special-case Angel.
    """
    info = get_symbol_exchange_from_token(token)
    if info:
        return info[0]
    return None


def _get_oa_symbol_from_brsymbol(brsymbol: str, exchange: str) -> str | None:
    return get_symbol_from_brsymbol_cache(brsymbol, exchange)


def map_order_data(order_data: dict) -> list[dict]:
    """Map Angel order data, converting symbol tokens to OpenBull symbols and
    Angel product types to OpenBull product types.
    """
    if not order_data or "data" not in order_data or order_data["data"] is None:
        logger.debug("No order data available to map.")
        return []

    data = order_data["data"]

    for order in data:
        symboltoken = order.get("symboltoken", "")
        exchange = order.get("exchange", "")
        symbol_from_db = _get_oa_symbol_from_token(symboltoken, exchange)
        if symbol_from_db:
            order["tradingsymbol"] = symbol_from_db
        else:
            # Fall back to brsymbol-based lookup (covers cases where the cache
            # was hydrated from a different broker but the brsymbol matches).
            br = order.get("tradingsymbol", "")
            from_br = _get_oa_symbol_from_brsymbol(br, exchange)
            if from_br:
                order["tradingsymbol"] = from_br

        # Normalize Angel product types to OpenBull conventions.
        product = order.get("producttype", "")
        if exchange in ("NSE", "BSE") and product == "DELIVERY":
            order["producttype"] = "CNC"
        elif product == "INTRADAY":
            order["producttype"] = "MIS"
        elif exchange in ("NFO", "MCX", "BFO", "CDS") and product == "CARRYFORWARD":
            order["producttype"] = "NRML"

    return data


def calculate_order_statistics(order_data: list[dict]) -> dict:
    """Calculate order statistics from order data."""
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    for order in order_data:
        if order.get("transactiontype") == "BUY":
            total_buy_orders += 1
        elif order.get("transactiontype") == "SELL":
            total_sell_orders += 1

        status = (order.get("status") or "").lower()
        if status == "complete":
            total_completed_orders += 1
        elif status == "open":
            total_open_orders += 1
        elif status == "rejected":
            total_rejected_orders += 1

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def transform_order_data(orders) -> list[dict]:
    """Transform Angel order data to OpenBull standard format.

    Numeric fields (price, trigger_price, quantity) returned as numbers so the
    frontend's .toFixed(2) calls don't fail on string inputs.
    """
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders = []
    for order in orders:
        if not isinstance(order, dict):
            continue

        ordertype = order.get("ordertype", "")
        if ordertype == "STOPLOSS_LIMIT":
            ordertype = "SL"
        elif ordertype == "STOPLOSS_MARKET":
            ordertype = "SL-M"

        try:
            quantity = int(order.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0

        # Angel returns averageprice once filled; fall back to price for open
        # orders. Cast both defensively since the API can return strings.
        try:
            avg_price = float(order.get("averageprice") or 0.0)
        except (TypeError, ValueError):
            avg_price = 0.0
        try:
            price_field = float(order.get("price") or 0.0)
        except (TypeError, ValueError):
            price_field = 0.0
        price = avg_price if avg_price else price_field

        try:
            trigger_price = float(order.get("triggerprice") or 0.0)
        except (TypeError, ValueError):
            trigger_price = 0.0

        transformed_orders.append({
            "symbol": order.get("tradingsymbol", ""),
            "exchange": order.get("exchange", ""),
            "action": order.get("transactiontype", ""),
            "quantity": quantity,
            "price": price,
            "trigger_price": trigger_price,
            "pricetype": ordertype,
            "product": order.get("producttype", ""),
            "orderid": order.get("orderid", ""),
            "order_status": order.get("status", ""),
            "timestamp": order.get("updatetime", ""),
        })

    return transformed_orders


def map_trade_data(trade_data: dict) -> list[dict]:
    """Map Angel trade data, converting broker symbols to OpenBull symbols and
    Angel product types to OpenBull product types.
    """
    if not trade_data or trade_data.get("data") is None:
        logger.debug("No trade data available to map.")
        return []

    data = trade_data["data"]

    for trade in data:
        symbol = trade.get("tradingsymbol", "")
        exchange = trade.get("exchange", "")
        from_br = _get_oa_symbol_from_brsymbol(symbol, exchange)
        if from_br:
            trade["tradingsymbol"] = from_br

        product = trade.get("producttype", "")
        if exchange in ("NSE", "BSE") and product == "DELIVERY":
            trade["producttype"] = "CNC"
        elif product == "INTRADAY":
            trade["producttype"] = "MIS"
        elif exchange in ("NFO", "MCX", "BFO", "CDS") and product == "CARRYFORWARD":
            trade["producttype"] = "NRML"

    return data


def transform_tradebook_data(tradebook_data: list[dict]) -> list[dict]:
    """Transform Angel tradebook to OpenBull standard format."""
    transformed_data = []
    for trade in tradebook_data:
        try:
            quantity = int(trade.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            average_price = float(trade.get("fillprice") or 0.0)
        except (TypeError, ValueError):
            average_price = 0.0
        try:
            trade_value = float(trade.get("tradevalue") or 0.0)
        except (TypeError, ValueError):
            trade_value = 0.0

        transformed_data.append({
            "symbol": trade.get("tradingsymbol", ""),
            "exchange": trade.get("exchange", ""),
            "product": trade.get("producttype", ""),
            "action": trade.get("transactiontype", ""),
            "quantity": quantity,
            "average_price": average_price,
            "trade_value": trade_value,
            "orderid": trade.get("orderid", ""),
            "timestamp": trade.get("filltime", ""),
        })
    return transformed_data


def map_position_data(position_data: dict) -> list[dict]:
    """Map Angel position data — same as map_order_data."""
    return map_order_data(position_data)


def transform_positions_data(positions_data: list[dict]) -> list[dict]:
    """Transform Angel positions to OpenBull standard format.

    Numeric fields are returned as numbers (frontend calls .toFixed(2)).
    """
    transformed_data = []
    for position in positions_data:
        try:
            quantity = int(position.get("netqty", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            average_price = float(position.get("avgnetprice") or 0.0)
        except (TypeError, ValueError):
            average_price = 0.0
        try:
            ltp = float(position.get("ltp") or 0.0)
        except (TypeError, ValueError):
            ltp = 0.0
        try:
            pnl = float(position.get("pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0

        transformed_data.append({
            "symbol": position.get("tradingsymbol", ""),
            "exchange": position.get("exchange", ""),
            "product": position.get("producttype", ""),
            "quantity": quantity,
            "average_price": average_price,
            "ltp": round(ltp, 2),
            "pnl": round(pnl, 2),
        })
    return transformed_data


def transform_holdings_data(holdings_data) -> list[dict]:
    """Transform Angel holdings to OpenBull standard format.

    Accepts either:
      - a dict in Angel's native shape (``{"holdings": [...], "totalholding": {...}}``)
      - a list of holding dicts (already-extracted)
    """
    if isinstance(holdings_data, dict):
        items = holdings_data.get("holdings", []) or []
    elif isinstance(holdings_data, list):
        items = holdings_data
    else:
        items = []

    transformed_data = []
    for holding in items:
        try:
            quantity = int(holding.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        try:
            average_price = float(holding.get("averageprice") or holding.get("average_price") or 0.0)
        except (TypeError, ValueError):
            average_price = 0.0
        try:
            ltp = float(holding.get("ltp") or holding.get("last_price") or 0.0)
        except (TypeError, ValueError):
            ltp = 0.0
        try:
            pnl = float(holding.get("profitandloss") or holding.get("pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        try:
            pnlpercent = float(
                holding.get("pnlpercentage") or holding.get("pnlpercent") or 0.0
            )
        except (TypeError, ValueError):
            pnlpercent = 0.0

        # If pnlpercent is missing, derive from average_price/ltp.
        if pnlpercent == 0.0 and average_price:
            pnlpercent = round((ltp - average_price) / average_price * 100, 2)

        transformed_data.append({
            "symbol": holding.get("tradingsymbol", ""),
            "exchange": holding.get("exchange", ""),
            "quantity": quantity,
            "product": holding.get("product", ""),
            "average_price": average_price,
            "ltp": ltp,
            "pnl": round(pnl, 2),
            "pnlpercent": round(pnlpercent, 2),
        })
    return transformed_data


def map_portfolio_data(portfolio_data: dict) -> dict:
    """Map Angel portfolio data, normalizing tradingsymbol and product fields.

    Returns a dict with shape ``{"holdings": [...], "totalholding": {...}}``
    matching what calculate_portfolio_statistics / transform_holdings_data
    expect.
    """
    if (
        not portfolio_data
        or portfolio_data.get("data") is None
        or "holdings" not in (portfolio_data.get("data") or {})
    ):
        logger.debug("No portfolio data available to map.")
        return {}

    data = portfolio_data["data"]

    if data.get("holdings"):
        for portfolio in data["holdings"]:
            symbol = portfolio.get("tradingsymbol", "")
            exchange = portfolio.get("exchange", "")
            from_br = _get_oa_symbol_from_brsymbol(symbol, exchange)
            if from_br:
                portfolio["tradingsymbol"] = from_br
            if portfolio.get("product") == "DELIVERY":
                portfolio["product"] = "CNC"

    return data


def calculate_portfolio_statistics(holdings_data: dict) -> dict:
    """Calculate portfolio statistics from Angel holdings data.

    Accepts the dict returned by ``map_portfolio_data`` (i.e. data["data"]).
    """
    if not holdings_data or holdings_data.get("totalholding") is None:
        return {
            "totalholdingvalue": 0,
            "totalinvvalue": 0,
            "totalprofitandloss": 0,
            "totalpnlpercentage": 0,
        }

    total = holdings_data["totalholding"] or {}
    return {
        "totalholdingvalue": total.get("totalholdingvalue", 0),
        "totalinvvalue": total.get("totalinvvalue", 0),
        "totalprofitandloss": total.get("totalprofitandloss", 0),
        "totalpnlpercentage": total.get("totalpnlpercentage", 0),
    }
