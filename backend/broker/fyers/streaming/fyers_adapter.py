"""
Fyers v3 streaming adapter.

Connects to Fyers' Data WebSocket, decodes ticks (delivered as JSON over the
data socket) and publishes normalized market data to a ZeroMQ PUB socket.

Wire protocol (Fyers Data WS v3):
    URL: wss://api-t1.fyers.in/data?access_token={api_key}:{access_token}
    Subscribe : {"T":"SUB_L2","SLIST":[<broker_symbols>],"SUB_T":1}
    Unsubscribe: {"T":"SUB_L2","SLIST":[<broker_symbols>],"SUB_T":-1}
    Mode flag: SUB_T = 1 (subscribe), -1 (unsubscribe)

We always subscribe in the richest available mode and let the proxy do
per-client filtering at the topic layer (LTP / QUOTE / DEPTH).
"""

import json
import logging
import ssl
import threading
import time

import websocket

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_symbol_from_brsymbol_cache,
)
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("fyers_stream")

RECONNECT_MAX_TRIES = 50
RECONNECT_MAX_DELAY = 60
SUBSCRIBE_BATCH_SIZE = 100


class FyersAdapter(BaseBrokerAdapter):
    """Fyers v3 streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        """auth_token is the combined ``api_key:access_token`` string."""
        super().__init__(auth_token, broker_config)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._connected = False
        self._last_msg_time: float | None = None

        # broker_symbol -> (symbol, exchange, mode)
        self._subs: dict[str, tuple[str, str, int]] = {}
        self._sub_lock = threading.Lock()

        # cache last close for change/change_percent calc
        self._last_close: dict[str, float] = {}

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        ws_url = (
            "wss://api-t1.fyers.in/data?access_token="
            + self.auth_token
        )

        self._running = True
        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True, name="fyers-ws")
        self._ws_thread.start()

        # Wait up to 10s for handshake.
        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError("Fyers WebSocket connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        new_brsymbols: list[str] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            if not sym or not exch:
                continue
            br = get_brsymbol_from_cache(sym, exch)
            if not br:
                logger.warning("Fyers WS: no broker symbol for %s/%s", sym, exch)
                continue
            with self._sub_lock:
                existing = self._subs.get(br)
                effective_mode = max(mode, existing[2]) if existing else mode
                self._subs[br] = (sym, exch, effective_mode)
            new_brsymbols.append(br)

        if not new_brsymbols or not self._connected:
            return

        for i in range(0, len(new_brsymbols), SUBSCRIBE_BATCH_SIZE):
            batch = new_brsymbols[i:i + SUBSCRIBE_BATCH_SIZE]
            self._send(json.dumps({"T": "SUB_L2", "SLIST": batch, "SUB_T": 1}))

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        brsymbols: list[str] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            if not sym or not exch:
                continue
            br = get_brsymbol_from_cache(sym, exch)
            if not br:
                continue
            with self._sub_lock:
                self._subs.pop(br, None)
            brsymbols.append(br)

        if brsymbols and self._connected:
            for i in range(0, len(brsymbols), SUBSCRIBE_BATCH_SIZE):
                batch = brsymbols[i:i + SUBSCRIBE_BATCH_SIZE]
                self._send(json.dumps({"T": "SUB_L2", "SLIST": batch, "SUB_T": -1}))

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        with self._sub_lock:
            self._subs.clear()
        logger.info("Fyers adapter disconnected")

    # ---- WS internals ----

    def _send(self, frame: str) -> None:
        try:
            if self._ws and self._connected:
                self._ws.send(frame)
        except Exception as e:
            logger.error("Fyers WS send error: %s", e)

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
                logger.error("Fyers WS run_forever error: %s", e)

            self._connected = False
            if not self._running:
                break

            reconnect_attempts += 1
            if reconnect_attempts > RECONNECT_MAX_TRIES:
                logger.error("Fyers max reconnect attempts (%d) reached", RECONNECT_MAX_TRIES)
                break

            delay = min(2 * (1.5 ** reconnect_attempts), RECONNECT_MAX_DELAY)
            logger.info("Reconnecting in %.1fs (attempt %d)", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Fyers Data WebSocket connected")
        self._connected = True
        self._last_msg_time = time.time()
        self._start_health_check()

        # Re-subscribe on reconnect.
        with self._sub_lock:
            brsymbols = list(self._subs.keys())
        for i in range(0, len(brsymbols), SUBSCRIBE_BATCH_SIZE):
            batch = brsymbols[i:i + SUBSCRIBE_BATCH_SIZE]
            self._send(json.dumps({"T": "SUB_L2", "SLIST": batch, "SUB_T": 1}))
        if brsymbols:
            logger.info("Re-subscribed %d Fyers symbols after reconnect", len(brsymbols))

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()

        if isinstance(message, (bytes, bytearray)):
            # Fyers' Data WS may send compact binary payloads; we forward the
            # raw bytes to the JSON path only when it can be decoded. Most
            # market-data ticks come as JSON text on this endpoint.
            try:
                message = message.decode("utf-8")
            except Exception:
                return

        if not isinstance(message, str):
            return

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("Fyers WS non-JSON: %s", message[:120])
            return

        # Tick payloads come either as a top-level object or as a list.
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    self._handle_tick(item)
        elif isinstance(payload, dict):
            # Server-side acks / errors look like {"s":"ok",...} or {"s":"error",...}
            if payload.get("s") == "error":
                logger.error("Fyers WS server error: %s", payload.get("message"))
                return
            self._handle_tick(payload)

    def _on_error(self, ws, error) -> None:
        logger.error("Fyers WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Fyers WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Tick handling ----

    def _handle_tick(self, tick: dict) -> None:
        """Translate a Fyers tick dict to LTP/QUOTE/DEPTH topics on ZMQ."""
        # Fyers data WS uses 'symbol' for the broker symbol.
        br = tick.get("symbol") or tick.get("n")
        if not br:
            return

        with self._sub_lock:
            sub = self._subs.get(br)
        if sub:
            symbol, exchange, _mode = sub
        else:
            # Fall back to a cache lookup — covers ticks whose broker symbol
            # arrives before our subscribe ack pass.
            symbol = None
            exchange = None
            for exch_candidate in ("NSE", "BSE", "NFO", "BFO", "MCX", "CDS"):
                resolved = get_symbol_from_brsymbol_cache(br, exch_candidate)
                if resolved:
                    symbol = resolved
                    exchange = exch_candidate
                    break
            if not symbol or not exchange:
                return

        # Fyers field names (Data WS v3):
        #   ltp, type, ch, chp, open_price, high_price, low_price, prev_close_price,
        #   vol_traded_today, last_traded_qty, avg_trade_price, tot_buy_qty,
        #   tot_sell_qty, exch_feed_time, bid_size, ask_size, bid_price, ask_price,
        #   bids, asks (5-level depth arrays)
        ltp = float(tick.get("ltp", 0) or 0)
        prev_close = float(tick.get("prev_close_price", 0) or 0)
        if prev_close:
            self._last_close[br] = prev_close
        else:
            prev_close = self._last_close.get(br, 0.0)

        change = round(ltp - prev_close, 4) if (ltp and prev_close) else 0.0
        change_pct = round((ltp - prev_close) / prev_close * 100, 4) if prev_close else 0.0
        ts_now = int(tick.get("exch_feed_time") or time.time())

        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp, "ltt": ts_now,
            "cp": prev_close, "change": change, "change_percent": change_pct,
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        quote_data = {
            **ltp_data,
            "mode": "quote",
            "open": float(tick.get("open_price", 0) or 0),
            "high": float(tick.get("high_price", 0) or 0),
            "low": float(tick.get("low_price", 0) or 0),
            "close": prev_close,
            "volume": int(tick.get("vol_traded_today", 0) or 0),
            "ltq": int(tick.get("last_traded_qty", 0) or 0),
            "average_price": float(tick.get("avg_trade_price", 0) or 0),
            "total_buy_quantity": int(tick.get("tot_buy_qty", 0) or 0),
            "total_sell_quantity": int(tick.get("tot_sell_qty", 0) or 0),
            "oi": int(tick.get("tot_oi", 0) or 0),
            "bid": float(tick.get("bid_price", 0) or 0),
            "ask": float(tick.get("ask_price", 0) or 0),
            "bid_qty": int(tick.get("bid_size", 0) or 0),
            "ask_qty": int(tick.get("ask_size", 0) or 0),
        }
        self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)

        bids = tick.get("bids")
        asks = tick.get("asks") or tick.get("ask")
        if isinstance(bids, list) and isinstance(asks, list):
            depth_data = {
                **quote_data,
                "mode": "full",
                "depth": {
                    "buy": [
                        {
                            "price": float(b.get("price", 0) or 0),
                            "quantity": int(b.get("volume", b.get("qty", 0)) or 0),
                            "orders": int(b.get("ord", b.get("orders", 0)) or 0),
                        }
                        for b in bids[:5]
                    ],
                    "sell": [
                        {
                            "price": float(a.get("price", 0) or 0),
                            "quantity": int(a.get("volume", a.get("qty", 0)) or 0),
                            "orders": int(a.get("ord", a.get("orders", 0)) or 0),
                        }
                        for a in asks[:5]
                    ],
                },
            }
            self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)

    # ---- Health check ----

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
                logger.error("Fyers data stall (>90s). Forcing reconnect.")
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                break
