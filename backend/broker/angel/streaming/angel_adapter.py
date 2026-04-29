"""
Angel One SmartAPI streaming adapter.

Connects to Angel's SmartStream WebSocket, parses binary tick packets, and
publishes normalized market data on a ZMQ PUB socket. Modeled after the
Zerodha adapter (sync ``websocket-client`` running in a daemon thread,
threading.Lock for state, exponential reconnect, health-check loop).

Wire protocol (Angel SmartAPI v2):
  Outbound JSON control frames:
    {"correlationID": <id>, "action": 1, "params": {"mode": <m>, "tokenList": [...]}}
    {"correlationID": <id>, "action": 0, "params": ...}
  Inbound binary tick packets:
    Mode 1 (LTP): 51 bytes
    Mode 2 (QUOTE): 123 bytes
    Mode 3 (SNAP_QUOTE / DEPTH): 379 bytes (with 5-level depth)
"""

import json
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
    MODE_QUOTE,
)

logger = logging.getLogger("angel_stream")

ANGEL_WS_URL = "wss://smartapisocket.angelone.in/smart-stream"

# Angel exchange-type codes used in subscribe payloads.
_ANGEL_EXCHANGE_TYPE = {
    "NSE": 1,
    "NFO": 2,
    "BSE": 3,
    "BFO": 4,
    "MCX": 5,
    "NCX": 7,
    "CDS": 13,
    "NSE_INDEX": 1,
    "BSE_INDEX": 3,
    "MCX_INDEX": 5,
}

# Angel SmartStream subscription modes.
_ANGEL_LTP = 1
_ANGEL_QUOTE = 2
_ANGEL_SNAP_QUOTE = 3  # Snap-quote includes 5-level depth.

# Map our integer modes (LTP/QUOTE/DEPTH) onto Angel's wire modes.
_OUR_TO_ANGEL_MODE = {
    MODE_LTP: _ANGEL_LTP,
    MODE_QUOTE: _ANGEL_QUOTE,
    MODE_DEPTH: _ANGEL_SNAP_QUOTE,
}

# Reconnect / health-check tuning.
RECONNECT_MAX_TRIES = 50
RECONNECT_MAX_DELAY = 60
HEARTBEAT_INTERVAL = 10
DATA_STALL_TIMEOUT = 90  # seconds without data before we force a reconnect.
HEALTH_CHECK_INTERVAL = 30


def _split_token(auth_token: str) -> tuple[str, str, str]:
    """Split combined ``api_key:jwt_token:feed_token`` issued by auth_api."""
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return "", auth_token or "", ""


def _parse_token_str(binary_packet: bytes) -> str:
    """Read the 25-byte token field as an ASCII string up to first NUL."""
    out = []
    for byte in binary_packet:
        if byte == 0:
            break
        out.append(chr(byte))
    return "".join(out)


