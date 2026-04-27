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
    """Attempt to fill a single order at current LTP. Returns True if filled.

    Fund movement on fill follows openalgo's transfer-on-fill model:

    * If the fill **adds** to (or opens) a position, the order's
      ``margin_blocked`` is moved from the order row into the position row.
      Nothing leaves ``used_margin`` — the cash is still locked, only the
      bucket changes (handled inside ``position_manager.apply_fill``).
    * If the fill **reduces or closes** a position, ``apply_fill`` returns
      the pro-rata margin slice that should be released; we hand it to
      ``release_margin`` together with the realized PnL so cash and PnL
      land on the funds row in a single transaction.

    The per-user lock in ``fund_manager`` keeps concurrent ticks for the
    same user serialized — every helper here that mutates the funds row
    acquires that lock and releases it on exit (including on exception)
    via the context-manager pattern.
    """
    price = _fill_price(order, ltp)
    if price is None:
        return False

    # Atomic: mark filled + create trade
    filled_row, trade = order_manager.fill(order.user_id, order.orderid, price)
    if filled_row is None or trade is None:
        return False

    # Update position book — also handles the order→position margin transfer
    # for the same-direction path. Returns the slice of margin to release for
    # the opposite-direction (reduce/close/reverse) path.
    realized, margin_to_release = position_manager.apply_fill(
        user_id=order.user_id,
        symbol=order.symbol,
        exchange=order.exchange,
        product=order.product,
        action=order.action,
        quantity=trade.quantity,
        price=price,
        order_margin=float(order.margin_blocked or 0.0),
    )

    # Fund movement: release pro-rata position margin + book realized PnL
    # in a single transaction. Pure same-direction fills hit neither branch
    # because the margin already lives on the position row.
    if margin_to_release > 0 or realized:
        fund_manager.release_margin(
            order.user_id, float(margin_to_release), float(realized)
        )

    logger.info(
        "sandbox: filled %s %s %s qty=%d @ %.2f (realized=%.2f, released=%.2f)",
        order.orderid, order.action, order.symbol, trade.quantity, price,
        realized, margin_to_release,
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
    """Fallback poll loop. Tries cached LTP first; falls back to a broker
    quote keyed off the order's user. Without the broker fallback, MARKET
    orders placed after market hours (when no tick stream is running) would
    sit at ``open`` forever — same bug openalgo solved with its multiquotes
    fallback in the execution engine."""
    from backend.sandbox.quote_helper import get_ltp as get_ltp_with_fallback

    while not _stop_event.is_set():
        try:
            interval = max(1, get_order_check_interval())
        except Exception:
            interval = 5
        try:
            pending = order_manager.list_pending_orders()
            for order in pending:
                ltp = get_ltp_with_fallback(order.user_id, order.symbol, order.exchange)
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
