"""
Fyers order API - place, modify, cancel orders and fetch orderbook/tradebook/positions/holdings.
Adapted from OpenAlgo's fyers order_api.py.

The auth_token passed in here is the combined ``"api_key:access_token"`` string
produced by ``authenticate_broker``. Fyers's REST API expects exactly that
combined string in the ``Authorization`` header, so we pass it through.
"""

import json
import logging
import threading
import time

import httpx

from backend.broker.fyers.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def _build_headers(auth: str) -> dict:
    """Build Fyers Authorization header. ``auth`` already contains ``api_key:access_token``."""
    return {
        "Authorization": auth,
        "Content-Type": "application/json",
    }


def get_api_response(endpoint: str, auth: str, method: str = "GET", payload=None) -> dict:
    """Make a request to the Fyers API and return the parsed JSON response."""
    try:
        client = get_httpx_client()
        url = f"https://api-t1.fyers.in{endpoint}"
        headers = _build_headers(auth)

        if method == "GET":
            response = client.get(url, headers=headers)
        elif method == "POST":
            json_payload = payload if isinstance(payload, dict) else (
                json.loads(payload) if payload else None
            )
            response = client.post(url, headers=headers, json=json_payload)
        else:
            json_payload = payload if isinstance(payload, dict) else (
                json.loads(payload) if payload else None
            )
            response = client.request(method, url, headers=headers, json=json_payload)

        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error on %s: %s", endpoint, e.response.text)
        try:
            return e.response.json()
        except json.JSONDecodeError:
            return {"s": "error", "message": f"HTTP error: {e}"}
    except httpx.HTTPError as e:
        logger.error("HTTP error during API request: %s", e)
        return {"s": "error", "message": f"HTTP error: {e}"}
    except json.JSONDecodeError as e:
        logger.error("JSON decode error: %s", e)
        return {"s": "error", "message": f"Invalid JSON response: {e}"}
    except Exception as e:
        logger.exception("Error during Fyers API request")
        return {"s": "error", "message": f"General error: {e}"}


def get_order_book(auth: str) -> dict:
    """Fetch the order book."""
    return get_api_response("/api/v3/orders", auth)


def get_trade_book(auth: str) -> dict:
    """Fetch the trade book."""
    return get_api_response("/api/v3/tradebook", auth)


def get_positions(auth: str) -> dict:
    """Fetch open positions."""
    return get_api_response("/api/v3/positions", auth)


def get_holdings(auth: str) -> dict:
    """Fetch holdings."""
    return get_api_response("/api/v3/holdings", auth)


# --- Per-Symbol Smart Order Lock + Position Cache ---
_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_lock = threading.Lock()
_position_cache: dict[str, dict] = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_symbol_lock(symbol: str, exchange: str, product: str) -> threading.Lock:
    """Get or create a per-symbol lock for serializing smart orders."""
    key = f"{symbol}:{exchange}:{product}"
    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.Lock()
        return _symbol_locks[key]


def _get_cached_positions(auth: str) -> dict:
    """Return positions from cache if fresh, else fetch from broker."""
    with _position_cache_lock:
        now = time.monotonic()
        cached = _position_cache.get(auth)
        if cached and (now - cached["timestamp"]) < _POSITION_CACHE_TTL:
            return cached["data"]

    positions_data = get_positions(auth)
    with _position_cache_lock:
        _position_cache[auth] = {"data": positions_data, "timestamp": time.monotonic()}
    return positions_data


def _invalidate_position_cache(auth: str) -> None:
    """Drop cached positions so the next read fetches fresh data."""
    with _position_cache_lock:
        _position_cache.pop(auth, None)


