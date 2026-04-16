"""
Upstox v3 streaming adapter.
Connects to Upstox protobuf WebSocket feed, decodes ticks, and publishes
normalized market data to a ZeroMQ PUB socket.
"""

import json
import logging
import ssl
import threading
import time
import uuid

import httpx
import websocket
from google.protobuf.json_format import MessageToDict

from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_token_from_cache,
)
from backend.broker.upstox.streaming import MarketDataFeedV3_pb2
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("upstox_stream")

AUTH_ENDPOINT = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
# Always subscribe in "full" mode to get continuous updates.
# Upstox ltpc mode only fires on price change (very infrequent).
# Server-side routing filters LTP/QUOTE/DEPTH per client subscription.
_UPSTOX_MODE = {MODE_LTP: "full", MODE_QUOTE: "full", MODE_DEPTH: "full"}


class UpstoxAdapter(BaseBrokerAdapter):
    """Upstox v3 protobuf streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._connected = False
        self._last_msg_time: float | None = None
        self._subscribed_keys: set[str] = set()
        self._key_mode: dict[str, int] = {}  # instrument_key -> highest subscribed mode

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        ws_url = self._get_ws_url()
        if not ws_url:
            raise ConnectionError("Failed to get Upstox WebSocket URL")

        self._running = True
        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()

        # Wait for connection (max 10s)
        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError("Upstox WebSocket connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        keys: list[str] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token = get_token_from_cache(sym, exch) if (sym and exch) else None
            if token:
                keys.append(token)
                # Always publish all topics (LTP+QUOTE+DEPTH) since we
                # subscribe in "full" mode. Server handles per-client filtering.
                self._key_mode[token] = MODE_DEPTH

        if not keys:
            return

        upstox_mode = _UPSTOX_MODE.get(mode, "ltpc")
        self._send_subscription(keys, upstox_mode, "sub")
        self._subscribed_keys.update(keys)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        keys: list[str] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token = get_token_from_cache(sym, exch) if (sym and exch) else None
            if token:
                keys.append(token)
                self._key_mode.pop(token, None)

        if keys:
            self._send_subscription(keys, method="unsub")
            self._subscribed_keys.difference_update(keys)

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._subscribed_keys.clear()
        self._key_mode.clear()
        logger.info("Upstox adapter disconnected")

    # ---- WS internals ----

    def _run_ws(self) -> None:
        reconnect_attempts = 0
        while self._running:
            try:
                self._ws.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                    ping_interval=30,
                    ping_timeout=10,
                )
            except Exception as e:
                logger.error("Upstox WS run_forever error: %s", e)

            self._connected = False
            if not self._running:
                break

            reconnect_attempts += 1
            if reconnect_attempts > 5:
                logger.error("Upstox max reconnect attempts reached")
                break

            delay = min(2 ** reconnect_attempts, 30)
            logger.info("Reconnecting in %ds (attempt %d)...", delay, reconnect_attempts)
            time.sleep(delay)

            ws_url = self._get_ws_url()
            if ws_url:
                self._ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

    def _on_open(self, ws) -> None:
        logger.info("Upstox WebSocket connected")
        self._connected = True
        self._last_msg_time = time.time()
        self._start_health_check()

        # Re-subscribe on reconnect
        if self._subscribed_keys:
            keys = list(self._subscribed_keys)
            self._send_subscription(keys, "full", "sub")
            logger.info("Re-subscribed %d keys after reconnect", len(keys))

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()
        if isinstance(message, bytes):
            self._process_protobuf(message)

    def _on_error(self, ws, error) -> None:
        logger.error("Upstox WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Upstox WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Protobuf decoding ----

    def _process_protobuf(self, data: bytes) -> None:
        try:
            feed_response = MarketDataFeedV3_pb2.FeedResponse()
            feed_response.ParseFromString(data)
            decoded = MessageToDict(feed_response)
        except Exception as e:
            logger.error("Protobuf decode error: %s", e)
            return

        feeds = decoded.get("feeds", {})
        for feed_key, feed_data in feeds.items():
            self._process_feed(feed_key, feed_data, decoded.get("currentTs"))

    def _process_feed(self, feed_key: str, feed_data: dict, current_ts) -> None:
        # Resolve instrument_key → (symbol, exchange)
        info = get_symbol_exchange_from_token(feed_key)
        if not info:
            # Try stripping exchange prefix (feed_key might be "NSE_EQ|12345")
            if "|" in feed_key:
                info = get_symbol_exchange_from_token(feed_key)
            if not info:
                return

        symbol, exchange = info
        highest_mode = self._key_mode.get(feed_key, MODE_LTP)

        # Extract fullFeed.marketFF (Upstox v3 protobuf structure)
        full_feed = feed_data.get("fullFeed", feed_data.get("ff", {}))
        market_ff = full_feed.get("marketFF", {}) if isinstance(full_feed, dict) else {}

        # LTPC can be at top-level (ltpc mode) or inside marketFF (full mode)
        ltpc = feed_data.get("ltpc") or market_ff.get("ltpc", {})
        ltp = ltpc.get("ltp", 0)
        ltt = ltpc.get("ltt")
        cp = ltpc.get("cp", 0)

        # Always publish LTP
        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp, "ltt": ltt, "change": cp,
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        if market_ff and highest_mode >= MODE_QUOTE:
            # Extract OHLCV from marketOHLC
            ohlc_list = market_ff.get("marketOHLC", {}).get("ohlc", [])
            day_ohlc = {}
            for ohlc in ohlc_list:
                if ohlc.get("interval") == "1d":
                    day_ohlc = ohlc
                    break

            atp = market_ff.get("atp", 0)
            tbq = market_ff.get("tbq", 0)
            tsq = market_ff.get("tsq", 0)
            oi = market_ff.get("oi", 0)

            quote_data = {
                "symbol": symbol, "exchange": exchange, "mode": "quote",
                "ltp": ltp, "ltt": ltt, "change": cp,
                "open": day_ohlc.get("open", 0),
                "high": day_ohlc.get("high", 0),
                "low": day_ohlc.get("low", 0),
                "close": day_ohlc.get("close", 0),
                "volume": day_ohlc.get("vol", 0) or day_ohlc.get("volume", 0),
                "oi": oi,
                "average_price": atp,
                "total_buy_quantity": tbq,
                "total_sell_quantity": tsq,
            }
            self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)

        if market_ff and highest_mode >= MODE_DEPTH:
            bid_ask = market_ff.get("marketLevel", {}).get("bidAskQuote", [])
            bids = []
            asks = []
            for level in bid_ask[:5]:
                bids.append({
                    "price": level.get("bidP", level.get("bp", 0)),
                    "quantity": level.get("bidQ", level.get("bq", 0)),
                    "orders": level.get("bidO", level.get("bno", 0)),
                })
                asks.append({
                    "price": level.get("askP", level.get("ap", 0)),
                    "quantity": level.get("askQ", level.get("aq", 0)),
                    "orders": level.get("askO", level.get("ano", 0)),
                })

            # Pad to 5 levels
            empty = {"price": 0, "quantity": 0, "orders": 0}
            while len(bids) < 5:
                bids.append(empty.copy())
            while len(asks) < 5:
                asks.append(empty.copy())

            depth_data = {**quote_data, "mode": "full", "depth": {"buy": bids, "sell": asks}}
            self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)

    # ---- Helpers ----

    def _get_ws_url(self) -> str | None:
        try:
            headers = {"Accept": "application/json", "Authorization": f"Bearer {self.auth_token}"}
            resp = httpx.get(AUTH_ENDPOINT, headers=headers, timeout=10)
            resp.raise_for_status()
            url = resp.json().get("data", {}).get("authorized_redirect_uri")
            if url:
                logger.info("Upstox WS URL obtained")
            return url
        except Exception as e:
            logger.error("Failed to get Upstox WS URL: %s", e)
            return None

    def _send_subscription(self, keys: list[str], mode: str = "full", method: str = "sub") -> None:
        if not self._connected or not self._ws:
            return
        msg = {
            "guid": uuid.uuid4().hex[:20],
            "method": method,
            "data": {"instrumentKeys": keys},
        }
        if method == "sub" and mode:
            msg["data"]["mode"] = mode
        try:
            self._ws.send(json.dumps(msg).encode("utf-8"), opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            logger.error("Send subscription error: %s", e)

    def _start_health_check(self) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()

    def _health_loop(self) -> None:
        while self._running and self._connected:
            time.sleep(30)
            if not self._running or not self._connected:
                break
            if self._last_msg_time and (time.time() - self._last_msg_time) > 90:
                logger.error("Upstox data stall (>90s). Forcing reconnect.")
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                break
