"""
Dhan WebSocket streaming adapter (5-level depth).

Implements the openbull BaseBrokerAdapter contract. Connects to Dhan's
binary market data feed (`wss://api-feed.dhan.co`), parses ticker/quote/full
packets, and publishes normalized data on the ZeroMQ PUB socket.

20-level depth is NOT included in this baseline port — only the 5-level
feed used by the standard openbull MODE_LTP / MODE_QUOTE / MODE_DEPTH
contract. The 20-level endpoint is a Dhan-specific premium feature that
requires a second connection and is not part of the openbull contract.
"""

import json
import logging
import struct
import threading
import time
from collections import defaultdict
from urllib.parse import urlencode

import websocket

from backend.broker.upstox.mapping.order_data import get_symbol_exchange_from_token
from backend.broker.dhan.streaming.dhan_mapping import (
    DhanCapabilityRegistry,
    DhanExchangeMapper,
)
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("dhan_stream")

# Dhan request codes (5-depth feed)
_REQUEST_CODES = {
    "TICKER": 15,
    "QUOTE": 17,
    "FULL": 21,
    "DISCONNECT": 12,
}

# Map openbull mode -> Dhan subscribe verb
_OPENBULL_TO_DHAN_MODE = {
    MODE_LTP: "TICKER",
    MODE_QUOTE: "QUOTE",
    MODE_DEPTH: "FULL",
}

# Reconnect / batching
RECONNECT_MAX_TRIES = 50
RECONNECT_MAX_DELAY = 60
MAX_INSTRUMENTS_PER_REQUEST = 100


def _strip_token_suffix(token: str) -> str:
    return str(token).split("::::")[0] if "::::" in str(token) else str(token)


