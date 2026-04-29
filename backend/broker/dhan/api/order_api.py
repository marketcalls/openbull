"""
Dhan order API - place, modify, cancel orders and fetch orderbook/tradebook/positions/holdings.
Adapted from OpenAlgo's dhan order_api.py.
"""

import json
import logging
import threading
import time

from backend.broker.dhan.mapping.transform_data import (
    map_exchange,
    map_exchange_type,
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_symbol_exchange_from_token,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

DHAN_BASE_URL = "https://api.dhan.co"


def _get_url(endpoint: str) -> str:
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return DHAN_BASE_URL + endpoint


def _client_id_from_config(config: dict | None, auth: str | None = None) -> str | None:
    """Extract Dhan client_id from config or auth_token if it embeds one.

    Dhan's auth tokens are JWTs; the client_id is normally provided via config
    {'client_id': ...} (or the legacy api_key:::client_id form).
    """
    if not config:
        return None
    cid = config.get("client_id") or config.get("dhan_client_id")
    if cid:
        return str(cid)
    api_key = config.get("api_key") or ""
    if ":::" in api_key:
        head, _, _ = api_key.partition(":::")
        return head or None
    return None


def get_api_response(endpoint: str, auth: str, method: str = "GET", payload: str = "") -> dict:
    """Call Dhan API; returns parsed JSON or an error dict {errorType,...}."""
    try:
        client = get_httpx_client()
        headers = {
            "access-token": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = _get_url(endpoint)

        if method == "GET":
            response = client.get(url, headers=headers)
        elif method == "POST":
            response = client.post(url, headers=headers, content=payload)
        elif method == "PUT":
            response = client.put(url, headers=headers, content=payload)
        elif method == "DELETE":
            response = client.delete(url, headers=headers)
        else:
            response = client.request(method, url, headers=headers, content=payload)

        try:
            response_data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return {
                "errorType": "ParseError",
                "errorMessage": f"Invalid JSON response (HTTP {response.status_code})",
            }

        if isinstance(response_data, dict):
            if response_data.get("status") in ("failed", "error"):
                error_data = response_data.get("data", {}) or {}
                if error_data:
                    error_code = list(error_data.keys())[0] if error_data else "unknown"
                    error_message = error_data.get(error_code, "Unknown error")
                    logger.error("Dhan API Error: %s - %s", error_code, error_message)
            if response_data.get("errorType"):
                logger.error(
                    "Dhan API Error: %s - %s",
                    response_data.get("errorCode"),
                    response_data.get("errorMessage"),
                )

        return response_data

    except Exception as e:
        logger.exception("Error in Dhan API request to %s", endpoint)
        return {"errorType": "ConnectionError", "errorMessage": str(e)}


def get_order_book(auth: str) -> dict:
    """Fetch the order book."""
    return get_api_response("/v2/orders", auth)


def get_trade_book(auth: str) -> dict:
    """Fetch the trade book."""
    return get_api_response("/v2/trades", auth)


def get_positions(auth: str) -> dict:
    """Fetch positions."""
    return get_api_response("/v2/positions", auth)


def get_holdings(auth: str) -> dict:
    """Fetch holdings."""
    return get_api_response("/v2/holdings", auth)


# --- Per-Symbol Smart Order Lock + Position Cache ---
_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_lock = threading.Lock()
_position_cache: dict[str, dict] = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_symbol_lock(symbol: str, exchange: str, product: str) -> threading.Lock:
    key = f"{symbol}:{exchange}:{product}"
    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.Lock()
        return _symbol_locks[key]


def _get_cached_positions(auth: str):
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
    with _position_cache_lock:
        _position_cache.pop(auth, None)


def get_open_position(tradingsymbol: str, exchange: str, product: str, auth: str) -> str:
    """Get net quantity of an open position. Returns '0' if no match."""
    br_symbol = get_brsymbol_from_cache(tradingsymbol, exchange) or tradingsymbol
    positions_data = _get_cached_positions(auth)
    net_qty = "0"

    if isinstance(positions_data, dict) and (
        positions_data.get("errorType")
        or positions_data.get("status") in ("failed", "error")
    ):
        logger.error(
            "Error getting positions for %s: %s",
            tradingsymbol, positions_data.get("errorMessage", "API Error"),
        )
        return net_qty

    if isinstance(positions_data, list):
        for position in positions_data:
            if (
                position.get("tradingSymbol") == br_symbol
                and position.get("exchangeSegment") == map_exchange_type(exchange)
                and position.get("productType") == product
            ):
                net_qty = str(position.get("netQty", "0"))
                break

    return net_qty


def place_order_api(data: dict, auth: str) -> tuple:
    """Place an order. Returns (response, response_data, order_id)."""
    try:
        config = data.get("_broker_config")
        client_id = _client_id_from_config(config) or data.get("dhan_client_id")
        if client_id:
            data["dhan_client_id"] = client_id

        token = get_token_from_cache(data["symbol"], data["exchange"])
        if not token:
            return None, {"status": "error", "message": "Instrument token not found"}, None

        # Strip composite token suffix if present (Zerodha-style "tok::::extok").
        token_str = str(token).split("::::")[0] if "::::" in str(token) else str(token)
        newdata = transform_data(data, token_str)

        headers = {
            "access-token": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client_id:
            headers["client-id"] = client_id

        payload = json.dumps(newdata)
        client = get_httpx_client()
        response = client.post(_get_url("/v2/orders"), headers=headers, content=payload)
        response.status = response.status_code

        try:
            response_data = json.loads(response.text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
            return response, {"error": "Invalid JSON response"}, None

        orderid = None
        if response.status_code in (200, 201) and isinstance(response_data, dict):
            orderid = response_data.get("orderId")
            if not orderid:
                logger.error("orderId not found in response: %s", response_data)
        else:
            logger.error("Place order failed (HTTP %d): %s", response.status_code, response_data)

        return response, response_data, orderid

    except Exception as e:
        logger.exception("Error placing Dhan order")
        return None, {"status": "error", "message": str(e)}, None


def place_smartorder_api(data: dict, auth: str) -> tuple:
    """Place a smart order by reconciling target position size with current."""
    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")
        symbol_lock = _get_symbol_lock(symbol, exchange, product)

        with symbol_lock:
            position_size = int(data.get("position_size", "0"))
            current_position = int(
                get_open_position(symbol, exchange, map_product_type(product), auth)
            )

            logger.info("position_size=%s, current=%s", position_size, current_position)

            if position_size == 0 and current_position == 0 and int(data.get("quantity", 0)) != 0:
                res, response, orderid = place_order_api(data, auth)
                _invalidate_position_cache(auth)
                return res, response, orderid

            if position_size == current_position:
                msg = "No action needed. Position size matches current position"
                if int(data.get("quantity", 0)) == 0:
                    msg = "No OpenPosition Found. Not placing Exit order."
                return None, {"status": "success", "message": msg}, None

            if position_size == 0 and current_position > 0:
                action, quantity = "SELL", abs(current_position)
            elif position_size == 0 and current_position < 0:
                action, quantity = "BUY", abs(current_position)
            elif current_position == 0:
                action = "BUY" if position_size > 0 else "SELL"
                quantity = abs(position_size)
            else:
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
        logger.exception("Error in place_smartorder_api")
        return None, {"status": "error", "message": str(e)}, None


def close_all_positions(current_api_key: str, auth: str) -> tuple[dict, int]:
    """Close all open positions by issuing market orders for the inverse qty."""
    positions_response = get_positions(auth)
    if positions_response is None or not positions_response:
        return {"message": "No Open Positions Found"}, 200

    if isinstance(positions_response, dict) and (
        positions_response.get("errorType")
        or positions_response.get("status") in ("failed", "error")
    ):
        return {"message": "No Open Positions Found"}, 200

    if not isinstance(positions_response, list):
        return {"message": "No Open Positions Found"}, 200

    for position in positions_response:
        try:
            net_qty = int(position.get("netQty", 0))
        except (TypeError, ValueError):
            continue
        if net_qty == 0:
            continue

        action = "SELL" if net_qty > 0 else "BUY"
        quantity = abs(net_qty)

        exchange = map_exchange(position.get("exchangeSegment", ""))
        security_id = position.get("securityId")
        symbol = None
        if security_id and exchange:
            info = get_symbol_exchange_from_token(str(security_id))
            if info:
                symbol = info[0]
        if not symbol:
            logger.warning("Could not resolve symbol for position %s", position)
            continue

        place_order_payload = {
            "apikey": current_api_key,
            "strategy": "Squareoff",
            "symbol": symbol,
            "action": action,
            "exchange": exchange,
            "pricetype": "MARKET",
            "product": reverse_map_product_type(position.get("productType", "")),
            "quantity": str(quantity),
        }

        place_order_api(place_order_payload, auth)

    return {"status": "success", "message": "All Open Positions SquaredOff"}, 200


def cancel_order(orderid: str, auth: str) -> tuple[dict, int]:
    """Cancel an order by ID."""
    try:
        headers = {
            "access-token": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        client = get_httpx_client()
        url = _get_url(f"/v2/orders/{orderid}")
        response = client.delete(url, headers=headers)
        response.status = response.status_code

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            data = {}

        if data:
            return {"status": "success", "orderid": orderid}, 200
        return {
            "status": "error",
            "message": data.get("message", "Failed to cancel order") if isinstance(data, dict) else "Failed to cancel order",
        }, response.status_code

    except Exception as e:
        logger.exception("Error cancelling Dhan order %s", orderid)
        return {"status": "error", "message": str(e)}, 500


def modify_order(data: dict, auth: str) -> tuple[dict, int]:
    """Modify an existing order."""
    try:
        config = data.get("_broker_config")
        client_id = _client_id_from_config(config) or data.get("dhan_client_id")
        if client_id:
            data["dhan_client_id"] = client_id

        orderid = data["orderid"]
        transformed_order_data = transform_modify_order_data(data)

        headers = {
            "access-token": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client_id:
            headers["client-id"] = client_id
        payload = json.dumps(transformed_order_data)

        client = get_httpx_client()
        response = client.put(_get_url(f"/v2/orders/{orderid}"), headers=headers, content=payload)
        response.status = response.status_code

        try:
            response_data = json.loads(response.text)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Invalid JSON response"}, response.status_code

        if isinstance(response_data, dict) and response_data.get("orderId"):
            return {"status": "success", "orderid": response_data["orderId"]}, 200
        return {
            "status": "error",
            "message": response_data.get("message", "Failed to modify order")
            if isinstance(response_data, dict)
            else "Failed to modify order",
        }, response.status_code

    except Exception as e:
        logger.exception("Error modifying Dhan order")
        return {"status": "error", "message": str(e)}, 500


def cancel_all_orders_api(data: dict, auth: str) -> tuple[list, list]:
    """Cancel all PENDING orders."""
    order_book_response = get_order_book(auth)
    if order_book_response is None:
        return [], []

    if isinstance(order_book_response, dict) and (
        order_book_response.get("errorType")
        or order_book_response.get("status") in ("failed", "error")
    ):
        return [], []

    if not isinstance(order_book_response, list):
        return [], []

    orders_to_cancel = [
        order for order in order_book_response
        if order.get("orderStatus") in ("PENDING",)
    ]

    canceled_orders: list[str] = []
    failed_cancellations: list[str] = []
    for order in orders_to_cancel:
        orderid = order.get("orderId")
        if not orderid:
            continue
        _, status_code = cancel_order(orderid, auth)
        if status_code == 200:
            canceled_orders.append(orderid)
        else:
            failed_cancellations.append(orderid)

    return canceled_orders, failed_cancellations
