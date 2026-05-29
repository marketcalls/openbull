"""
Dhan order data mapping - transforms broker response data to OpenBull standard format.
Adapted from OpenAlgo's dhan order_data.py.

Numeric type rules (frontend calls .toFixed(2)):
  - transform_positions_data: average_price (float), quantity (int), pnl (float), ltp (float)
  - transform_holdings_data: average_price (float), ltp (float), pnl (float), pnlpercent (float)
  - transform_tradebook_data: average_price (float), trade_value (float), quantity (int)
  - transform_order_data: price (float), trigger_price (float), quantity (int)
"""

import logging

from backend.broker.dhan.mapping.transform_data import map_exchange
from backend.broker.upstox.mapping.order_data import (
    get_symbol_from_brsymbol_cache,
    get_symbol_exchange_from_token,
)

logger = logging.getLogger(__name__)


def _resolve_symbol(security_id, exchange: str) -> str | None:
    """Look up OpenBull symbol from token in shared symtoken cache."""
    if security_id is None:
        return None
    token_str = str(security_id)
    info = get_symbol_exchange_from_token(token_str)
    if info:
        return info[0]
    return None


def map_order_data(order_data) -> list:
    """Map Dhan order data: enrich with tradingSymbol and normalize productType."""
    if order_data is None:
        logger.debug("No order data available to map.")
        return []

    if not isinstance(order_data, list):
        return []

    for order in order_data:
        instrument_token = order.get("securityId")
        exchange = map_exchange(order.get("exchangeSegment", ""))
        if exchange:
            order["exchangeSegment"] = exchange

        symbol_from_db = _resolve_symbol(instrument_token, exchange or "")
        if symbol_from_db:
            order["tradingSymbol"] = symbol_from_db

        product_type = order.get("productType")
        if (
            order.get("exchangeSegment") in ("NSE", "BSE")
            and product_type == "CNC"
        ):
            order["productType"] = "CNC"
        elif product_type == "INTRADAY":
            order["productType"] = "MIS"
        elif (
            order.get("exchangeSegment") in ("NFO", "MCX", "BFO", "CDS", "BCD")
            and product_type == "MARGIN"
        ):
            order["productType"] = "NRML"

    return order_data


def calculate_order_statistics(order_data: list) -> dict:
    """Calculate buy/sell/completed/open/rejected counts from order data.

    Also normalizes orderStatus values (TRADED->complete, etc) — matches openalgo.
    """
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    if order_data:
        for order in order_data:
            if order.get("transactionType") == "BUY":
                total_buy_orders += 1
            elif order.get("transactionType") == "SELL":
                total_sell_orders += 1

            status = order.get("orderStatus")
            if status == "TRADED":
                total_completed_orders += 1
                order["orderStatus"] = "complete"
            elif status == "PENDING":
                total_open_orders += 1
                order["orderStatus"] = "open"
            elif status == "REJECTED":
                total_rejected_orders += 1
                order["orderStatus"] = "rejected"
            elif status == "CANCELLED":
                order["orderStatus"] = "cancelled"

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def transform_order_data(orders) -> list[dict]:
    """Transform Dhan order data to OpenBull standard format.

    quantity -> int, price/trigger_price -> float (NEVER formatted strings).
    """
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders: list[dict] = []
    if not isinstance(orders, list):
        return transformed_orders

    for order in orders:
        if not isinstance(order, dict):
            logger.warning("Expected dict, got %s. Skipping.", type(order))
            continue

        order_type = order.get("orderType", "")
        if order_type == "STOP_LOSS":
            order_type = "SL"
        elif order_type == "STOP_LOSS_MARKET":
            order_type = "SL-M"

        transformed_orders.append({
            "symbol": order.get("tradingSymbol", ""),
            "exchange": order.get("exchangeSegment", ""),
            "action": order.get("transactionType", ""),
            "quantity": int(order.get("quantity") or 0),
            "price": float(order.get("price") or 0.0),
            "trigger_price": float(order.get("triggerPrice") or 0.0),
            "pricetype": order_type,
            "product": order.get("productType", ""),
            "orderid": order.get("orderId", ""),
            "order_status": order.get("orderStatus", ""),
            "timestamp": order.get("updateTime", ""),
        })

    return transformed_orders


