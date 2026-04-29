"""
Zerodha KiteTicker streaming adapter.
Connects to Zerodha's WebSocket, parses tick packets, and publishes
normalized market data to a ZeroMQ PUB socket.

Wire protocol: JSON text frames for control (subscribe/mode/unsubscribe);
binary frames for ticks. Per Kite docs, the only accepted control frame
shapes are:
    {"a":"subscribe","v":[tokens]}
    {"a":"unsubscribe","v":[tokens]}
    {"a":"mode","v":[mode,[tokens]]}    # mode in {"ltp","quote","full"}
"""

import json
import logging
import ssl
import struct
import threading
import time
from collections import deque

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

# Kite uses string mode names on the wire; we map our integer modes to those
# strings. MODE_DEPTH on our side maps to "full" on Kite's side because Kite's
# full-mode packet is what carries the depth ladder.
_KITE_MODE_NAME = {MODE_LTP: "ltp", MODE_QUOTE: "quote", MODE_DEPTH: "full"}

# Subscription batching — Kite supports up to 3000 instruments per connection,
# but bulk subscribes must be chunked or the server can drop frames.
MAX_TOKENS_PER_SUBSCRIBE = 200
SUBSCRIPTION_DELAY = 2.0
MAX_INSTRUMENTS_PER_CONNECTION = 3000