class AngelAdapter(BaseBrokerAdapter):
    """Angel One SmartAPI streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)

        api_key, jwt_token, feed_token = _split_token(auth_token)
        # Allow broker_config to override pieces (e.g. on token refresh).
        self._api_key = broker_config.get("api_key") or api_key
        self._jwt_token = jwt_token
        self._feed_token = broker_config.get("feed_token") or feed_token
        self._client_code = broker_config.get("client_code") or broker_config.get("clientcode") or ""

        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None

        self._connected = False
        self._last_msg_time: float | None = None

        # Subscription bookkeeping.
        # _subscriptions: keyed by (symbol, exchange) -> {"token", "exchange_type", "mode"}
        self._subscriptions: dict[tuple[str, str], dict] = {}
        # token (str) -> (symbol, exchange) — used in tick parsing.
        self._token_to_se: dict[str, tuple[str, str]] = {}
        # last close for change/change_percent computation.
        self._last_close: dict[str, float] = {}

        self._sub_lock = threading.Lock()
        self._reconnect_reset_signal = False

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        if not self._jwt_token or not self._api_key or not self._feed_token:
            raise ConnectionError(
                "Angel WS connect requires api_key, jwt_token and feed_token; "
                "ensure the auth flow stored all three (combined token)."
            )

        headers = [
            f"Authorization: {self._jwt_token}",
            f"x-api-key: {self._api_key}",
            f"x-client-code: {self._client_code}",
            f"x-feed-token: {self._feed_token}",
        ]

        self._running = True
        self._ws = websocket.WebSocketApp(
            ANGEL_WS_URL,
            header=headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_data=self._on_data,
        )

        self._ws_thread = threading.Thread(
            target=self._run_ws, daemon=True, name="angel-ws"
        )
        self._ws_thread.start()

        # Block briefly for the handshake.
        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError("Angel SmartStream connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        angel_mode = _OUR_TO_ANGEL_MODE.get(mode)
        if angel_mode is None:
            logger.error("Invalid Angel mode %s; expected one of %s",
                         mode, list(_OUR_TO_ANGEL_MODE))
            return

        # Group new tokens by Angel exchange-type so we can issue one frame
        # per exchange (Angel allows mixed in tokenList but grouping is cleaner).
        new_by_exch: dict[int, list[str]] = {}
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                token_str = get_token_from_cache(sym, exch)
                if not token_str:
                    logger.warning("Angel subscribe: token not found for %s/%s", sym, exch)
                    continue

                ang_exch = _ANGEL_EXCHANGE_TYPE.get(exch)
                if ang_exch is None:
                    logger.warning("Angel subscribe: unsupported exchange %s for %s", exch, sym)
                    continue

                key = (sym, exch)
                self._subscriptions[key] = {
                    "token": token_str,
                    "exchange_type": ang_exch,
                    "mode": angel_mode,
                    "our_mode": mode,
                }
                self._token_to_se[token_str] = (sym, exch)
                new_by_exch.setdefault(ang_exch, []).append(token_str)

        if not new_by_exch:
            return

        if not self._connected:
            # Pending subs will be sent on (re)connect via _on_open.
            logger.debug("Angel subscribe queued — WS not connected yet")
            return

        token_list = [
            {"exchangeType": exch, "tokens": tokens}
            for exch, tokens in new_by_exch.items()
        ]
        self._send_subscribe(angel_mode, token_list)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        angel_mode = _OUR_TO_ANGEL_MODE.get(mode, _ANGEL_QUOTE)

        by_exch: dict[int, list[str]] = {}
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                key = (sym, exch)
                sub = self._subscriptions.pop(key, None)
                if not sub:
                    continue
                self._token_to_se.pop(sub["token"], None)
                by_exch.setdefault(sub["exchange_type"], []).append(sub["token"])

        if not by_exch or not self._connected:
            return

        token_list = [
            {"exchangeType": exch, "tokens": tokens}
            for exch, tokens in by_exch.items()
        ]
        self._send_unsubscribe(angel_mode, token_list)

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        with self._sub_lock:
            self._subscriptions.clear()
            self._token_to_se.clear()
        logger.info("Angel adapter disconnected")

    # ---- WS lifecycle ----

    def _run_ws(self) -> None:
        reconnect_attempts = 0
        while self._running:
            self._reconnect_reset_signal = False
            try:
                self._ws.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_payload="ping",
                )
            except Exception as e:
                logger.error("Angel WS run_forever error: %s", e)

            self._connected = False
            if not self._running:
                break

            if self._reconnect_reset_signal:
                reconnect_attempts = 0

            reconnect_attempts += 1
            if reconnect_attempts > RECONNECT_MAX_TRIES:
                logger.error("Angel max reconnect attempts (%d) reached", RECONNECT_MAX_TRIES)
                break

            delay = min(2 * (1.5 ** reconnect_attempts), RECONNECT_MAX_DELAY)
            logger.info("Angel reconnecting in %.1fs (attempt %d)", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Angel SmartStream connected")
        self._connected = True
        self._last_msg_time = time.time()
        self._reconnect_reset_signal = True
        self._start_health_check()

        # Re-subscribe everything we know about, grouped by (mode, exchange_type).
        with self._sub_lock:
            grouped: dict[tuple[int, int], list[str]] = {}
            for sub in self._subscriptions.values():
                grouped.setdefault((sub["mode"], sub["exchange_type"]), []).append(sub["token"])

        for (mode, exch_type), tokens in grouped.items():
            token_list = [{"exchangeType": exch_type, "tokens": tokens}]
            try:
                self._send_subscribe(mode, token_list)
                time.sleep(0.2)
            except Exception as e:
                logger.error("Angel resubscribe failed (mode=%s exch=%s): %s", mode, exch_type, e)

        if grouped:
            total = sum(len(v) for v in grouped.values())
            logger.info("Angel re-subscribed %d tokens after connect", total)

    def _on_message(self, ws, message) -> None:
        self._last_msg_time = time.time()
        # Text frames are control / pong; only log.
        if isinstance(message, str):
            if message != "pong":
                logger.debug("Angel WS text: %s", message[:200])
            return
        # Binary tick frames are dispatched via _on_data.

    def _on_data(self, ws, data, data_type, continue_flag) -> None:
        self._last_msg_time = time.time()
        # data_type 2 = binary frames per websocket-client conventions.
        if data_type == 2 and isinstance(data, (bytes, bytearray)):
            try:
                self._parse_and_publish(bytes(data))
            except Exception as e:
                logger.error("Angel binary parse error: %s", e)

    def _on_error(self, ws, error) -> None:
        logger.error("Angel WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Angel WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Wire-format builders ----

    def _send_subscribe(self, mode: int, token_list: list[dict]) -> None:
        msg = json.dumps({
            "correlationID": "openbull-angel",
            "action": 1,
            "params": {"mode": mode, "tokenList": token_list},
        })
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Angel subscribe send error: %s", e)

    def _send_unsubscribe(self, mode: int, token_list: list[dict]) -> None:
        msg = json.dumps({
            "correlationID": "openbull-angel",
            "action": 0,
            "params": {"mode": mode, "tokenList": token_list},
        })
        try:
            self._ws.send(msg)
        except Exception as e:
            logger.error("Angel unsubscribe send error: %s", e)

    # ---- Binary tick parsing (Angel SmartAPI v2 layout) ----

    def _parse_and_publish(self, pkt: bytes) -> None:
        if len(pkt) < 51:
            return

        sub_mode = pkt[0]
        # exchange_type = pkt[1]
        token_str = _parse_token_str(pkt[2:27])
        # sequence = struct.unpack("<q", pkt[27:35])[0]
        exchange_ts = struct.unpack("<q", pkt[35:43])[0]
        ltp_paise = struct.unpack("<q", pkt[43:51])[0]
        ltp = ltp_paise / 100.0

        # Resolve symbol from cache fallback if our local map misses.
        info = self._token_to_se.get(token_str)
        if not info:
            info = get_symbol_exchange_from_token(token_str)
        if not info:
            return
        symbol, exchange = info

        cp = self._last_close.get(token_str, 0.0)
        change = round(ltp - cp, 4) if (ltp and cp) else 0.0
        change_pct = round((ltp - cp) / cp * 100, 4) if cp else 0.0

        ltp_data = {
            "symbol": symbol, "exchange": exchange, "mode": "ltp",
            "ltp": ltp,
            "ltt": int(exchange_ts / 1000) if exchange_ts else int(time.time()),
            "cp": cp, "change": change, "change_percent": change_pct,
        }
        self.publish(f"{exchange}_{symbol}_LTP", ltp_data)

        quote_data: dict | None = None

        # QUOTE / SNAP_QUOTE share the layout from offset 51..123.
        if sub_mode in (_ANGEL_QUOTE, _ANGEL_SNAP_QUOTE) and len(pkt) >= 123:
            try:
                ltq = struct.unpack("<q", pkt[51:59])[0]
                avg_paise = struct.unpack("<q", pkt[59:67])[0]
                volume = struct.unpack("<q", pkt[67:75])[0]
                tot_buy_qty = struct.unpack("<d", pkt[75:83])[0]
                tot_sell_qty = struct.unpack("<d", pkt[83:91])[0]
                open_paise = struct.unpack("<q", pkt[91:99])[0]
                high_paise = struct.unpack("<q", pkt[99:107])[0]
                low_paise = struct.unpack("<q", pkt[107:115])[0]
                close_paise = struct.unpack("<q", pkt[115:123])[0]

                close = close_paise / 100.0
                if close:
                    self._last_close[token_str] = close
                q_change = round(ltp - close, 4) if (ltp and close) else 0.0
                q_change_pct = round((ltp - close) / close * 100, 4) if close else 0.0

                quote_data = {
                    "symbol": symbol, "exchange": exchange, "mode": "quote",
                    "ltp": ltp,
                    "ltq": ltq,
                    "average_price": avg_paise / 100.0,
                    "volume": volume,
                    "total_buy_quantity": tot_buy_qty,
                    "total_sell_quantity": tot_sell_qty,
                    "open": open_paise / 100.0,
                    "high": high_paise / 100.0,
                    "low": low_paise / 100.0,
                    "close": close,
                    "cp": close,
                    "change": q_change,
                    "change_percent": q_change_pct,
                    "oi": 0,
                    "ltt": int(exchange_ts / 1000) if exchange_ts else int(time.time()),
                }
                self.publish(f"{exchange}_{symbol}_QUOTE", quote_data)
            except struct.error as e:
                logger.debug("Angel quote parse error for %s: %s", symbol, e)

        # SNAP_QUOTE adds last_traded_time, OI and 5-level depth at 147..347.
        if sub_mode == _ANGEL_SNAP_QUOTE and len(pkt) >= 379 and quote_data is not None:
            try:
                last_trade_ts = struct.unpack("<q", pkt[123:131])[0]
                oi = struct.unpack("<q", pkt[131:139])[0]
                # oi_change_pct = struct.unpack("<q", pkt[139:147])[0]
                upper_circuit = struct.unpack("<q", pkt[347:355])[0]
                lower_circuit = struct.unpack("<q", pkt[355:363])[0]
                wk52_high = struct.unpack("<q", pkt[363:371])[0]
                wk52_low = struct.unpack("<q", pkt[371:379])[0]

                quote_data["oi"] = oi
                quote_data["last_traded_time"] = last_trade_ts
                quote_data["upper_circuit"] = upper_circuit / 100.0
                quote_data["lower_circuit"] = lower_circuit / 100.0
                quote_data["wk52_high"] = wk52_high / 100.0
                quote_data["wk52_low"] = wk52_low / 100.0

                # 10 entries x 20 bytes from offset 147..347. Per Angel docs,
                # buy/sell are interleaved by a flag in the first 2 bytes.
                bids: list[dict] = []
                asks: list[dict] = []
                for i in range(10):
                    off = 147 + i * 20
                    flag = struct.unpack("<H", pkt[off:off + 2])[0]
                    qty = struct.unpack("<q", pkt[off + 2:off + 10])[0]
                    price_paise = struct.unpack("<q", pkt[off + 10:off + 18])[0]
                    orders = struct.unpack("<H", pkt[off + 18:off + 20])[0]
                    entry = {
                        "price": price_paise / 100.0,
                        "quantity": qty,
                        "orders": orders,
                    }
                    if flag == 0:
                        bids.append(entry)
                    else:
                        asks.append(entry)

                # Pad to 5 entries each so consumers see a stable shape.
                while len(bids) < 5:
                    bids.append({"price": 0.0, "quantity": 0, "orders": 0})
                while len(asks) < 5:
                    asks.append({"price": 0.0, "quantity": 0, "orders": 0})
                bids = bids[:5]
                asks = asks[:5]

                depth_data = {
                    **quote_data,
                    "mode": "full",
                    "depth": {"buy": bids, "sell": asks},
                }
                self.publish(f"{exchange}_{symbol}_DEPTH", depth_data)
            except struct.error as e:
                logger.debug("Angel depth parse error for %s: %s", symbol, e)

    # ---- Health check ----

    def _start_health_check(self) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._health_loop, daemon=True, name="angel-health"
        )
        self._health_thread.start()

    def _health_loop(self) -> None:
        while self._running and self._connected:
            time.sleep(HEALTH_CHECK_INTERVAL)
            if not self._running or not self._connected:
                break
            if self._last_msg_time and (time.time() - self._last_msg_time) > DATA_STALL_TIMEOUT:
                logger.error(
                    "Angel data stall (>%ds). Forcing reconnect.", DATA_STALL_TIMEOUT
                )
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                break
