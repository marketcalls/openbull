"""
Angel One order API — place / modify / cancel orders, fetch
orderbook / tradebook / positions / holdings.
Adapted from OpenAlgo's angel order_api.py. Key changes:
  - uses backend.utils.httpx_client (sync httpx)
  - splits the combined ``api_key:jwt_token:feed_token`` auth_token issued by
    angel.api.auth_api
  - resolves symbol tokens via the shared in-memory symbol cache (upstox)
"""

import json
import logging
import threading
import time

from backend.broker.angel.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_symbol_exchange_from_token,
    get_symbol_from_brsymbol_cache,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


# ---------- token / symbol helpers ----------

def _split_token(auth_token: str) -> tuple[str, str]:
    """Split the combined ``api_key:jwt_token:feed_token`` token.

    Falls back to using the whole string as the JWT if it isn't combined.
    """
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", auth_token or ""


def _angel_headers(api_key: str, jwt_token: str) -> dict:
    return {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "CLIENT_LOCAL_IP",
        "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
        "X-MACAddress": "MAC_ADDRESS",
        "X-PrivateKey": api_key,
    }


def _get_token(symbol: str, exchange: str) -> str | None:
    return get_token_from_cache(symbol, exchange)


def _get_br_symbol(symbol: str, exchange: str) -> str:
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def _get_oa_symbol(brsymbol: str, exchange: str) -> str:
    return get_symbol_from_brsymbol_cache(brsymbol, exchange) or brsymbol


def _get_oa_symbol_from_token(token: str) -> str | None:
    info = get_symbol_exchange_from_token(token)
    if info:
        return info[0]
    return None


# ---------- low-level API helper ----------

def get_api_response(
    endpoint: str,
    auth: str,
    method: str = "GET",
    payload: str = "",
    max_retries: int = 2,
) -> dict:
    """Call Angel REST API with retry on rate-limit / empty responses."""
    api_key, jwt_token = _split_token(auth)
    headers = _angel_headers(api_key, jwt_token)
    url = f"https://apiconnect.angelone.in{endpoint}"

    client = get_httpx_client()

    for attempt in range(max_retries + 1):
        try:
            if method == "GET":
                response = client.get(url, headers=headers)
            elif method == "POST":
                response = client.post(url, headers=headers, content=payload)
            else:
                response = client.request(method, url, headers=headers, content=payload)
        except Exception as e:
            logger.error("HTTP request failed for %s: %s", endpoint, e)
            if attempt < max_retries:
                time.sleep(1)
                continue
            return {"status": "error", "message": str(e)}

        if not response.text:
            logger.error("Empty response from %s (HTTP %s)", endpoint, response.status_code)
            if attempt < max_retries:
                time.sleep(1)
                continue
            return {
                "status": "error",
                "message": f"Empty response (HTTP {response.status_code})",
            }

        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            # Angel rate-limit responses come back as plain text.
            if "exceeding access rate" in response.text.lower() and attempt < max_retries:
                logger.warning(
                    "Rate limited on %s, retrying in 1s (attempt %d/%d)",
                    endpoint, attempt + 1, max_retries,
                )
                time.sleep(1)
                continue
            logger.error(
                "Failed to parse JSON response from %s: %s", endpoint, response.text
            )
            return {
                "status": "error",
                "message": f"Invalid JSON response (HTTP {response.status_code})",
            }

    return {"status": "error", "message": "Max retries exceeded"}


def get_order_book(auth_token: str) -> dict:
    return get_api_response(
        "/rest/secure/angelbroking/order/v1/getOrderBook", auth_token
    )


def get_trade_book(auth_token: str) -> dict:
    return get_api_response(
        "/rest/secure/angelbroking/order/v1/getTradeBook", auth_token
    )


def get_positions(auth_token: str) -> dict:
    return get_api_response(
        "/rest/secure/angelbroking/order/v1/getPosition", auth_token
    )


def get_holdings(auth_token: str) -> dict:
    return get_api_response(
        "/rest/secure/angelbroking/portfolio/v1/getAllHolding", auth_token
    )


# ---------- per-symbol smart order lock ----------

