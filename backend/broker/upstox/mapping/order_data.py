"""
Upstox order data mapping - transforms broker response data to OpenBull standard format.
Adapted from OpenAlgo's upstox order_data.py.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


# In-memory caches populated from DB on startup
_token_to_symbol: dict[str, str] | None = None  # token -> symbol
_symbol_exchange_to_token: dict[tuple[str, str], str] | None = None  # (symbol, exchange) -> token
_symbol_exchange_to_brsymbol: dict[tuple[str, str], str] | None = None  # (symbol, exchange) -> brsymbol
_brsymbol_exchange_to_symbol: dict[tuple[str, str], str] | None = None  # (brsymbol, exchange) -> symbol

# Keep backward-compatible alias
_symbol_cache = None


async def _load_symbol_cache():
    """Load all symbol mappings into memory from the DB."""
    global _symbol_cache, _token_to_symbol, _symbol_exchange_to_token, _symbol_exchange_to_brsymbol, _brsymbol_exchange_to_symbol
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from backend.config import get_settings

    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT token, symbol, exchange, brsymbol FROM symtoken")
            )
            rows = result.fetchall()

            _token_to_symbol = {}
            _symbol_exchange_to_token = {}
            _symbol_exchange_to_brsymbol = {}
            _brsymbol_exchange_to_symbol = {}

            for token, symbol, exchange, brsymbol in rows:
                _token_to_symbol[token] = symbol
                _symbol_exchange_to_token[(symbol, exchange)] = token
                _symbol_exchange_to_brsymbol[(symbol, exchange)] = brsymbol
                _brsymbol_exchange_to_symbol[(brsymbol, exchange)] = symbol

            _symbol_cache = _token_to_symbol
        logger.info("Symbol cache loaded with %d entries", len(_token_to_symbol))
    finally:
        await engine.dispose()


def _get_symbol_from_cache(token: str) -> str | None:
    """Look up symbol from in-memory cache."""
    if _token_to_symbol is None:
        return None
    return _token_to_symbol.get(token)


def get_token_from_cache(symbol: str, exchange: str) -> str | None:
    """Look up instrument token from in-memory cache."""
    if _symbol_exchange_to_token is None:
        return None
    return _symbol_exchange_to_token.get((symbol, exchange))


def get_brsymbol_from_cache(symbol: str, exchange: str) -> str | None:
    """Look up broker symbol from in-memory cache."""
    if _symbol_exchange_to_brsymbol is None:
        return None
    return _symbol_exchange_to_brsymbol.get((symbol, exchange))


def get_symbol_from_brsymbol_cache(brsymbol: str, exchange: str) -> str | None:
    """Look up OpenBull symbol from broker symbol in-memory cache."""
    if _brsymbol_exchange_to_symbol is None:
        return None
    return _brsymbol_exchange_to_symbol.get((brsymbol, exchange))


def map_order_data(order_data: dict) -> list[dict]:
    """Map Upstox order data, converting instrument tokens to symbols and product types."""
    if order_data.get("data") is None:
        logger.debug("No order data available to map.")
        return []

    data = order_data["data"]

    for order in data:
        instrument_token = order.get("instrument_token", "")
        exchange = order.get("exchange", "")

        # Look up human-readable symbol from in-memory cache
        symbol = _get_symbol_from_cache(instrument_token)
        if symbol:
            order["tradingsymbol"] = symbol

        # Map product types
        if (exchange in ("NSE", "BSE")) and order.get("product") == "D":
            order["product"] = "CNC"
        elif order.get("product") == "I":
            order["product"] = "MIS"
        elif exchange in ("NFO", "MCX", "BFO", "CDS") and order.get("product") == "D":
            order["product"] = "NRML"

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

        status = order.get("status", "")
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
    """Transform Upstox order data to OpenBull standard format."""
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders = []
    for order in orders:
        if not isinstance(order, dict):
            continue

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
            "order_status": order.get("status", ""),
            "timestamp": order.get("order_timestamp", ""),
        })

    return transformed_orders


def map_trade_data(trade_data: dict) -> list[dict]:
    """Map trade data (same logic as order data for Upstox)."""
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
    """Map Upstox position data."""
    return map_order_data(position_data)


def transform_positions_data(positions_data: list[dict]) -> list[dict]:
    """Transform positions data to OpenBull standard format."""
    transformed_data = []
    for position in positions_data:
        avg_price = position.get("average_price")
        quantity = position.get("quantity", 0)

        if avg_price is None or avg_price == 0:
            if quantity > 0:
                avg_price = position.get("buy_price", 0.0)
                if not avg_price:
                    avg_price = position.get("day_buy_price", 0.0)
            elif quantity < 0:
                avg_price = position.get("sell_price", 0.0)
                if not avg_price:
                    avg_price = position.get("day_sell_price", 0.0)
            else:
                avg_price = 0.0

        average_price = float(avg_price) if avg_price is not None else 0.0

        transformed_data.append({
            "symbol": position.get("tradingsymbol", ""),
            "exchange": position.get("exchange", ""),
            "product": position.get("product", ""),
            "quantity": position.get("quantity", 0),
            "average_price": average_price,
            "pnl": position.get("pnl", 0.0),
            "ltp": position.get("last_price", 0.0),
        })
    return transformed_data


def map_portfolio_data(portfolio_data: dict) -> list[dict]:
    """Map Upstox portfolio/holdings data."""
    if portfolio_data.get("data") is None:
        logger.debug("No portfolio data available to map.")
        return []

    data = portfolio_data["data"]
    for item in data:
        if item.get("product") == "D":
            item["product"] = "CNC"
    return data


def transform_holdings_data(holdings_data: list[dict]) -> list[dict]:
    """Transform holdings data to OpenBull standard format."""
    transformed_data = []
    for holding in holdings_data:
        avg_price = holding.get("average_price", 0.0)
        if avg_price and avg_price != 0:
            pnlpercent = (holding.get("last_price", 0) - avg_price) / avg_price * 100
        else:
            pnlpercent = 0.0

        transformed_data.append({
            "symbol": holding.get("tradingsymbol", ""),
            "exchange": holding.get("exchange", ""),
            "quantity": holding.get("quantity", 0),
            "product": holding.get("product", ""),
            "average_price": avg_price if avg_price else 0.0,
            "ltp": holding.get("last_price", 0.0),
            "pnl": holding.get("pnl", 0.0),
            "pnlpercent": round(pnlpercent, 2),
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
