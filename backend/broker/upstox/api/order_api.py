"""
Upstox order API - place, modify, cancel orders and fetch orderbook/tradebook/positions/holdings.
Adapted from OpenAlgo's upstox order_api.py. Key change: accepts config dict instead of os.getenv.
"""

import json
import logging
import threading
import time

import httpx

from backend.broker.upstox.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def get_api_response(endpoint: str, auth: str, method: str = "GET", payload: str = "") -> dict:
    """Send request to Upstox API and return JSON response."""
    logger.debug("Requesting %s on endpoint: %s", method, endpoint)
    try:
        client = get_httpx_client()
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"https://api.upstox.com{endpoint}"

        if method == "GET":
            response = client.get(url, headers=headers)
        elif method == "POST":
            response = client.post(url, headers=headers, content=payload)
        elif method == "PUT":
            response = client.put(url, headers=headers, content=payload)
        elif method == "DELETE":
            response = client.delete(url, headers=headers)
        else:
            return {"status": "error", "message": f"Unsupported HTTP method: {method}"}

        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as e:
        error_response = e.response.text
        logger.error("HTTP error on %s: %s", endpoint, error_response)
        try:
            return e.response.json()
        except json.JSONDecodeError:
            return {"status": "error", "message": f"HTTP error: {error_response}"}
    except Exception as e:
        logger.error("Unexpected error on %s: %s", endpoint, e)
        return {"status": "error", "message": str(e)}


def get_order_book(auth: str) -> dict:
    """Fetch the order book."""
    return get_api_response("/v2/order/retrieve-all", auth)


def get_trade_book(auth: str) -> dict:
    """Fetch the trade book."""
    return get_api_response("/v2/order/trades/get-trades-for-day", auth)


def get_positions(auth: str) -> dict:
    """Fetch short-term positions."""
    return get_api_response("/v2/portfolio/short-term-positions", auth)


def get_holdings(auth: str) -> dict:
    """Fetch long-term holdings."""
    return get_api_response("/v2/portfolio/long-term-holdings", auth)


# --- Per-Symbol Smart Order Lock ---
_symbol_locks = {}
_symbol_locks_lock = threading.Lock()
_position_cache = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_symbol_lock(symbol, exchange, product):
    key = f"{symbol}:{exchange}:{product}"
    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.Lock()
        return _symbol_locks[key]


def _get_cached_positions(auth):
    with _position_cache_lock:
        now = time.monotonic()
        cached = _position_cache.get(auth)
        if cached and (now - cached["timestamp"]) < _POSITION_CACHE_TTL:
            return cached["data"]

    positions_data = get_positions(auth)
    with _position_cache_lock:
        _position_cache[auth] = {"data": positions_data, "timestamp": time.monotonic()}
    return positions_data


def _invalidate_position_cache(auth):
    with _position_cache_lock:
        _position_cache.pop(auth, None)


def _run_async_query(query: str, params: dict):
    """Run an async DB query from a sync context using a dedicated engine."""
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    async def _query():
        from backend.config import get_settings
        engine = create_async_engine(get_settings().database_url, echo=False)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(text(query), params)
            row = result.fetchone()
        await engine.dispose()
        return row

    try:
        return asyncio.run(_query())
    except Exception:
        return None


def _get_br_symbol(symbol: str, exchange: str) -> str:
    """Look up broker symbol from symtoken table."""
    row = _run_async_query(
        "SELECT brsymbol FROM symtoken WHERE symbol = :symbol AND exchange = :exchange LIMIT 1",
        {"symbol": symbol, "exchange": exchange},
    )
    return row[0] if row else symbol


def _get_token(symbol: str, exchange: str) -> str | None:
    """Look up instrument token from symtoken table."""
    row = _run_async_query(
        "SELECT token FROM symtoken WHERE symbol = :symbol AND exchange = :exchange LIMIT 1",
        {"symbol": symbol, "exchange": exchange},
    )
    return row[0] if row else None


def _get_symbol(token: str, exchange: str) -> str | None:
    """Look up OpenBull symbol from instrument token."""
    row = _run_async_query(
        "SELECT symbol FROM symtoken WHERE token = :token AND exchange = :exchange LIMIT 1",
        {"token": token, "exchange": exchange},
    )
    return row[0] if row else None


def get_open_position(tradingsymbol, exchange, product, auth):
    """Get the net quantity of an open position for a given symbol."""
    try:
        br_symbol = _get_br_symbol(tradingsymbol, exchange)
        positions_data = _get_cached_positions(auth)
        net_qty = "0"

        if (
            positions_data
            and positions_data.get("status") == "success"
            and positions_data.get("data")
        ):
            for position in positions_data["data"]:
                if (
                    position.get("tradingsymbol") == br_symbol
                    and position.get("exchange") == exchange
                    and position.get("product") == product
                ):
                    net_qty = position.get("quantity", "0")
                    break

        return net_qty
    except Exception:
        logger.error("Error getting open position for %s", tradingsymbol)
        return "0"


def place_order_api(data: dict, auth: str) -> tuple:
    """Place an order via Upstox API. Returns (response, response_data, order_id)."""
    logger.info("Placing order with data: %s", data)
    try:
        token = _get_token(data["symbol"], data["exchange"])
        if not token:
            return None, {"status": "error", "message": "Instrument token not found"}, None

        newdata = transform_data(data, token)
        payload = json.dumps({
            "quantity": newdata["quantity"],
            "product": newdata.get("product", "I"),
            "validity": newdata.get("validity", "DAY"),
            "price": newdata.get("price", "0"),
            "tag": newdata.get("tag", "string"),
            "instrument_token": newdata["instrument_token"],
            "order_type": newdata.get("order_type", "MARKET"),
            "transaction_type": newdata["transaction_type"],
            "disclosed_quantity": newdata.get("disclosed_quantity", "0"),
            "trigger_price": newdata.get("trigger_price", "0"),
            "is_amo": newdata.get("is_amo", False),
        })

        client = get_httpx_client()
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        response = client.post(
            "https://api.upstox.com/v2/order/place", headers=headers, content=payload
        )
        response.raise_for_status()
        response.status = response.status_code

        response_data = response.json()
        if response_data.get("status") == "success":
            order_id = response_data.get("data", {}).get("order_id")
            return response, response_data, order_id
        else:
            return response, response_data, None

    except httpx.HTTPStatusError as e:
        return e.response, e.response.json(), None
    except Exception as e:
        logger.error("Unexpected error in place_order_api: %s", e)
        return None, {"status": "error", "message": str(e)}, None


def place_smartorder_api(data: dict, auth: str) -> tuple:
    """Place a smart order by comparing desired position with current position."""
    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")
        symbol_lock = _get_symbol_lock(symbol, exchange, product)

        with symbol_lock:
            position_size = int(data.get("position_size", "0"))
            current_position = int(get_open_position(symbol, exchange, map_product_type(product), auth))

            if position_size == 0 and current_position == 0 and int(data.get("quantity", 0)) != 0:
                return place_order_api(data, auth)

            if position_size == current_position:
                msg = "No action needed. Position size matches current position."
                if int(data.get("quantity", 0)) == 0:
                    msg = "No open position found. Not placing exit order."
                return None, {"status": "success", "message": msg}, None

            if position_size > current_position:
                action, quantity = "BUY", position_size - current_position
            else:
                action, quantity = "SELL", current_position - position_size

            order_data = data.copy()
            order_data["action"] = action
            order_data["quantity"] = str(quantity)

            res, response, orderid = place_order_api(order_data, auth)
            _invalidate_position_cache(auth)
            return res, response, orderid

    except Exception as e:
        logger.error("Unexpected error in place_smartorder_api: %s", e)
        return None, {"status": "error", "message": str(e)}, None


def cancel_order(orderid: str, auth: str) -> tuple[dict, int]:
    """Cancel a specific order by its ID."""
    try:
        response_data = get_api_response(
            f"/v2/order/cancel?order_id={orderid}", auth, method="DELETE"
        )
        if response_data.get("status") == "success":
            canceled_id = response_data.get("data", {}).get("order_id")
            return {"status": "success", "orderid": canceled_id}, 200
        else:
            return {"status": "error", "message": response_data.get("message", "Failed to cancel order")}, 400

    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


def modify_order(data: dict, auth: str) -> tuple[dict, int]:
    """Modify an existing order."""
    try:
        transformed = transform_modify_order_data(data)
        payload = json.dumps(transformed)
        response_data = get_api_response("/v2/order/modify", auth, method="PUT", payload=payload)

        if response_data.get("status") == "success":
            modified_id = response_data.get("data", {}).get("order_id")
            return {"status": "success", "orderid": modified_id}, 200
        else:
            return {"status": "error", "message": response_data.get("message", "Failed to modify order")}, 400

    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


def cancel_all_orders_api(data: dict, auth: str) -> tuple[list, list]:
    """Cancel all open and trigger-pending orders."""
    try:
        order_book_response = get_order_book(auth)
        if order_book_response.get("status") != "success":
            return [], []

        orders_to_cancel = [
            order for order in order_book_response.get("data", [])
            if order.get("status") in ["open", "trigger pending"]
        ]

        if not orders_to_cancel:
            return [], []

        canceled_orders, failed_cancellations = [], []
        for order in orders_to_cancel:
            orderid = order["order_id"]
            cancel_response, status_code = cancel_order(orderid, auth)
            if status_code == 200:
                canceled_orders.append(orderid)
            else:
                failed_cancellations.append(orderid)

        return canceled_orders, failed_cancellations

    except Exception:
        logger.error("Error canceling all orders")
        return [], []


def close_all_positions(current_api_key: str, auth: str) -> tuple[dict, int]:
    """Close all open positions."""
    try:
        positions_response = get_positions(auth)
        if positions_response.get("status") != "success" or not positions_response.get("data"):
            return {"message": "No Open Positions Found"}, 200

        for position in positions_response["data"]:
            if int(position.get("quantity", 0)) == 0:
                continue

            action = "SELL" if int(position["quantity"]) > 0 else "BUY"
            quantity = abs(int(position["quantity"]))
            symbol = _get_symbol(position["instrument_token"], position["exchange"])

            if not symbol:
                continue

            place_order_payload = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": position["exchange"],
                "pricetype": "MARKET",
                "product": reverse_map_product_type(position["exchange"], position["product"]),
                "quantity": str(quantity),
            }
            place_order_api(place_order_payload, auth)

        return {"status": "success", "message": "All Open Positions SquaredOff"}, 200

    except Exception:
        logger.error("Error closing all positions")
        return {"status": "error", "message": "Failed to close all positions"}, 500