def map_trade_data(trade_data) -> list:
    """Map trade data (same logic as order data for Dhan)."""
    return map_order_data(trade_data)


def transform_tradebook_data(tradebook_data: list) -> list[dict]:
    """Transform tradebook data to OpenBull standard format.

    quantity -> int, average_price/trade_value -> float.
    """
    transformed_data: list[dict] = []
    if not isinstance(tradebook_data, list):
        return transformed_data

    for trade in tradebook_data:
        qty = int(trade.get("tradedQuantity") or 0)
        avg_price = float(trade.get("tradedPrice") or 0.0)
        transformed_data.append({
            "symbol": trade.get("tradingSymbol", ""),
            "exchange": trade.get("exchangeSegment", ""),
            "product": trade.get("productType", ""),
            "action": trade.get("transactionType", ""),
            "quantity": qty,
            "average_price": avg_price,
            "trade_value": float(qty * avg_price),
            "orderid": trade.get("orderId", ""),
            "timestamp": trade.get("updateTime", ""),
        })
    return transformed_data


def map_position_data(position_data) -> list:
    """Map Dhan position data (same logic as order data)."""
    return map_order_data(position_data)


def transform_positions_data(positions_data: list) -> list[dict]:
    """Transform Dhan positions to OpenBull standard format.

    Dhan's /v2/positions does NOT include LTP. We compute pnl as
    realized + unrealized (matches openalgo). LTP is left as 0.0 here —
    callers that need LTP populated can join with quote data downstream.

    quantity -> int, average_price/pnl/ltp -> float.
    """
    transformed_data: list[dict] = []
    if not isinstance(positions_data, list):
        return transformed_data

    for position in positions_data:
        realized_pnl = float(position.get("realizedProfit") or 0.0)
        unrealized_pnl = float(position.get("unrealizedProfit") or 0.0)
        transformed_data.append({
            "symbol": position.get("tradingSymbol", ""),
            "exchange": position.get("exchangeSegment", ""),
            "product": position.get("productType", ""),
            "quantity": int(position.get("netQty") or 0),
            "average_price": float(position.get("costPrice") or 0.0),
            "ltp": float(position.get("lastTradedPrice") or position.get("ltp") or 0.0),
            "pnl": float(realized_pnl + unrealized_pnl),
        })
    return transformed_data


def transform_holdings_data(holdings_data: list) -> list[dict]:
    """Transform Dhan holdings to OpenBull standard format.

    Prefers the enriched fields written by map_portfolio_data (_oa_symbol,
    _exchange, _ltp) so the first paint shows the real listing exchange and a
    live LTP; falls back to the raw Dhan fields when enrichment is absent.
    quantity -> int, average_price/ltp/pnl/pnlpercent -> float.
    """
    transformed_data: list[dict] = []
    if not isinstance(holdings_data, list):
        return transformed_data

    for holdings in holdings_data:
        avg_price = float(holdings.get("avgCostPrice") or 0.0)
        qty = int(holdings.get("totalQty") or 0)

        # Enriched LTP first; fall back to any LTP Dhan happened to include.
        ltp = float(
            holdings.get("_ltp")
            or holdings.get("lastTradedPrice")
            or holdings.get("ltp")
            or 0.0
        )

        if ltp > 0 and avg_price > 0:
            pnl = (ltp - avg_price) * qty
            pnlpercent = ((ltp - avg_price) / avg_price) * 100
        else:
            pnl = 0.0
            pnlpercent = 0.0

        # Symbol/exchange: prefer values resolved from securityId in
        # map_portfolio_data (Dhan reports exchange="ALL" on /holdings).
        sym = holdings.get("_oa_symbol")
        exch = holdings.get("_exchange")
        if not sym:
            raw_sym = holdings.get("tradingSymbol", "")
            raw_exch = holdings.get("exchange") or holdings.get("exchangeSegment") or ""
            mapped_exch = map_exchange(raw_exch) or raw_exch
            sym = (
                get_symbol_from_brsymbol_cache(raw_sym, mapped_exch)
                if raw_sym and mapped_exch
                else None
            ) or raw_sym
            exch = mapped_exch

        transformed_data.append({
            "symbol": sym,
            "exchange": exch or "NSE",
            "quantity": qty,
            "product": "CNC",
            "average_price": avg_price,
            "ltp": ltp,
            "pnl": float(round(pnl, 2)),
            "pnlpercent": float(round(pnlpercent, 2)),
        })
    return transformed_data


