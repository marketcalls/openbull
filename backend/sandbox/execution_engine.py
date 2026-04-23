"""
Sandbox execution engine.

Two paths fill pending orders:

1. **Tick-driven** — a CRITICAL-priority subscriber on the shared
   :class:`MarketDataCache` receives every authenticated broker tick. For the
   symbol in that tick we iterate pending orders and fill any whose trigger
   condition is met.
2. **Polling fallback** — every 5 s (configurable) we scan all pending orders
   and try to fill them using whatever LTP the cache currently holds. This
   keeps LIMIT / SL orders progressing when the broker feed goes quiet.

Both paths call the same :func:`_try_fill_order` — one code path, two triggers.
Safe under thread concurrency because order_manager / position_manager /
fund_manager each open their own scoped session; each fill mutation is atomic.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.sandbox import order_manager, position_manager, fund_manager
from backend.sandbox.config import get_order_check_interval

logger = logging.getLogger(__name__)


# -- global state ---------------------------------------------------------

_running = False
_subscriber_id: int | None = None
_poll_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


# -- fill decision --------------------------------------------------------

def _fill_price(order, ltp: float) -> float | None:
    """Return the price this order should fill at given the current LTP,
    or ``None`` if the trigger condition is not met.

    Conventions (matching openalgo's sandbox):
      * MARKET  → fill at LTP immediately
      * LIMIT BUY  → fill when LTP <= limit price, execute at limit price
      * LIMIT SELL → fill when LTP >= limit price, execute at limit price
      * SL BUY   → trigger when LTP >= trigger_price, then behaves like a LIMIT BUY
      * SL SELL  → trigger when LTP <= trigger_price, then behaves like a LIMIT SELL
      * SL-M     → trigger + fill at LTP
    """
    if ltp <= 0:
        return None

    pt = order.pricetype.upper()
    action = order.action.upper()

    if pt == "MARKET":
        return ltp

    if pt == "LIMIT":
        if action == "BUY" and ltp <= order.price:
            return order.price
        if action == "SELL" and ltp >= order.price:
            return order.price
        return None

    if pt in ("SL", "SL-M"):
        # SL orders are trigger_pending until LTP crosses trigger.
        trigger = order.trigger_price
        triggered = (
            (action == "BUY" and ltp >= trigger) or
            (action == "SELL" and ltp <= trigger)
        )
        if not triggered:
            return None
        # Once triggered, SL fills at LIMIT price (if set), SL-M at LTP.
        if pt == "SL":
            if action == "BUY" and ltp <= order.price:
                return order.price
            if action == "SELL" and ltp >= order.price:
                return order.price
            return None
        return ltp  # SL-M

    return None


def _try_fill_order(order, ltp: float) -> bool:
    """Attempt to fill a single order at current LTP. Returns True if filled."""
    price = _fill_price(order, ltp)
    if price is None:
        return False

    # Atomic: mark filled + create trade
    filled_row, trade = order_manager.fill(order.user_id, order.orderid, price)
    if filled_row is None or trade is None:
        return False

    # Update position book
    realized = position_manager.apply_fill(
        user_id=order.user_id,
        symbol=order.symbol,
        exchange=order.exchange,
        product=order.product,
        action=order.action,
        quantity=trade.quantity,
        price=price,
    )

    # Fund movement:
    # - Release the margin blocked at order-entry
    # - Credit / debit realized PnL from the offset portion of the fill
    if order.margin_blocked:
        fund_manager.release_margin(order.user_id, order.margin_blocked)
    if realized:
        fund_manager.apply_realized_pnl(order.user_id, realized)

    logger.info(
        "sandbox: filled %s %s %s qty=%d @ %.2f (realized=%.2f)",
        order.orderid, order.action, order.symbol, trade.quantity, price, realized,
    )
    return True


# -- tick callback --------------------------------------------------------

def _on_tick(data: dict[str, Any]) -> None:
    """MarketDataCache CRITICAL subscriber. Runs on the broker tick thread."""
    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        payload = data.get("data") or {}
        ltp_val = payload.get("ltp")
        if not symbol or not exchange or not isinstance(ltp_val, (int, float)) or ltp_val <= 0:
            return

        # Pull only pending orders for this symbol. list_pending_orders returns
        # everything across users; we filter in memory — cheap because the set
        # of pending orders is small (seconds to minutes lifetime).
        for order in order_manager.list_pending_orders():
            if order.symbol != symbol or order.exchange != exchange:
                continue
            _try_fill_order(order, float(ltp_val))
    except Exception:
        logger.exception("sandbox tick handler failed")


# -- polling fallback -----------------------------------------------------

def _poll_loop() -> None:
    from backend.services.market_data_cache import get_market_data_cache

    mds = get_market_data_cache()
    while not _stop_event.is_set():
        try:
            interval = max(1, get_order_check_interval())
        except Exception:
            interval = 5
        try:
            pending = order_manager.list_pending_orders()
            for order in pending:
                ltp = mds.get_ltp_value(order.symbol, order.exchange)
                if ltp is None or ltp <= 0:
                    continue
                _try_fill_order(order, float(ltp))
        except Exception:
            logger.exception("sandbox poll iteration failed")
        _stop_event.wait(interval)


# -- lifecycle ------------------------------------------------------------

def start() -> None:
    """Call once at app startup. Idempotent."""
    global _running, _subscriber_id, _poll_thread
    with _lock:
        if _running:
            return
        _running = True
        _stop_event.clear()

        # Tick-driven path
        try:
            from backend.services.market_data_cache import (
                get_market_data_cache,
                SubscriberPriority,
            )

            mds = get_market_data_cache()
            _subscriber_id = mds.subscribe(
                priority=SubscriberPriority.CRITICAL,
                event_type="all",
                callback=_on_tick,
                filter_symbols=None,
                name="sandbox_execution_engine",
            )
            logger.info("sandbox execution engine subscribed (id=%s)", _subscriber_id)
        except Exception:
            logger.exception("sandbox: MDS subscribe failed — relying on polling only")

        # Polling fallback
        _poll_thread = threading.Thread(
            target=_poll_loop, name="sandbox-exec-poll", daemon=True
        )
        _poll_thread.start()


def stop() -> None:
    global _running, _subscriber_id, _poll_thread
    with _lock:
        if not _running:
            return
        _running = False
        _stop_event.set()

    # Unsubscribe (best-effort)
    try:
        from backend.services.market_data_cache import get_market_data_cache

        if _subscriber_id is not None:
            get_market_data_cache().unsubscribe(_subscriber_id)
    except Exception:
        pass
    _subscriber_id = None

    if _poll_thread is not None:
        _poll_thread.join(timeout=2.0)
    _poll_thread = None