# Reconnect config
RECONNECT_MAX_TRIES = 50
RECONNECT_MAX_DELAY = 60


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
    """Zerodha KiteTicker streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._sub_thread: threading.Thread | None = None
        self._connected = False
        self._last_msg_time: float | None = None
        self._subscribed_tokens: dict[int, int] = {}  # numeric_token -> our integer mode
        self._ntoken_to_token: dict[int, str] = {}    # reverse map for symbol lookup
        self._last_close: dict[int, float] = {}       # per-token prev close cache
        # Pending subscribe queue: (token, our_mode_int). Drained in batches by
        # _process_subscriptions on a worker thread so subscribe() returns fast.
        self._pending: deque = deque()
        self._pending_lock = threading.Lock()
        self._sub_lock = threading.Lock()  # protects _subscribed_tokens / _ntoken_to_token

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        # auth_api stores the token as "api_key:access_token" because that's
        # what Kite REST expects in the Authorization header. The WS URL
        # however needs the BARE access_token — sending the combined form
        # makes Kite reject the handshake. Split it here.
        if ":" in self.auth_token:
            api_key_from_token, _, access_token = self.auth_token.partition(":")
            api_key = self.broker_config.get("api_key") or api_key_from_token
        else:
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
        if mode not in _KITE_MODE_NAME:
            logger.error("Invalid Zerodha mode %s; expected one of %s", mode, list(_KITE_MODE_NAME))
            return

        new_ntoks: list[int] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token_str = get_token_from_cache(sym, exch) if (sym and exch) else None
            if not token_str:
                continue
            ntok = _numeric_token(token_str)
            if ntok is None:
                continue
            with self._sub_lock:
                self._ntoken_to_token[ntok] = token_str
                current = self._subscribed_tokens.get(ntok, 0)
                if mode > current:
                    self._subscribed_tokens[ntok] = mode
                # Capacity guard — Kite docs cap at 3000 instruments per connection.
                if len(self._subscribed_tokens) > MAX_INSTRUMENTS_PER_CONNECTION:
                    logger.error(
                        "Zerodha WS subscription cap reached (%d). Dropping %s/%s.",
                        MAX_INSTRUMENTS_PER_CONNECTION, sym, exch,
                    )
                    self._subscribed_tokens.pop(ntok, None)
                    self._ntoken_to_token.pop(ntok, None)
                    continue
            new_ntoks.append(ntok)

        if not new_ntoks:
            return

        with self._pending_lock:
            for ntok in new_ntoks:
                self._pending.append((ntok, mode))

        self._ensure_sub_worker()

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        tokens: list[int] = []
        for item in symbols:
            sym, exch = item.get("symbol"), item.get("exchange")
            token_str = get_token_from_cache(sym, exch) if (sym and exch) else None
            if not token_str:
                continue
            ntok = _numeric_token(token_str)
            if ntok is None:
                continue
            with self._sub_lock:
                self._subscribed_tokens.pop(ntok, None)
                self._ntoken_to_token.pop(ntok, None)
            tokens.append(ntok)

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
        with self._sub_lock:
            self._subscribed_tokens.clear()
            self._ntoken_to_token.clear()
        with self._pending_lock:
            self._pending.clear()
        logger.info("Zerodha adapter disconnected")

    # ---- WS internals ----

    def _run_ws(self) -> None:
        reconnect_attempts = 0
        while self._running:
            # Arm the reset signal before each run; _on_open will set it true
            # on a successful handshake. If run_forever returns without that,
            # the attempt failed and we count it against the budget.
            self._reconnect_reset_signal = False
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

            if self._reconnect_reset_signal:
                # We connected at least once during this run — flaky session,
                # not a dead one. Reset the counter so the budget isn't burned.
                reconnect_attempts = 0

            reconnect_attempts += 1
            if reconnect_attempts > RECONNECT_MAX_TRIES:
                logger.error("Zerodha max reconnect attempts (%d) reached", RECONNECT_MAX_TRIES)
                break

            delay = min(2 * (1.5 ** reconnect_attempts), RECONNECT_MAX_DELAY)
            logger.info("Reconnecting in %.1fs (attempt %d)...", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Zerodha KiteTicker connected")
        self._connected = True
        self._last_msg_time = time.time()
        # Reset the outer reconnect counter via a thread-local marker. We can't
        # touch _run_ws's local reconnect_attempts directly, so we expose a
        # per-instance counter and let _run_ws read it.
        self._reconnect_reset_signal = True
        self._start_health_check()

        # Re-subscribe on reconnect, in batches.
        with self._sub_lock:
            by_mode: dict[int, list[int]] = {}
            for ntok, mode in self._subscribed_tokens.items():
                by_mode.setdefault(mode, []).append(ntok)

        for mode, tokens in by_mode.items():
            mode_name = _KITE_MODE_NAME.get(mode, "quote")
            for i in range(0, len(tokens), MAX_TOKENS_PER_SUBSCRIBE):
                batch = tokens[i:i + MAX_TOKENS_PER_SUBSCRIBE]
                self._send_subscribe(batch)
                time.sleep(0.5)
                self._send_set_mode(batch, mode_name)
                time.sleep(SUBSCRIPTION_DELAY)
        if by_mode:
            total = sum(len(t) for t in by_mode.values())
            logger.info("Re-subscribed %d tokens after reconnect", total)

        self._ensure_sub_worker()

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()
        # 1-byte binary frames are Kite's heartbeat; do not treat as data.
        if isinstance(message, (bytes, bytearray)):
            if len(message) == 1:
                return
            if len(message) >= 4:
                self._parse_and_publish(bytes(message))
            return
        # Text frames are server-side control / errors. Surface them.
        if isinstance(message, str):
            try:
                data = json.loads(message)
                if data.get("type") == "error":
                    logger.error("Zerodha WS server error: %s", data.get("data", ""))
                else:
                    logger.debug("Zerodha WS text frame: %s", str(data)[:200])
            except json.JSONDecodeError:
                logger.debug("Zerodha WS non-JSON text: %s", message[:120])

    def _on_error(self, ws, error) -> None:
        logger.error("Zerodha WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Zerodha WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Subscription worker ----

    def _ensure_sub_worker(self) -> None:
        if self._sub_thread and self._sub_thread.is_alive():
            return
        self._sub_thread = threading.Thread(
            target=self._process_subscriptions, daemon=True, name="zerodha-sub-worker",
        )
        self._sub_thread.start()

    def _process_subscriptions(self) -> None:
        consecutive_failures = 0
        while self._running:
            with self._pending_lock:
                pending_count = len(self._pending)
            if not pending_count:
                return  # caller will respawn us when more arrive

            if not self._connected:
                consecutive_failures += 1
                if consecutive_failures > 3:
                    logger.error("Subscription worker giving up after 3 disconnect cycles")
                    return
                time.sleep(min(2 * consecutive_failures, 10))
                continue
            consecutive_failures = 0

            # Drain a batch of up to MAX_TOKENS_PER_SUBSCRIBE for a single mode.
            batch: list[int] = []
            batch_mode: int | None = None
            with self._pending_lock:
                while self._pending and len(batch) < MAX_TOKENS_PER_SUBSCRIBE:
                    ntok, mode = self._pending[0]
                    if batch_mode is None:
                        batch_mode = mode
                    elif mode != batch_mode:
                        break
                    self._pending.popleft()
                    batch.append(ntok)

            if not batch or batch_mode is None:
                continue

            mode_name = _KITE_MODE_NAME.get(batch_mode, "quote")
            try:
                self._send_subscribe(batch)
                time.sleep(1.0)
                self._send_set_mode(batch, mode_name)
                logger.debug("Subscribed batch of %d tokens in mode '%s'", len(batch), mode_name)
                time.sleep(SUBSCRIPTION_DELAY)
            except Exception as e:
                logger.error("Subscribe batch failed (%d tokens, mode '%s'): %s", len(batch), mode_name, e)
                # requeue and back off
                with self._pending_lock:
                    for ntok in batch:
                        self._pending.appendleft((ntok, batch_mode))
                time.sleep(5)

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
            info = get_symbol_exchange_from_token(str(ntok))
        if not info:
            return

        symbol, exchange = info

        # Index packets use a compact layout (28 / 32 bytes) — different
        # field order from equity quote / full. Handle them separately.
        if len(pkt) in (28, 32):
            self._parse_index_packet(pkt, ntok, ltp, symbol, exchange)
            return

        # Determine packet mode by length (equity layout)
        if len(pkt) == 8:
            pkt_mode = MODE_LTP
        elif len(pkt) >= 184:
            pkt_mode = MODE_DEPTH
        elif len(pkt) >= 44:
            pkt_mode = MODE_QUOTE
        else:
            pkt_mode = MODE_LTP

        cp_cached = self._last_close.get(ntok, 0.0)
        change = round(ltp - cp_cached, 4) if (ltp and cp_cached) else 0.0
        change_percent = round((ltp - cp_cached) / cp_cached * 100, 4) if cp_cached else 0.0

        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp, "ltt": int(time.time()),
            "cp": cp_cached, "change": change, "change_percent": change_percent,
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        quote_data: dict | None = None

        # QUOTE (44+ bytes)
        if pkt_mode >= MODE_QUOTE and len(pkt) >= 44:
            try:
                fields = struct.unpack(">11i", pkt[0:44])
                close = fields[10] / 100.0
                if close:
                    self._last_close[ntok] = close
                q_change = round(ltp - close, 4) if (ltp and close) else 0.0
                q_change_pct = round((ltp - close) / close * 100, 4) if close else 0.0
                quote_data = {
                    "symbol": symbol, "exchange": exchange, "mode": "quote",
                    "ltp": fields[1] / 100.0,
                    "ltq": fields[2],
                    "average_price": fields[3] / 100.0,
                    "volume": fields[4],
                    "total_buy_quantity": fields[5],
                    "total_sell_quantity": fields[6],
                    "open": fields[7] / 100.0,
                    "high": fields[8] / 100.0,
                    "low": fields[9] / 100.0,
                    "close": close,
                    "cp": close,
                    "change": q_change,
                    "change_percent": q_change_pct,
                    "oi": 0,
                    "ltt": int(time.time()),
                }

                # Per Kite WS docs (full 184-byte packet layout):
                #   offset 44-48  : last_traded_time   (epoch seconds)
                #   offset 48-52  : oi
                #   offset 52-56  : oi_day_high
                #   offset 56-60  : oi_day_low
                #   offset 60-64  : exchange_timestamp (epoch seconds)
                #   offset 64-184 : market depth (5 buy + 5 sell, 12 B each)
                if len(pkt) >= 64:
                    try:
                        ltt = struct.unpack(">I", pkt[44:48])[0]
                        oi = struct.unpack(">I", pkt[48:52])[0]
                        oi_day_high = struct.unpack(">I", pkt[52:56])[0]
                        oi_day_low = struct.unpack(">I", pkt[56:60])[0]
                        exch_ts = struct.unpack(">I", pkt[60:64])[0]
                        quote_data["last_traded_time"] = ltt
                        quote_data["oi"] = oi
                        quote_data["oi_day_high"] = oi_day_high
                        quote_data["oi_day_low"] = oi_day_low
                        quote_data["exchange_timestamp"] = exch_ts
                        if ltt:
                            quote_data["ltt"] = ltt
                    except struct.error:
                        pass

                self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)
            except struct.error as e:
                logger.debug("Quote parse error for %s: %s", symbol, e)

        # DEPTH / FULL (184+ bytes)
        if pkt_mode >= MODE_DEPTH and len(pkt) >= 184 and quote_data is not None:
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
            except struct.error as e:
                logger.debug("Depth parse error for %s: %s", symbol, e)

    def _parse_index_packet(
        self, pkt: bytes, ntok: int, ltp: float, symbol: str, exchange: str,
    ) -> None:
        """Parse Kite's compact index packet (28 = quote, 32 = full).

        Layout (per Kite WS docs):
            0-4   token
            4-8   ltp
            8-12  high
            12-16 low
            16-20 open
            20-24 close
            24-28 price_change
            28-32 exchange_timestamp (full only)
        """
        try:
            # 28 bytes contains 7 int32 values; 32 bytes adds exchange_timestamp.
            if len(pkt) == 28:
                _, _, high_p, low_p, open_p, close_p, _change_p = struct.unpack(">7i", pkt[0:28])
                exch_ts = 0
            else:
                fields = struct.unpack(">8i", pkt[0:32])
                _, _, high_p, low_p, open_p, close_p, _change_p, exch_ts = fields

            high = high_p / 100.0
            low = low_p / 100.0
            open_ = open_p / 100.0
            close = close_p / 100.0
            if close:
                self._last_close[ntok] = close
            change = round(ltp - close, 4) if (ltp and close) else 0.0
            change_pct = round((ltp - close) / close * 100, 4) if close else 0.0
            ts_now = int(time.time())

            ltp_data = {
                "symbol": symbol, "exchange": exchange, "mode": "ltp",
                "ltp": ltp, "ltt": exch_ts or ts_now,
                "cp": close, "change": change, "change_percent": change_pct,
            }
            self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

            quote_data = {
                **ltp_data,
                "mode": "quote",
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                # Indices have no LTQ / volume / depth / OI — keep keys present
                # so downstream consumers don't KeyError.
                "ltq": 0,
                "average_price": 0.0,
                "volume": 0,
                "total_buy_quantity": 0,
                "total_sell_quantity": 0,
                "oi": 0,
            }
            if exch_ts:
                quote_data["exchange_timestamp"] = exch_ts
            self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)

            # 32-byte packets are sent when the user subscribed in `full` mode
            # for an index — emit a depth topic too, with empty ladder so the
            # subscriber's expected schema is preserved.
            if len(pkt) == 32:
                depth_data = {
                    **quote_data,
                    "mode": "full",
                    "depth": {"buy": [], "sell": []},
                }
                self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)
        except struct.error as e:
            logger.debug("Index packet parse error for %s: %s", symbol, e)

    # ---- Wire-format builders (JSON text frames per Kite docs) ----

    def _send_subscribe(self, tokens: list[int]) -> None:
        msg = json.dumps({"a": "subscribe", "v": tokens})
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Subscribe send error: %s", e)

    def _send_unsubscribe(self, tokens: list[int]) -> None:
        msg = json.dumps({"a": "unsubscribe", "v": tokens})
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Unsubscribe send error: %s", e)

    def _send_set_mode(self, tokens: list[int], mode_name: str) -> None:
        # Kite expects {"a":"mode","v":[mode_name,[tokens]]} — a 2-element
        # array where the second element is itself the token list.
        msg = json.dumps({"a": "mode", "v": [mode_name, tokens]})
        try:
            self._ws.send(msg)
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