def get_open_position(tradingsymbol, exchange, product, auth):
    """Get the net quantity of an open position for a given symbol."""
    br_symbol = get_brsymbol_from_cache(tradingsymbol, exchange) or tradingsymbol
    positions_data = _get_cached_positions(auth)
    net_qty = "0"

    if positions_data and positions_data.get("s") == "ok" and positions_data.get("netPositions"):
        for position in positions_data["netPositions"]:
            if position.get("symbol") == br_symbol and position.get("productType") == product:
                net_qty = position.get("netQty", "0")
                break
    return net_qty


def place_order_api(data: dict, auth: str) -> tuple:
    """Place a new order via Fyers API. Returns (response, response_data, order_id)."""
    try:
        client = get_httpx_client()
        url = "https://api-t1.fyers.in/api/v3/orders/sync"
        headers = _build_headers(auth)

        payload = transform_data(data)
        logger.debug("Placing Fyers order with payload: %s", json.dumps(payload))

        response = client.post(url, headers=headers, json=payload)
        response.status = response.status_code
        response_data = response.json()

        if response_data.get("s") == "ok":
            orderid = response_data.get("id")
            logger.info("Fyers order placed successfully. Order ID: %s", orderid)
            return response, response_data, orderid

        orderid = response_data.get("id") or None
        logger.warning("Fyers order placement failed: %s", response_data.get("message"))
        return response, response_data, orderid

    except httpx.HTTPError as e:
        logger.error("HTTP error during Fyers order placement: %s", e)
        return None, {"s": "error", "message": f"HTTP error: {e}"}, None
    except json.JSONDecodeError as e:
        logger.error("JSON decode error during Fyers order placement: %s", e)
        return None, {"s": "error", "message": f"Invalid JSON response: {e}"}, None
    except Exception as e:
        logger.exception("Error during Fyers order placement")
        return None, {"s": "error", "message": f"General error: {e}"}, None


def place_smartorder_api(data: dict, auth: str) -> tuple:
    """Place a smart order: compare desired position with current position and act."""
    res = None
    symbol = data.get("symbol")
    exchange = data.get("exchange")
    product = data.get("product")

    symbol_lock = _get_symbol_lock(symbol, exchange, product)
    with symbol_lock:
        try:
            position_size = int(data.get("position_size", "0") or 0)
            current_position = int(
                get_open_position(symbol, exchange, map_product_type(product), auth) or 0
            )

            logger.debug("position_size=%s, current_position=%s", position_size, current_position)

            if position_size == 0 and current_position == 0 and int(data.get("quantity", 0) or 0) != 0:
                res, response, orderid = place_order_api(data, auth)
                _invalidate_position_cache(auth)
                return res, response, orderid

            if position_size == current_position:
                if int(data.get("quantity", 0) or 0) == 0:
                    response = {
                        "status": "success",
                        "message": "No OpenPosition Found. Not placing Exit order.",
                    }
                else:
                    response = {
                        "status": "success",
                        "message": "No action needed. Position size matches current position",
                    }
                return res, response, None

            if position_size == 0 and current_position > 0:
                action, quantity = "SELL", abs(current_position)
            elif position_size == 0 and current_position < 0:
                action, quantity = "BUY", abs(current_position)
            elif current_position == 0:
                action = "BUY" if position_size > 0 else "SELL"
                quantity = abs(position_size)
            elif position_size > current_position:
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
            logger.exception("Error in Fyers place_smartorder_api")
            return None, {"status": "error", "message": str(e)}, None


def close_all_positions(current_api_key: str, auth: str) -> tuple[dict, int]:
    """Close all open positions via Fyers exit-all endpoint."""
    try:
        client = get_httpx_client()
        url = "https://api-t1.fyers.in/api/v3/positions"
        headers = _build_headers(auth)

        payload = {"exit_all": 1}
        response = client.request("DELETE", url, headers=headers, json=payload)
        response_data = response.json()

        if response_data.get("s") == "ok":
            return {"status": "success", "message": "All positions closed successfully"}, 200

        error_msg = response_data.get("message", "Failed to close positions")
        logger.warning("Failed to close all positions: %s", error_msg)
        return {"status": "error", "message": error_msg}, response.status_code

    except httpx.HTTPError as e:
        logger.exception("HTTP error during close all positions")
        return {"status": "error", "message": f"HTTP error: {e}"}, 500
    except json.JSONDecodeError as e:
        logger.exception("JSON decode error during close all positions")
        return {"status": "error", "message": f"JSON decode error: {e}"}, 500
    except Exception as e:
        logger.exception("Unexpected error during close all positions")
        return {"status": "error", "message": f"General error: {e}"}, 500