def map_portfolio_data(portfolio_data, auth_token=None, broker=None, config=None):
    """Validate the Dhan /holdings response and enrich each row with the real
    listing exchange + live LTP so the first API paint is meaningful.

    Dhan returns exchange="ALL" for every holding (demat is exchange-agnostic)
    and /holdings carries no LTP. We resolve the OpenBull symbol + exchange from
    the broker-returned securityId via the shared symtoken cache, then -- when
    holdings_service supplies an auth context -- batch-fetch LTPs via the
    multiquote service. Writes three private fields consumed by
    calculate_portfolio_statistics and transform_holdings_data:
    _oa_symbol, _exchange, _ltp. The frontend useLivePrice hook keeps LTP/P&L
    updating over WebSocket after first paint.
    """
    if portfolio_data is None or (
        isinstance(portfolio_data, dict)
        and (
            portfolio_data.get("errorCode") == "DHOLDING_ERROR"
            or portfolio_data.get("internalErrorCode") == "DH-1111"
            or portfolio_data.get("internalErrorMessage") == "No holdings available"
        )
    ):
        logger.info("No holdings available.")
        return {}
    if not isinstance(portfolio_data, list):
        return {}

    # Resolve symbol + real exchange from securityId (exchange-scoped token).
    for h in portfolio_data:
        security_id = h.get("securityId")
        resolved_symbol = h.get("tradingSymbol", "")
        resolved_exchange = None
        if security_id is not None:
            info = get_symbol_exchange_from_token(str(security_id))
            if info:
                resolved_symbol, resolved_exchange = info[0], info[1]
        h["_oa_symbol"] = resolved_symbol
        h["_exchange"] = resolved_exchange or "NSE"
        h["_ltp"] = 0.0

    # Batch-fetch LTPs when an auth context is available.
    if auth_token and broker:
        try:
            from backend.services.quotes_service import get_multi_quotes_with_auth

            payload = [
                {"symbol": h["_oa_symbol"], "exchange": h["_exchange"]}
                for h in portfolio_data
                if h.get("_oa_symbol") and h.get("_exchange")
            ]
            if payload:
                ok, resp, _ = get_multi_quotes_with_auth(
                    symbols_list=payload, auth_token=auth_token, broker=broker, config=config
                )
                if ok and isinstance(resp, dict):
                    ltp_map: dict[str, float] = {}
                    for row in resp.get("results", []):
                        if not isinstance(row, dict):
                            continue
                        data = row.get("data", row)
                        ltp_map[f"{row.get('exchange')}:{row.get('symbol')}"] = float(
                            data.get("ltp", 0) or 0
                        )
                    for h in portfolio_data:
                        key = f"{h['_exchange']}:{h['_oa_symbol']}"
                        if key in ltp_map:
                            h["_ltp"] = ltp_map[key]
        except Exception as e:
            logger.warning("Failed to fetch holdings LTP via multiquotes: %s", e)

    return portfolio_data


def calculate_portfolio_statistics(holdings_data: list) -> dict:
    """Portfolio totals. Uses the enriched _ltp when present so holding value
    and P&L are live on first paint; falls back to avgCostPrice (zero P&L) until
    live LTP fills in."""
    if not isinstance(holdings_data, list):
        holdings_data = []

    totalinvvalue = sum(
        float(item.get("avgCostPrice") or 0) * int(item.get("totalQty") or 0)
        for item in holdings_data
    )
    totalholdingvalue = sum(
        (float(item.get("_ltp") or 0) or float(item.get("avgCostPrice") or 0))
        * int(item.get("totalQty") or 0)
        for item in holdings_data
    )
    totalprofitandloss = totalholdingvalue - totalinvvalue
    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0.0

    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }
