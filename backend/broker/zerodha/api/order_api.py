"""
Zerodha order API - place, modify, cancel orders and fetch orderbook/tradebook/positions/holdings.
Adapted from OpenAlgo's zerodha order_api.py. Key change: accepts config dict instead of os.getenv.
"""

import logging
import threading
import time
import urllib.parse

from backend.broker.zerodha.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def get_api_response(endpoint: str, auth: str, method: str = "GET", payload=None) -> dict:
    """Make an API request to Zerodha's Kite API."""
    base_url = "https://api.kite.trade"
    client = get_httpx_client()
    headers = {"X-Kite-Version": "3", "Authorization": f"token {auth}"}
    url = f"{base_url}{endpoint}"

    try:
        if method.upper() == "GET":
            response = client.get(url, headers=headers)
        elif method.upper() == "POST":
            if isinstance(payload, str):
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                response = client.post(url, headers=headers, content=payload)
            else:
                headers["Content-Type"] = "application/json"
                response = client.post(url, headers=headers, json=payload)
        elif method.upper() == "PUT":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = client.put(url, headers=headers, content=payload)
        elif method.upper() == "DELETE":
            response = client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        return response.json()

    except Exception as e:
        error_msg = str(e)
        try:
            if hasattr(e, "response") and e.response is not None:
                error_detail = e.response.json()
                error_msg = error_detail.get("message", error_msg)
        except Exception:
            pass
        logger.error("Zerodha API request failed: %s", error_msg)
        raise


def get_order_book(auth: str) -> dict:
    """Fetch the order book."""
    return get_api_response("/orders", auth)


def get_trade_book(auth: str) -> dict:
    """Fetch the trade book."""
    return get_api_response("/trades", auth)


def get_positions(auth: str) -> dict:
    """Fetch positions."""
    return get_api_response("/portfolio/positions", auth)


def get_holdings(auth: str) -> dict:
    """Fetch holdings."""
    return get_api_response("/portfolio/holdings", auth)


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


def _get_br_symbol(symbol: str, exchange: str) -> str:
    """Look up broker symbol from symtoken table."""
    from sqlalchemy import create_engine, text as sa_text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("postgresql://postgres:123456@localhost:5432/openbull")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        result = session.execute(
            sa_text("SELECT brsymbol FROM symtoken WHERE symbol = :symbol AND exchange = :exchange LIMIT 1"),
            {"symbol": symbol, "exchange": exchange},
        )
        row = result.fetchone()
        return row[0] if row else symbol
    finally:
        session.close()
        engine.dispose()


def _get_oa_symbol(brsymbol: str, exchange: str) -> str:
    """Look up OpenBull symbol from broker symbol."""
    from sqlalchemy import create_engine, text as sa_text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("postgresql://postgres:123456@localhost:5432/openbull")
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        result = session.execute(
            sa_text("SELECT symbol FROM symtoken WHERE brsymbol = :brsymbol AND exchange = :exchange LIMIT 1"),
            {"brsymbol": brsymbol, "exchange": exchange},
        )
        row = result.fetchone()
        return row[0] if row else brsymbol
    finally:
        session.close()
        engine.dispose()


def get_open_position(tradingsymbol, exchange, product, auth):
    """Get the net quantity of an open position."""
    tradingsymbol = _get_br_symbol(tradingsymbol, exchange)
    positions_data = _get_cached_positions(auth)
    net_qty = "0"

    if positions_data and positions_data.get("status") and positions_data.get("data"):
        for position in positions_data["data"]["net"]:
            if (
                position.get("tradingsymbol") == tradingsymbol
                and position.get("exchange") == exchange
                and position.get("product") == product
            ):
                net_qty = position.get("quantity", "0")
                break

    return net_qty


def place_order_api(data: dict, auth: str) -> tuple:
    """Place an order via Zerodha Kite API. Returns (response, response_data, order_id)."""
    newdata = transform_data(data)

    payload = {
        "tradingsymbol": newdata["tradingsymbol"],
        "exchange": newdata["exchange"],
        "transaction_type": newdata["transaction_type"],
        "order_type": newdata["order_type"],
        "quantity": newdata["quantity"],
        "product": newdata["product"],
        "price": newdata["price"],
        "trigger_price": newdata["trigger_price"],
        "disclosed_quantity": newdata["disclosed_quantity"],
        "validity": newdata["validity"],
        "market_protection": newdata["market_protection"],
        "tag": newdata["tag"],
    }

    payload_encoded = urllib.parse.urlencode(payload)

    client = get_httpx_client()
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    response = client.post(
        "https://api.kite.trade/orders/regular", headers=headers, content=payload_encoded
    )

    response_data = response.json()
    response.status = response.status_code

    if response_data.get("status") == "success":
        orderid = response_data["data"]["order_id"]
    else:
        orderid = None

    return response, response_data, orderid


def place_smartorder_api(data: dict, auth: str) -> tuple:
    """Place a smart order by comparing desired position with current position."""
    res = None
    response_data = {"status": "error", "message": "No action required or invalid parameters"}
    orderid = None

    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")

        if not all([symbol, exchange, product]):
            return res, response_data, orderid

        symbol_lock = _get_symbol_lock(symbol, exchange, product)

        with symbol_lock:
            position_size = int(data.get("position_size", "0"))
            current_position = int(
                get_open_position(symbol, exchange, map_product_type(product), auth)
            )

            action = None
            quantity = 0

            if position_size == 0 and current_position == 0:
                action = data.get("action", "BUY").upper()
                quantity = int(data.get("quantity", "0"))
            elif position_size == 0 and current_position > 0:
                action = "SELL"
                quantity = abs(current_position)
            elif position_size == 0 and current_position < 0:
                action = "BUY"
                quantity = abs(current_position)
            elif current_position == 0:
                action = "BUY" if position_size > 0 else "SELL"
                quantity = abs(position_size)
            else:
                if position_size > current_position:
                    action = "BUY"
                    quantity = position_size - current_position
                elif position_size < current_position:
                    action = "SELL"
                    quantity = current_position - position_size

            if action and quantity > 0:
                order_data = data.copy()
                order_data["action"] = action
                order_data["quantity"] = str(quantity)

                res, response, orderid = place_order_api(order_data, auth)
                _invalidate_position_cache(auth)
                return res, response, orderid
            else:
                response_data = {"status": "success", "message": "No action needed. Position already matched."}
                return res, response_data, orderid

    except Exception as e:
        logger.error("Error in place_smartorder_api: %s", e)
        return res, {"status": "error", "message": str(e)}, orderid

    return res, response_data, orderid


def cancel_order(orderid: str, auth: str) -> tuple[dict, int]:
    """Cancel a specific order."""
    try:
        client = get_httpx_client()
        headers = {"X-Kite-Version": "3", "Authorization": f"token {auth}"}
        response = client.delete(
            f"https://api.kite.trade/orders/regular/{orderid}", headers=headers
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status"):
            return {"status": "success", "orderid": data["data"]["order_id"]}, 200
        else:
            return {"status": "error", "message": data.get("message", "Failed to cancel order")}, response.status_code

    except Exception as e:
        logger.error("Error canceling order %s: %s", orderid, e)
        return {"status": "error", "message": f"Failed to cancel order: {e}"}, 500


def modify_order(data: dict, auth: str) -> tuple[dict, int]:
    """Modify an existing order."""
    newdata = transform_modify_order_data(data)

    payload = {
        "order_type": newdata["order_type"],
        "quantity": str(newdata["quantity"]),
        "price": str(newdata["price"]) if newdata["price"] else "0",
        "disclosed_quantity": str(newdata["disclosed_quantity"]) if newdata["disclosed_quantity"] else "0",
        "validity": newdata["validity"],
    }
    if newdata.get("trigger_price"):
        payload["trigger_price"] = str(newdata["trigger_price"])

    payload_encoded = urllib.parse.urlencode(payload)

    client = get_httpx_client()
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    response = client.put(
        f"https://api.kite.trade/orders/regular/{data['orderid']}",
        headers=headers,
        content=payload_encoded,
    )
    response_data = response.json()
    response.status = response.status_code

    if response_data.get("status") == "success" or response_data.get("message") == "SUCCESS":
        return {"status": "success", "orderid": response_data["data"]["order_id"]}, 200
    else:
        return {"status": "error", "message": response_data.get("message", "Failed to modify order")}, response.status_code


def cancel_all_orders_api(data: dict, auth: str) -> tuple[list, list]:
    """Cancel all open and trigger-pending orders."""
    order_book_response = get_order_book(auth)
    if order_book_response.get("status") != "success":
        return [], []

    orders_to_cancel = [
        order for order in order_book_response.get("data", [])
        if order.get("status") in ["OPEN", "TRIGGER PENDING"]
    ]

    canceled_orders = []
    failed_cancellations = []
    for order in orders_to_cancel:
        orderid = order["order_id"]
        cancel_response, status_code = cancel_order(orderid, auth)
        if status_code == 200:
            canceled_orders.append(orderid)
        else:
            failed_cancellations.append(orderid)

    return canceled_orders, failed_cancellations


def close_all_positions(current_api_key: str, auth: str) -> tuple[dict, int]:
    """Close all open positions."""
    positions_response = get_positions(auth)

    if positions_response.get("data") is None or not positions_response.get("data"):
        return {"message": "No Open Positions Found"}, 200

    if positions_response.get("status"):
        for position in positions_response["data"]["net"]:
            if int(position["quantity"]) == 0:
                continue

            action = "SELL" if int(position["quantity"]) > 0 else "BUY"
            quantity = abs(int(position["quantity"]))
            symbol = _get_oa_symbol(position["tradingsymbol"], position["exchange"])

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