class DhanAdapter(BaseBrokerAdapter):
    """Dhan 5-depth streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        super().__init__(auth_token, broker_config)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._connected = False
        self._client_id: str | None = self._extract_client_id()
        # Per-instrument subscription state.
        # key: (exchange_segment_str, security_id_str) -> (mode_int, symbol, exchange)
        self._subs: dict[tuple[str, str], tuple[int, str, str]] = {}
        self._subs_lock = threading.Lock()

    def _extract_client_id(self) -> str | None:
        cfg = self.broker_config or {}
        cid = cfg.get("client_id") or cfg.get("dhan_client_id")
        if cid:
            return str(cid)
        api_key = cfg.get("api_key") or ""
        if ":::" in api_key:
            head, _, _ = api_key.partition(":::")
            return head or None
        return None

    # ---- BaseBrokerAdapter interface ----

    def connect(self) -> None:
        if not self._client_id:
            raise ConnectionError(
                "Dhan client_id missing. Provide config['client_id'] or "
                "use api_key='client_id:::app_key'."
            )

        params = {
            "version": "2",
            "token": self.auth_token,
            "clientId": self._client_id,
            "authType": "2",
        }
        ws_url = f"wss://api-feed.dhan.co?{urlencode(params)}"

        self._running = True
        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True, name="dhan-ws")
        self._ws_thread.start()

        for _ in range(100):
            if self._connected:
                return
            time.sleep(0.1)
        if not self._connected:
            raise ConnectionError("Dhan WebSocket connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        if mode not in _OPENBULL_TO_DHAN_MODE:
            logger.error("Invalid Dhan mode %s; expected one of %s", mode, list(_OPENBULL_TO_DHAN_MODE))
            return

        # Group instruments by (segment, mode) for batched subscribe
        groups: dict[str, list[dict]] = defaultdict(list)

        with self._subs_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                from backend.broker.upstox.mapping.order_data import get_token_from_cache
                token = get_token_from_cache(sym, exch)
                if not token:
                    logger.debug("Skipping %s/%s: token not in cache", sym, exch)
                    continue
                security_id = _strip_token_suffix(token)
                dhan_segment = DhanExchangeMapper.get_dhan_exchange(exch)
                if not dhan_segment:
                    logger.debug("Skipping %s/%s: unsupported exchange", sym, exch)
                    continue
                key = (dhan_segment, security_id)
                current = self._subs.get(key, (0, sym, exch))[0]
                # Upgrade subscription mode if requested mode is higher
                new_mode = max(current, mode)
                self._subs[key] = (new_mode, sym, exch)
                groups[_OPENBULL_TO_DHAN_MODE[mode]].append(
                    {"ExchangeSegment": dhan_segment, "SecurityId": security_id}
                )

        if not self._connected:
            return  # _on_open will resubscribe everything

        for dhan_mode, instruments in groups.items():
            self._send_subscribe(instruments, dhan_mode)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        # Dhan has no real unsubscribe; we just drop local tracking so
        # _on_data ignores any further packets for these instruments.
        with self._subs_lock:
            from backend.broker.upstox.mapping.order_data import get_token_from_cache
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                token = get_token_from_cache(sym, exch)
                if not token:
                    continue
                security_id = _strip_token_suffix(token)
                dhan_segment = DhanExchangeMapper.get_dhan_exchange(exch)
                if not dhan_segment:
                    continue
                self._subs.pop((dhan_segment, security_id), None)

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                self._ws.send(json.dumps({"RequestCode": _REQUEST_CODES["DISCONNECT"]}))
            except Exception:
                pass
            try:
                self._ws.close()
            except Exception:
                pass
        with self._subs_lock:
            self._subs.clear()
        logger.info("Dhan adapter disconnected")

    # ---- WS internals ----

    def _run_ws(self) -> None:
        reconnect_attempts = 0
        while self._running:
            try:
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error("Dhan WS run_forever error: %s", e)

            self._connected = False
            if not self._running:
                break

            reconnect_attempts += 1
            if reconnect_attempts > RECONNECT_MAX_TRIES:
                logger.error("Dhan WS max reconnect attempts (%d) reached", RECONNECT_MAX_TRIES)
                break
            delay = min(2 * (1.5 ** reconnect_attempts), RECONNECT_MAX_DELAY)
            logger.info("Reconnecting Dhan WS in %.1fs (attempt %d)...", delay, reconnect_attempts)
            time.sleep(delay)

    def _on_open(self, ws) -> None:
        logger.info("Dhan WS connected")
        self._connected = True

        # Resubscribe everything on (re)connect, grouped by (segment, mode)
        groups: dict[str, list[dict]] = defaultdict(list)
        with self._subs_lock:
            for (segment, security_id), (mode, _, _) in self._subs.items():
                dhan_mode = _OPENBULL_TO_DHAN_MODE.get(mode)
                if not dhan_mode:
                    continue
                groups[dhan_mode].append({"ExchangeSegment": segment, "SecurityId": security_id})

        for dhan_mode, instruments in groups.items():
            self._send_subscribe(instruments, dhan_mode)

    def _on_message(self, ws, message) -> None:
        if isinstance(message, (bytes, bytearray)) and len(message) >= 8:
            try:
                self._parse_binary(bytes(message))
            except Exception:
                logger.exception("Error parsing Dhan binary message")

    def _on_error(self, ws, error) -> None:
        logger.error("Dhan WS error: %s", error)
        self._connected = False

    def _on_close(self, ws, code, msg) -> None:
        logger.info("Dhan WS closed (code=%s, msg=%s)", code, msg)
        self._connected = False

    # ---- Subscription wire ----

    def _send_subscribe(self, instruments: list[dict], dhan_mode: str) -> None:
        if not instruments:
            return
        request_code = _REQUEST_CODES.get(dhan_mode)
        if not request_code:
            return
        # Batch into chunks of MAX_INSTRUMENTS_PER_REQUEST
        for i in range(0, len(instruments), MAX_INSTRUMENTS_PER_REQUEST):
            batch = instruments[i : i + MAX_INSTRUMENTS_PER_REQUEST]
            msg = {
                "RequestCode": request_code,
                "InstrumentCount": len(batch),
                "InstrumentList": batch,
            }
            try:
                self._ws.send(json.dumps(msg))
                logger.debug("Subscribed batch of %d in %s mode", len(batch), dhan_mode)
            except Exception as e:
                logger.error("Dhan subscribe send failed: %s", e)

    # ---- Binary parsing ----

    def _parse_binary(self, data: bytes) -> None:
        """Parse one or more Dhan packets from a binary frame.

        Header layout (8 bytes, little-endian):
            uint8  feed_response_code
            uint16 message_length
            uint8  exchange_segment
            uint32 security_id
        """
        offset = 0
        n = len(data)
        while offset + 8 <= n:
            feed_code = data[offset]
            msg_len = struct.unpack("<H", data[offset + 1 : offset + 3])[0]
            segment_code = data[offset + 3]
            security_id = struct.unpack("<I", data[offset + 4 : offset + 8])[0]

            payload_start = offset + 8
            payload_end = offset + msg_len
            if payload_end > n:
                break
            payload = data[payload_start:payload_end]

            if feed_code == 0:
                # Heartbeat / acknowledgement — ignore
                offset = payload_end
                continue

            self._handle_packet(feed_code, segment_code, security_id, payload)
            offset = payload_end

    def _resolve_symbol(self, segment_code: int, security_id: int) -> tuple[str, str] | None:
        """Find (symbol, exchange) for a tick. Prefer the local _subs map (we
        registered the segment ourselves). Fall back to symtoken cache."""
        # Try every known segment string for this numeric code (IDX_I covers
        # both NSE_INDEX and BSE_INDEX). We just need a (segment_str, sec_id)
        # key match in _subs.
        sec_id_str = str(security_id)
        with self._subs_lock:
            for (seg, sid), (_, sym, exch) in self._subs.items():
                if sid == sec_id_str:
                    # Best-effort: confirm the segment numeric matches
                    expected = DhanExchangeMapper.get_segment_from_exchange(exch)
                    if expected is None or expected == segment_code:
                        return sym, exch

        # Fallback: shared symtoken cache by token id
        info = get_symbol_exchange_from_token(sec_id_str)
        if info:
            return info
        return None

    def _handle_packet(
        self, feed_code: int, segment_code: int, security_id: int, payload: bytes,
    ) -> None:
        resolved = self._resolve_symbol(segment_code, security_id)
        if not resolved:
            return
        symbol, exchange = resolved

        if feed_code == 2:  # TICKER
            if len(payload) < 8:
                return
            ltp = struct.unpack("<f", payload[0:4])[0]
            ltt = struct.unpack("<I", payload[4:8])[0]
            self.publish(f"{exchange}_{symbol}_LTP", {
                "symbol": symbol, "exchange": exchange, "mode": "ltp",
                "ltp": float(ltp), "ltt": int(ltt),
            })

        elif feed_code == 4:  # QUOTE
            if len(payload) < 42:
                return
            ltp = struct.unpack("<f", payload[0:4])[0]
            ltq = struct.unpack("<H", payload[4:6])[0]
            ltt = struct.unpack("<I", payload[6:10])[0]
            atp = struct.unpack("<f", payload[10:14])[0]
            volume = struct.unpack("<I", payload[14:18])[0]
            tot_sell = struct.unpack("<I", payload[18:22])[0]
            tot_buy = struct.unpack("<I", payload[22:26])[0]
            open_p = struct.unpack("<f", payload[26:30])[0]
            close_p = struct.unpack("<f", payload[30:34])[0]
            high_p = struct.unpack("<f", payload[34:38])[0]
            low_p = struct.unpack("<f", payload[38:42])[0]

            change = round(ltp - close_p, 4) if (ltp and close_p) else 0.0
            change_pct = round((ltp - close_p) / close_p * 100, 4) if close_p else 0.0

            self.publish(f"{exchange}_{symbol}_LTP", {
                "symbol": symbol, "exchange": exchange, "mode": "ltp",
                "ltp": float(ltp), "ltt": int(ltt),
                "cp": float(close_p), "change": change, "change_percent": change_pct,
            })
            self.publish(f"{exchange}_{symbol}_QUOTE", {
                "symbol": symbol, "exchange": exchange, "mode": "quote",
                "ltp": float(ltp),
                "ltq": int(ltq),
                "ltt": int(ltt),
                "average_price": float(atp),
                "volume": int(volume),
                "total_buy_quantity": int(tot_buy),
                "total_sell_quantity": int(tot_sell),
                "open": float(open_p),
                "high": float(high_p),
                "low": float(low_p),
                "close": float(close_p),
                "cp": float(close_p),
                "change": change,
                "change_percent": change_pct,
                "oi": 0,
            })

        elif feed_code == 8:  # FULL (includes 5-level depth)
            if len(payload) < 154:
                return
            ltp = struct.unpack("<f", payload[0:4])[0]
            ltq = struct.unpack("<H", payload[4:6])[0]
            ltt = struct.unpack("<I", payload[6:10])[0]
            atp = struct.unpack("<f", payload[10:14])[0]
            volume = struct.unpack("<I", payload[14:18])[0]
            tot_sell = struct.unpack("<I", payload[18:22])[0]
            tot_buy = struct.unpack("<I", payload[22:26])[0]
            oi = struct.unpack("<I", payload[26:30])[0]
            oi_high = struct.unpack("<I", payload[30:34])[0]
            oi_low = struct.unpack("<I", payload[34:38])[0]
            open_p = struct.unpack("<f", payload[38:42])[0]
            close_p = struct.unpack("<f", payload[42:46])[0]
            high_p = struct.unpack("<f", payload[46:50])[0]
            low_p = struct.unpack("<f", payload[50:54])[0]

            bids: list[dict] = []
            asks: list[dict] = []
            depth_offset = 54
            for i in range(5):
                base = depth_offset + (i * 20)
                bid_qty = struct.unpack("<I", payload[base : base + 4])[0]
                ask_qty = struct.unpack("<I", payload[base + 4 : base + 8])[0]
                bid_orders = struct.unpack("<H", payload[base + 8 : base + 10])[0]
                ask_orders = struct.unpack("<H", payload[base + 10 : base + 12])[0]
                bid_price = struct.unpack("<f", payload[base + 12 : base + 16])[0]
                ask_price = struct.unpack("<f", payload[base + 16 : base + 20])[0]
                bids.append({"price": float(bid_price), "quantity": int(bid_qty), "orders": int(bid_orders)})
                asks.append({"price": float(ask_price), "quantity": int(ask_qty), "orders": int(ask_orders)})

            change = round(ltp - close_p, 4) if (ltp and close_p) else 0.0
            change_pct = round((ltp - close_p) / close_p * 100, 4) if close_p else 0.0

            quote_data = {
                "symbol": symbol, "exchange": exchange,
                "ltp": float(ltp),
                "ltq": int(ltq),
                "ltt": int(ltt),
                "average_price": float(atp),
                "volume": int(volume),
                "total_buy_quantity": int(tot_buy),
                "total_sell_quantity": int(tot_sell),
                "open": float(open_p),
                "high": float(high_p),
                "low": float(low_p),
                "close": float(close_p),
                "cp": float(close_p),
                "change": change,
                "change_percent": change_pct,
                "oi": int(oi),
                "oi_high": int(oi_high),
                "oi_low": int(oi_low),
            }

            self.publish(f"{exchange}_{symbol}_LTP", {
                **quote_data, "mode": "ltp",
            })
            self.publish(f"{exchange}_{symbol}_QUOTE", {
                **quote_data, "mode": "quote",
            })
            self.publish(f"{exchange}_{symbol}_DEPTH", {
                **quote_data, "mode": "full",
                "depth": {"buy": bids, "sell": asks},
            })

        # Codes 5 (OI), 6 (PREV_CLOSE), 50 (DISCONNECT) — no openbull topic mapping