_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_lock = threading.Lock()
_position_cache: dict = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_symbol_lock(symbol: str, exchange: str, product: str) -> threading.Lock:
    key = f"{symbol}:{exchange}:{product}"
    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = threading.Lock()
        return _symbol_locks[key]


def _get_cached_positions(auth: str) -> dict:
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


# ---------- public order API ----------

def get_open_position(
    tradingsymbol: str, exchange: str, product: str, auth_token: str
) -> str:
    """Return the net quantity (as string) of an open position; '0' if none."""
    br_tradingsymbol = _get_br_symbol(tradingsymbol, exchange)
    positions_data = _get_cached_positions(auth_token)

    net_qty = "0"
    if positions_data and positions_data.get("status") and positions_data.get("data"):
        for position in positions_data["data"]:
            if (
                position.get("tradingsymbol") == br_tradingsymbol
                and position.get("exchange") == exchange
                and position.get("producttype") == product
            ):
                net_qty = position.get("netqty", "0")
                break
    return net_qty


def place_order_api(order_data: dict, auth_token: str) -> tuple:
    """Place an order via Angel SmartAPI. Returns (response, response_data, orderid)."""
    api_key, jwt_token = _split_token(auth_token)
    order_data = dict(order_data)  # don't mutate caller
    order_data["apikey"] = api_key

    token = _get_token(order_data["symbol"], order_data["exchange"]) or ""
    newdata = transform_data(order_data, token)

    headers = _angel_headers(newdata.get("apikey", api_key), jwt_token)
    payload = json.dumps({
        "variety": newdata.get("variety", "NORMAL"),
        "tradingsymbol": newdata["tradingsymbol"],
        "symboltoken": newdata["symboltoken"],
        "transactiontype": newdata["transactiontype"],
        "exchange": newdata["exchange"],
        "ordertype": newdata.get("ordertype", "MARKET"),
        "producttype": newdata.get("producttype", "INTRADAY"),
        "duration": newdata.get("duration", "DAY"),
        "price": newdata.get("price", "0"),
        "triggerprice": newdata.get("triggerprice", "0"),
        "squareoff": newdata.get("squareoff", "0"),
        "stoploss": newdata.get("stoploss", "0"),
        "quantity": newdata["quantity"],
    })

    client = get_httpx_client()
    response = client.post(
        "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder",
        headers=headers,
        content=payload,
    )
    response.status = response.status_code

    try:
        response_data = response.json()
    except Exception:
        response_data = {"status": False, "message": response.text}

    if response_data.get("status") is True:
        orderid = (response_data.get("data") or {}).get("orderid")
    else:
        orderid = None
    return response, response_data, orderid


def place_smartorder_api(order_data: dict, auth_token: str) -> tuple:
    """Place a smart order — diffs requested position vs current and trades the gap."""
    res = None
    response_data = {"status": "success", "message": "No action needed."}
    orderid = None

    try:
        symbol = order_data.get("symbol")
        exchange = order_data.get("exchange")
        product = order_data.get("product")

        if not all([symbol, exchange, product]):
            return res, {"status": "error", "message": "Missing symbol/exchange/product"}, orderid

        symbol_lock = _get_symbol_lock(symbol, exchange, product)

        with symbol_lock:
            position_size = int(order_data.get("position_size", "0"))
            current_position = int(
                get_open_position(symbol, exchange, map_product_type(product), auth_token)
            )

            logger.info("position_size: %s, current_position: %s", position_size, current_position)

            action = None
            quantity = 0

            if (
                position_size == 0
                and current_position == 0
                and int(order_data.get("quantity", 0) or 0) != 0
            ):
                # Pass-through: caller wants a regular trade.
                res, response, orderid = place_order_api(order_data, auth_token)
                _invalidate_position_cache(auth_token)
                return res, response, orderid

            if position_size == current_position:
                if int(order_data.get("quantity", 0) or 0) == 0:
                    response_data = {
                        "status": "success",
                        "message": "No OpenPosition Found. Not placing Exit order.",
                    }
                else:
                    response_data = {
                        "status": "success",
                        "message": "No action needed. Position size matches current position",
                    }
                return res, response_data, orderid

            if position_size == 0 and current_position > 0:
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
                next_data = dict(order_data)
                next_data["action"] = action
                next_data["quantity"] = str(quantity)
                res, response, orderid = place_order_api(next_data, auth_token)
                _invalidate_position_cache(auth_token)
                return res, response, orderid

            return res, response_data, orderid

    except Exception as e:
        logger.error("Error in place_smartorder_api: %s", e)
        return res, {"status": "error", "message": str(e)}, orderid