def cancel_order(orderid: str, auth: str) -> tuple[dict, int]:
    """Cancel a specific order by ID."""
    try:
        client = get_httpx_client()
        url = "https://api-t1.fyers.in/api/v3/orders/sync"
        headers = _build_headers(auth)

        payload = {"id": orderid}
        response = client.request("DELETE", url, headers=headers, json=payload)
        response_data = response.json()

        if response_data.get("s") == "ok":
            return {"status": "success", "orderid": response_data.get("id")}, 200

        error_msg = response_data.get("message", "Failed to cancel order")
        logger.warning("Failed to cancel order %s: %s", orderid, error_msg)
        return {"status": "error", "message": error_msg}, response.status_code

    except httpx.HTTPError as e:
        logger.exception("HTTP error during order cancellation")
        return {"status": "error", "message": f"HTTP error: {e}"}, 500
    except json.JSONDecodeError as e:
        logger.exception("JSON decode error during order cancellation")
        return {"status": "error", "message": f"JSON decode error: {e}"}, 500
    except Exception as e:
        logger.exception("Unexpected error during order cancellation")
        return {"status": "error", "message": f"General error: {e}"}, 500


def modify_order(data: dict, auth: str) -> tuple[dict, int]:
    """Modify an existing order."""
    try:
        client = get_httpx_client()
        url = "https://api-t1.fyers.in/api/v3/orders/sync"
        headers = _build_headers(auth)

        payload = transform_modify_order_data(data)
        response = client.patch(url, headers=headers, json=payload)
        response_data = response.json()

        if response_data.get("s") in ("ok", "OK"):
            return {"status": "success", "orderid": response_data.get("id")}, 200

        error_msg = response_data.get("message", "Failed to modify order")
        logger.warning("Failed to modify order: %s", error_msg)
        return {"status": "error", "message": error_msg}, response.status_code

    except httpx.HTTPError as e:
        logger.exception("HTTP error during order modification")
        return {"status": "error", "message": f"HTTP error: {e}"}, 500
    except json.JSONDecodeError as e:
        logger.exception("JSON decode error during order modification")
        return {"status": "error", "message": f"JSON decode error: {e}"}, 500
    except Exception as e:
        logger.exception("Unexpected error during order modification")
        return {"status": "error", "message": f"General error: {e}"}, 500


def cancel_all_orders_api(data: dict, auth: str) -> tuple[list, list]:
    """Cancel all open / trigger-pending orders. Returns (canceled_ids, failed_ids)."""
    try:
        order_book_response = get_order_book(auth)
        if order_book_response.get("s") != "ok":
            logger.error(
                "Could not fetch order book to cancel all orders: %s",
                order_book_response.get("message"),
            )
            return [], []

        orders_to_cancel = [
            order
            for order in order_book_response.get("orderBook", [])
            if order.get("status") in (4, 6)  # 4=trigger-pending, 6=open
        ]

        if not orders_to_cancel:
            logger.info("No open orders to cancel.")
            return [], []

        canceled_orders, failed_cancellations = [], []
        for order in orders_to_cancel:
            orderid = order.get("id")
            if not orderid:
                continue

            cancel_response, status_code = cancel_order(orderid, auth)
            if status_code == 200:
                canceled_orders.append(orderid)
            else:
                failed_cancellations.append(orderid)

        return canceled_orders, failed_cancellations

    except Exception:
        logger.exception("Error canceling all Fyers orders")
        return [], []
