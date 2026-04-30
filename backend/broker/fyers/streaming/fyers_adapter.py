"""
Fyers v3 streaming adapter — HSM (binary) protocol.

Replaces the legacy ``wss://api-t1.fyers.in/data?access_token=...`` JSON
endpoint (deprecated by Fyers; returns 404 from Cloudflare) with the v3
HSM data feed at ``wss://socket.fyers.in/hsm/v1-5/prod`` exposed by
:mod:`backend.broker.fyers.streaming.fyers_hsm_websocket`.

Architecture (mirrors openalgo's `broker/fyers/streaming/fyers_adapter.py`):

* :class:`FyersHSMWebSocket` owns the binary frame parsing, auth handshake,
  reconnect with backoff, and resubscribe-on-reconnect. We hand it
  per-message callbacks and let it run.
* :class:`FyersTokenConverter` resolves OpenBull ``(symbol, exchange)``
  pairs to Fyers HSM tokens (``sf|nse_cm|2885`` for the symbol feed,
  ``dp|...`` for depth, ``if|...`` for indices) by calling
  ``/data/symbol-token`` once per ``subscribe()`` batch.
* This adapter joins HSM tokens back to ``EXCHANGE:SYMBOL`` *through the
  broker symbol* (NOT positionally — Fyers' API does not preserve input
  order; openalgo commit ``5eb7baaa`` documents the bug). Inbound ticks
  are mapped to OpenBull's flat dict shape and published on
  ``{exchange}_{symbol}_LTP|QUOTE|DEPTH`` ZMQ topics.

Subscribe semantics:

* ``MODE_LTP`` / ``MODE_QUOTE`` -> ``"SymbolUpdate"`` HSM type (sf / if
  feeds carry ltp + ohlc + bid/ask + volume + oi).
* ``MODE_DEPTH`` -> two parallel HSM subscriptions: one ``"SymbolUpdate"``
  for the price/ohlc fields and one ``"DepthUpdate"`` for the 5-level
  buy/sell ladder. Without both, depth subscribers would have no LTP.

HSM has no per-symbol unsubscribe — :meth:`unsubscribe` drops local
client-mode tracking but keeps the upstream stream live. The WS proxy's
ZMQ filter layer (``backend/websocket_proxy/server.py``) takes care of
not delivering ticks the client no longer wants.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any

from backend.broker.fyers.streaming.fyers_hsm_websocket import FyersHSMWebSocket
from backend.broker.fyers.streaming.fyers_token_converter import (
    FyersTokenConverter,
    get_br_symbol,
)
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("fyers_stream")

# How long to wait for the HSM auth ack before giving up. Slightly longer
# than the HSM client's own 10s SSL timeout to leave room for retry.
_CONNECT_TIMEOUT_SECONDS = 15

# Exchanges where Fyers reports prices in paisa (multiply-divide by 100 on top
# of the per-tick multiplier). Indices are quoted directly so they're excluded.
_PAISA_EXCHANGES = {"BSE", "MCX", "NSE", "NFO", "BFO", "CDS", "BCD"}


class FyersAdapter(BaseBrokerAdapter):
    """Fyers v3 HSM streaming adapter."""

    def __init__(self, auth_token: str, broker_config: dict):
        """``auth_token`` is the combined ``"api_key:access_token"`` string set by
        :func:`backend.broker.fyers.api.auth_api.authenticate_broker`."""
        super().__init__(auth_token, broker_config)

        self._hsm: FyersHSMWebSocket | None = None
        self._converter: FyersTokenConverter | None = None
        self._connected = False
        self._connect_lock = threading.Lock()
        self._sub_lock = threading.Lock()

        # full_symbol ("EXCHANGE:SYMBOL") -> highest mode subscribed (1/2/3).
        self._mode_for: dict[str, int] = {}

        # Per-(symbol, exchange) input we last saw — used so we can resubscribe
        # on reconnect through the openbull side rather than the HSM-token side.
        # (HSM client itself remembers tokens; we remember inputs so a new
        # subscribe for the same symbol upgrades modes correctly.)
        self._inputs: dict[str, dict[str, str]] = {}  # full_symbol -> {symbol, exchange}

        # HSM token -> full_symbol. Built per subscribe() call by joining
        # through the broker symbol (NOT positionally — see module docstring).
        self._hsm_to_full: dict[str, str] = {}

    # ---- BaseBrokerAdapter interface --------------------------------

    def connect(self) -> None:
        with self._connect_lock:
            if self._connected:
                return
            try:
                self._hsm = FyersHSMWebSocket(access_token=self.auth_token)
            except ValueError as e:
                # Raised by HSM client when JWT can't be parsed / has expired.
                raise ConnectionError(f"Fyers HSM auth token unusable: {e}") from e

            self._converter = FyersTokenConverter(access_token=self.auth_token)

            self._hsm.set_callbacks(
                on_message=self._on_hsm_message,
                on_error=self._on_hsm_error,
                on_open=self._on_hsm_open,
                on_close=self._on_hsm_close,
            )

            self._hsm.connect()  # spawns its own background WS thread

            # Wait for the HSM client to finish its auth handshake.
            deadline = time.time() + _CONNECT_TIMEOUT_SECONDS
            while time.time() < deadline:
                if self._hsm.is_connected():
                    self._connected = True
                    self._running = True
                    logger.info("Fyers HSM WebSocket connected")
                    return
                time.sleep(0.1)

            # Couldn't connect — drop the partial state so the WS proxy can
            # surface a clean failure and the next attempt starts fresh.
            try:
                self._hsm.disconnect()
            except Exception:
                pass
            self._hsm = None
            raise ConnectionError("Fyers HSM WebSocket connection timed out")

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        if not self._hsm or not self._connected:
            logger.warning("Fyers HSM not connected; ignoring subscribe(%d symbols)", len(symbols))
            return
        if mode not in (MODE_LTP, MODE_QUOTE, MODE_DEPTH):
            logger.error("Unknown subscribe mode %s for fyers", mode)
            return

        # Track inputs + per-symbol mode (highest wins) before we hit the
        # converter so even invalid-symbol responses don't drop bookkeeping.
        valid_inputs: list[dict] = []
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                full = f"{exch}:{sym}"
                self._inputs[full] = {"symbol": sym, "exchange": exch}
                self._mode_for[full] = max(self._mode_for.get(full, 0), mode)
                valid_inputs.append({"symbol": sym, "exchange": exch})

        if not valid_inputs:
            return

        # MODE_DEPTH needs BOTH the symbol-update feed (LTP/OHLC) and the
        # depth-update feed; LTP/QUOTE only need the symbol-update feed.
        data_types = ["SymbolUpdate"]
        if mode == MODE_DEPTH:
            data_types.append("DepthUpdate")

        for data_type in data_types:
            self._subscribe_data_type(valid_inputs, data_type)

    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        # HSM has no selective unsub — the upstream stream stays live but we
        # drop local mode tracking so the WS proxy stops forwarding ticks
        # for unsubscribed symbols. Matches openalgo's behavior
        # (broker/fyers/streaming/fyers_adapter.py:310-317).
        with self._sub_lock:
            for item in symbols:
                sym = item.get("symbol")
                exch = item.get("exchange")
                if not sym or not exch:
                    continue
                full = f"{exch}:{sym}"
                self._mode_for.pop(full, None)
                self._inputs.pop(full, None)

    def disconnect(self) -> None:
        with self._connect_lock:
            self._connected = False
            self._running = False
            hsm = self._hsm
            self._hsm = None
            self._converter = None
        if hsm is not None:
            try:
                hsm.disconnect()
            except Exception:
                logger.exception("Error during Fyers HSM disconnect")
        with self._sub_lock:
            self._mode_for.clear()
            self._inputs.clear()
            self._hsm_to_full.clear()
        logger.info("Fyers adapter disconnected")

    # ---- subscribe helpers -----------------------------------------

    def _subscribe_data_type(self, inputs: list[dict], data_type: str) -> None:
        """Convert inputs -> HSM tokens and send one HSM SUBSCRIBE message."""
        try:
            hsm_tokens, token_mappings, invalid = (
                self._converter.convert_openalgo_symbols_to_hsm(inputs, data_type)
            )
        except Exception:
            logger.exception("Fyers symbol-to-HSM conversion failed for %s", data_type)
            return

        if invalid:
            logger.warning("Fyers invalid symbols (%s): %s", data_type, invalid)
        if not hsm_tokens:
            logger.warning("Fyers HSM produced no tokens for %s", data_type)
            return

        # Join HSM tokens to full_symbol THROUGH the broker symbol — the
        # /data/symbol-token API does not preserve input order in its
        # validSymbol map, so positional pairing scrambles ticks (openalgo
        # commit 5eb7baaa for the same bug + symptom write-up).
        brsymbol_to_full: dict[str, str] = {}
        for s in inputs:
            br = get_br_symbol(s["symbol"], s["exchange"])
            if br:
                brsymbol_to_full[br] = f"{s['exchange']}:{s['symbol']}"

        with self._sub_lock:
            for token in hsm_tokens:
                br = token_mappings.get(token)
                if not br:
                    continue
                full = brsymbol_to_full.get(br)
                if not full:
                    logger.warning(
                        "Fyers brsymbol %s did not match any input subscription", br
                    )
                    continue
                self._hsm_to_full[token] = full

        try:
            self._hsm.subscribe_symbols(hsm_tokens, token_mappings)
            logger.info(
                "Fyers HSM subscribed %d tokens (%s) — %d total mappings",
                len(hsm_tokens), data_type, len(self._hsm_to_full),
            )
        except Exception:
            logger.exception("Fyers HSM subscribe_symbols raised")

    # ---- HSM -> openbull mapping -----------------------------------

    def _on_hsm_message(self, fyers_data: dict[str, Any]) -> None:
        """Receive a parsed HSM tick (sf | dp | if) and fan out to ZMQ topics."""
        try:
            hsm_token = fyers_data.get("hsm_token")
            full = self._hsm_to_full.get(hsm_token) if hsm_token else None
            if not full:
                # Fyers occasionally sends snapshot frames before our mapping
                # is in place (HSM resubscribe race). Fall back to the
                # `original_symbol` carried in the frame; if that's absent or
                # not in our active set, drop the tick silently.
                orig = fyers_data.get("original_symbol")
                full = orig if orig in self._mode_for else None
                if not full:
                    return

            exchange, symbol = full.split(":", 1)
            highest_mode = self._mode_for.get(full, MODE_LTP)
            feed_type = fyers_data.get("type")

            if feed_type == "dp":
                # Depth feed — only matters when caller asked for DEPTH.
                if highest_mode >= MODE_DEPTH:
                    depth_payload = self._map_depth(fyers_data, symbol, exchange)
                    if depth_payload is not None:
                        self.publish(f"{exchange}_{symbol}_DEPTH", depth_payload)
                return

            # sf / if — symbol or index feed. Carries LTP/OHLC; emit LTP
            # always and QUOTE if the caller subscribed at >= MODE_QUOTE.
            ltp_payload = self._map_ltp(fyers_data, symbol, exchange)
            if ltp_payload is not None:
                self.publish(f"{exchange}_{symbol}_LTP", ltp_payload)

            if highest_mode >= MODE_QUOTE:
                quote_payload = self._map_quote(fyers_data, symbol, exchange)
                if quote_payload is not None:
                    self.publish(f"{exchange}_{symbol}_QUOTE", quote_payload)

        except Exception:
            logger.exception("Fyers HSM dispatch failed")

    def _on_hsm_open(self) -> None:
        logger.info("Fyers HSM WebSocket socket opened")

    def _on_hsm_close(self) -> None:
        logger.info("Fyers HSM WebSocket socket closed")
        self._connected = False

    def _on_hsm_error(self, error: Any) -> None:
        logger.error("Fyers HSM WebSocket error: %s", error)

    # ---- price helpers ---------------------------------------------

    @staticmethod
    def _segment_divisor(exchange: str, is_index: bool) -> int:
        """Fyers reports paisa for cash + F&O segments; indices are direct."""
        if is_index:
            return 1
        return 100 if exchange.upper() in _PAISA_EXCHANGES else 1

    @classmethod
    def _convert_price(
        cls, value: Any, multiplier: int, precision: int, segment_divisor: int
    ) -> float:
        if not value or multiplier <= 0:
            return 0.0
        return round(float(value) / multiplier / segment_divisor, precision)

    def _map_ltp(self, fyers_data: dict[str, Any], symbol: str, exchange: str) -> dict | None:
        if "ltp" not in fyers_data:
            return None
        is_index = fyers_data.get("type") == "if"
        multiplier = int(fyers_data.get("multiplier", 100) or 100)
        precision = int(fyers_data.get("precision", 2) or 2)
        seg = self._segment_divisor(exchange, is_index)
        ltp = self._convert_price(fyers_data.get("ltp", 0), multiplier, precision, seg)
        return {
            "symbol": symbol,
            "exchange": exchange,
            "ltp": ltp,
            "timestamp": int(time.time()),
            "data_type": "LTP",
        }

    def _map_quote(self, fyers_data: dict[str, Any], symbol: str, exchange: str) -> dict | None:
        is_index = fyers_data.get("type") == "if"
        multiplier = int(fyers_data.get("multiplier", 100) or 100)
        precision = int(fyers_data.get("precision", 2) or 2)
        seg = self._segment_divisor(exchange, is_index)

        def cp(v: Any) -> float:
            return self._convert_price(v, multiplier, precision, seg)

        return {
            "symbol": symbol,
            "exchange": exchange,
            "ltp": cp(fyers_data.get("ltp", 0)),
            "open": cp(fyers_data.get("open_price", 0)),
            "high": cp(fyers_data.get("high_price", 0)),
            "low": cp(fyers_data.get("low_price", 0)),
            "close": cp(fyers_data.get("prev_close_price", 0)),
            "prev_close": cp(fyers_data.get("prev_close_price", 0)),
            "bid": cp(fyers_data.get("bid_price", 0)),
            "ask": cp(fyers_data.get("ask_price", 0)),
            "bid_qty": int(fyers_data.get("bid_size", 0) or 0),
            "ask_qty": int(fyers_data.get("ask_size", 0) or 0),
            "volume": int(fyers_data.get("vol_traded_today", 0) or 0),
            "oi": int(fyers_data.get("OI", 0) or 0),
            "upper_circuit": cp(fyers_data.get("upper_ckt", 0)),
            "lower_circuit": cp(fyers_data.get("lower_ckt", 0)),
            "last_traded_time": int(fyers_data.get("last_traded_time", 0) or 0),
            "exchange_time": int(fyers_data.get("exch_feed_time", 0) or 0),
            "avg_trade_price": cp(fyers_data.get("avg_trade_price", 0)),
            "last_trade_quantity": int(fyers_data.get("last_traded_qty", 0) or 0),
            "total_buy_quantity": int(fyers_data.get("tot_buy_qty", 0) or 0),
            "total_sell_quantity": int(fyers_data.get("tot_sell_qty", 0) or 0),
            "timestamp": int(time.time()),
            "data_type": "Quote",
        }

    def _map_depth(self, fyers_data: dict[str, Any], symbol: str, exchange: str) -> dict | None:
        if fyers_data.get("type") != "dp":
            return None
        multiplier = int(fyers_data.get("multiplier", 100) or 100)
        precision = int(fyers_data.get("precision", 2) or 2)
        seg = self._segment_divisor(exchange, is_index=False)

        def cp(v: Any) -> float:
            return self._convert_price(v, multiplier, precision, seg)

        buy_levels: list[dict] = []
        sell_levels: list[dict] = []
        for i in range(1, 6):
            bid_price = cp(fyers_data.get(f"bid_price{i}", 0))
            bid_size = int(fyers_data.get(f"bid_size{i}", 0) or 0)
            bid_orders = int(fyers_data.get(f"bid_order{i}", 0) or 0)
            ask_price = cp(fyers_data.get(f"ask_price{i}", 0))
            ask_size = int(fyers_data.get(f"ask_size{i}", 0) or 0)
            ask_orders = int(fyers_data.get(f"ask_order{i}", 0) or 0)
            if bid_price > 0:
                buy_levels.append(
                    {"price": bid_price, "quantity": bid_size, "orders": bid_orders}
                )
            if ask_price > 0:
                sell_levels.append(
                    {"price": ask_price, "quantity": ask_size, "orders": ask_orders}
                )

        # Mid-price LTP fallback when the depth packet is the first thing
        # we get for a symbol — keeps the UI from showing 0 while sf/if
        # is still in flight.
        ltp = 0.0
        if buy_levels and sell_levels:
            ltp = round((buy_levels[0]["price"] + sell_levels[0]["price"]) / 2, precision)

        return {
            "symbol": symbol,
            "exchange": exchange,
            "ltp": ltp,
            "depth": {"buy": buy_levels, "sell": sell_levels},
            "timestamp": int(time.time()),
            "data_type": "Depth",
        }