def close_all_positions(current_api_key: str, auth_token: str) -> tuple[dict, int]:
    """Close every open position by placing matching market orders."""
    positions_response = get_positions(auth_token)

    if not positions_response or positions_response.get("data") is None or not positions_response.get("data"):
        return {"message": "No Open Positions Found"}, 200

    if positions_response.get("status"):
        for position in positions_response["data"]:
            try:
                netqty = int(position.get("netqty", 0) or 0)
            except (TypeError, ValueError):
                netqty = 0
            if netqty == 0:
                continue

            action = "SELL" if netqty > 0 else "BUY"
            quantity = abs(netqty)

            symbol = _get_oa_symbol_from_token(position.get("symboltoken", ""))
            if not symbol:
                # Fall back to brsymbol-based lookup.
                symbol = _get_oa_symbol(position.get("tradingsymbol", ""), position.get("exchange", ""))
            logger.info("Squaring off symbol: %s", symbol)

            place_order_payload = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": position.get("exchange", ""),
                "pricetype": "MARKET",
                "product": reverse_map_product_type(position.get("producttype", "")),
                "quantity": str(quantity),
            }
            place_order_api(place_order_payload, auth_token)

    return {"status": "success", "message": "All Open Positions SquaredOff"}, 200


def cancel_order(orderid: str, auth_token: str) -> tuple[dict, int]:
    """Cancel a specific order."""
    api_key, jwt_token = _split_token(auth_token)
    headers = _angel_headers(api_key, jwt_token)
    payload = json.dumps({"variety": "NORMAL", "orderid": orderid})

    client = get_httpx_client()
    response = client.post(
        "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/cancelOrder",
        headers=headers,
        content=payload,
    )
    response.status = response.status_code

    try:
        data = response.json()
    except Exception:
        data = {"status": False, "message": response.text}

    if data.get("status"):
        return {"status": "success", "orderid": orderid}, 200
    return {
        "status": "error",
        "message": data.get("message", "Failed to cancel order"),
    }, response.status_code


def modify_order(order_data: dict, auth_token: str) -> tuple[dict, int]:
    """Modify an existing order."""
    api_key, jwt_token = _split_token(auth_token)
    headers = _angel_headers(api_key, jwt_token)

    token = _get_token(order_data["symbol"], order_data["exchange"]) or ""
    data = dict(order_data)
    data["symbol"] = _get_br_symbol(order_data["symbol"], order_data["exchange"])
    transformed = transform_modify_order_data(data, token)

    payload = json.dumps(transformed)

    client = get_httpx_client()
    response = client.post(
        "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/modifyOrder",
        headers=headers,
        content=payload,
    )
    response.status = response.status_code

    try:
        resp = response.json()
    except Exception:
        resp = {"status": False, "message": response.text}

    if resp.get("status") == "true" or resp.get("status") is True or resp.get("message") == "SUCCESS":
        return {"status": "success", "orderid": (resp.get("data") or {}).get("orderid")}, 200
    return {
        "status": "error",
        "message": resp.get("message", "Failed to modify order"),
    }, response.status_code


def cancel_all_orders_api(data: dict, auth_token: str) -> tuple[list, list]:
    """Cancel all open or trigger-pending orders."""
    order_book_response = get_order_book(auth_token)
    if not order_book_response or order_book_response.get("status") is not True:
        return [], []

    orders_to_cancel = [
        order
        for order in order_book_response.get("data", []) or []
        if order.get("status") in ("open", "trigger pending")
    ]

    canceled_orders: list[str] = []
    failed_cancellations: list[str] = []
    for order in orders_to_cancel:
        orderid = order.get("orderid")
        if not orderid:
            continue
        _, status_code = cancel_order(orderid, auth_token)
        if status_code == 200:
            canceled_orders.append(orderid)
        else:
            failed_cancellations.append(orderid)

    return canceled_orders, failed_cancellations
