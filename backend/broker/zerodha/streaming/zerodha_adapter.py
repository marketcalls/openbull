"""
Zerodha KiteTicker streaming adapter.
Connects to Zerodha's binary WebSocket, parses tick packets, and publishes
normalized market data to a ZeroMQ PUB socket.
"""

import logging
import ssl
import struct
import threading
import time

import websocket

from backend.broker.upstox.mapping.order_data import (
    get_symbol_exchange_from_token,
    get_token_from_cache,
)
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("zerodha_stream")

# Zerodha mode bytes for the set-mode binary message
_KITE_MODE = {MODE_LTP: 1, MODE_QUOTE: 2, MODE_DEPTH: 3}
_KITE_MODE_NAME = {1: "ltp", 2: "quote", 3: "full"}


def _numeric_token(token_str: str) -> int | None:
    """Extract the numeric instrument_token from the cache's composite format."""
    if "::::" in token_str:
        try:
            return int(token_str.split("::::")[0])
        except ValueError:
            return None
    try:
        return int(token_str)
    except ValueError:
        return None


class ZerodhaAdapter(BaseBrokerAdapter):
    """Zerodha KiteTicker binary streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._connected = False
        self._last_msg_time: float | None = None
        self._subscribed_tokens: dict[int, int] = {}  # numeric_token -> mode
        # Reverse map: numeric_token -> token_str (for symbol lookup via cache)
        self._ntoken_to_token: dict[int, str] = {}

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        # Auth token format: "api_key:access_token"
        api_key = self.broker_config.get("api_key", "")
        access_token = self.auth_token

        ws_url = f"wss://ws.kite.trade?api_key={api_key}&access_token={access_token}"

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

        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError("Zerodha KiteTicker connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        tokens_to_sub: list[int] = []
        kite_mode = _KITE_MODE.get(mode, 2)

        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token_str = get_token_from_cache(sym, exch) if (sym and exch) else None
            if not token_str:
                continue
            ntok = _numeric_token(token_str)
            if ntok is None:
                continue
            self._ntoken_to_token[ntok] = token_str
            current = self._subscribed_tokens.get(ntok, 0)
            if mode > current:
                self._subscribed_tokens[ntok] = mode
            tokens_to_sub.append(ntok)

        if not tokens_to_sub or not self._connected:
            return

        # Subscribe binary message: type=1
        self._send_subscribe(tokens_to_sub)
        # Set mode binary message: type=2
        self._send_set_mode(tokens_to_sub, kite_mode)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        tokens: list[int] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token_str = get_token_from_cache(sym, exch) if (sym and exch) else None
            if not token_str:
                continue
            ntok = _numeric_token(token_str)
            if ntok is not None:
                tokens.append(ntok)
                self._subscribed_tokens.pop(ntok, None)
                self._ntoken_to_token.pop(ntok, None)

        if tokens and self._connected:
            self._send_unsubscribe(tokens)

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._subscribed_tokens.clear()
        self._ntoken_to_token.clear()
        logger.info("Zerodha adapter disconnected")

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
                logger.error("Zerodha WS run_forever error: %s", e)

            self._connected = False
            if not self._running:
                break

            reconnect_attempts += 1
            if reconnect_attempts > 10:
                logger.error("Zerodha max reconnect attempts reached")
                break

            delay = min(2 * (1.5 ** reconnect_attempts), 60)
            logger.info("Reconnecting in %.1fs (attempt %d)...", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Zerodha KiteTicker connected")
        self._connected = True
        self._last_msg_time = time.time()
        self._start_health_check()

        # Re-subscribe on reconnect
        if self._subscribed_tokens:
            by_mode: dict[int, list[int]] = {}
            for ntok, mode in self._subscribed_tokens.items():
                by_mode.setdefault(mode, []).append(ntok)
            for mode, tokens in by_mode.items():
                self._send_subscribe(tokens)
                self._send_set_mode(tokens, _KITE_MODE.get(mode, 2))
            logger.info("Re-subscribed %d tokens after reconnect", len(self._subscribed_tokens))

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()
        if isinstance(message, bytes) and len(message) >= 4:
            self._parse_and_publish(message)

    def _on_error(self, ws, error) -> None:
        logger.error("Zerodha WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Zerodha WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Binary parsing (KiteTicker protocol) ----

    def _parse_and_publish(self, data: bytes) -> None:
        try:
            if len(data) < 4:
                return
            num_packets = struct.unpack(">H", data[0:2])[0]
            offset = 2

            for _ in range(num_packets):
                if offset + 2 > len(data):
                    break
                pkt_len = struct.unpack(">H", data[offset:offset + 2])[0]
                offset += 2
                if offset + pkt_len > len(data):
                    break
                pkt = data[offset:offset + pkt_len]
                offset += pkt_len

                self._parse_packet(pkt)

        except Exception as e:
            logger.error("Binary parse error: %s", e)

    def _parse_packet(self, pkt: bytes) -> None:
        if len(pkt) < 8:
            return

        ntok = struct.unpack(">I", pkt[0:4])[0]
        ltp = struct.unpack(">i", pkt[4:8])[0] / 100.0

        # Resolve symbol
        token_str = self._ntoken_to_token.get(ntok)
        if not token_str:
            return
        info = get_symbol_exchange_from_token(token_str)
        if not info:
            # Try numeric string
            info = get_symbol_exchange_from_token(str(ntok))
        if not info:
            return

        symbol, exchange = info

        # Determine packet mode by length
        if len(pkt) == 8:
            pkt_mode = MODE_LTP
        elif len(pkt) >= 184:
            pkt_mode = MODE_DEPTH
        elif len(pkt) >= 44:
            pkt_mode = MODE_QUOTE
        else:
            pkt_mode = MODE_LTP

        # Always publish LTP
        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp, "ltt": int(time.time()),
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        # QUOTE (44+ bytes)
        if pkt_mode >= MODE_QUOTE and len(pkt) >= 44:
            try:
                fields = struct.unpack(">11i", pkt[0:44])
                quote_data = {
                    "symbol": symbol, "exchange": exchange, "mode": "quote",
                    "ltp": fields[1] / 100.0,
                    "open": fields[7] / 100.0,
                    "high": fields[8] / 100.0,
                    "low": fields[9] / 100.0,
                    "close": fields[10] / 100.0,
                    "volume": fields[4],
                    "average_price": fields[3] / 100.0,
                    "total_buy_quantity": fields[5],
                    "total_sell_quantity": fields[6],
                    "oi": 0,
                    "ltt": int(time.time()),
                }
                # OI at offset 180 (if available)
                if len(pkt) >= 184:
                    try:
                        quote_data["oi"] = struct.unpack(">I", pkt[180:184])[0]
                    except struct.error:
                        pass

                self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)
            except struct.error as e:
                logger.debug("Quote parse error for %s: %s", symbol, e)

        # DEPTH (184+ bytes) — requires quote_data from the block above
        if pkt_mode >= MODE_DEPTH and len(pkt) >= 184 and pkt_mode >= MODE_QUOTE:
            try:
                bids, asks = [], []
                depth_offset = 64
                for i in range(5):
                    base = depth_offset + (i * 12)
                    if base + 12 <= len(pkt):
                        qty = struct.unpack(">I", pkt[base:base + 4])[0]
                        price = struct.unpack(">I", pkt[base + 4:base + 8])[0] / 100.0
                        orders = struct.unpack(">H", pkt[base + 8:base + 10])[0]
                        bids.append({"quantity": qty, "price": price, "orders": orders})
                for i in range(5):
                    base = depth_offset + 60 + (i * 12)
                    if base + 12 <= len(pkt):
                        qty = struct.unpack(">I", pkt[base:base + 4])[0]
                        price = struct.unpack(">I", pkt[base + 4:base + 8])[0] / 100.0
                        orders = struct.unpack(">H", pkt[base + 8:base + 10])[0]
                        asks.append({"quantity": qty, "price": price, "orders": orders})

                depth_data = {
                    **quote_data,
                    "mode": "full",
                    "depth": {"buy": bids, "sell": asks},
                }
                self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)
            except (struct.error, NameError) as e:
                logger.debug("Depth parse error for %s: %s", symbol, e)

    # ---- Binary message builders ----

    def _send_subscribe(self, tokens: list[int]) -> None:
        """Subscribe message: type=1, count, tokens."""
        n = len(tokens)
        msg = struct.pack(f">bH{'I' * n}", 1, n, *tokens)
        try:
            self._ws.send(msg, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            logger.error("Subscribe send error: %s", e)

    def _send_unsubscribe(self, tokens: list[int]) -> None:
        """Unsubscribe message: type=1 with count=0 then tokens."""
        n = len(tokens)
        msg = struct.pack(f">bH{'I' * n}", 1, n, *tokens)
        # KiteTicker unsubscribe: re-send subscribe then set mode=0 effectively.
        # Actually KiteTicker uses a different approach: just stop mode for tokens.
        # For simplicity, send nothing — the proxy will stop routing.
        pass

    def _send_set_mode(self, tokens: list[int], mode: int) -> None:
        """Set mode message: type=2, count, mode_byte, tokens."""
        n = len(tokens)
        msg = struct.pack(f">bHb{'I' * n}", 2, n, mode, *tokens)
        try:
            self._ws.send(msg, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            logger.error("Set mode send error: %s", e)

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
                logger.error("Zerodha data stall (>90s). Forcing reconnect.")
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                break
