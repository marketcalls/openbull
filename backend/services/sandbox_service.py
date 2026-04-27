"""
Sandbox service layer.

All callers — the place-order service, orderbook service, etc. — branch to
this module when ``get_trading_mode() == "sandbox"``. Every function returns
the same ``(success, response_dict, status_code)`` tuple shape as its live
counterpart so the dispatch in ``backend/services/*.py`` is a one-line swap.

MARKET orders fill immediately using the latest LTP from the
:class:`MarketDataCache`. If no tick has arrived yet for that symbol the order
is accepted as ``open`` and the execution engine will fill it on the next tick
or poll cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.sandbox import fund_manager, holdings_manager, order_manager, position_manager
from backend.sandbox.execution_engine import _try_fill_order
from backend.sandbox.order_validation import validate_order
from backend.sandbox.quote_helper import get_ltp as get_ltp_with_fallback
from backend.sandbox.symbol_info import classify_from_symbol

logger = logging.getLogger(__name__)


# -- helpers ---------------------------------------------------------------

def _parse_int(v: Any, default: int = 0) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return default


def _parse_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return default


def _current_ltp(symbol: str, exchange: str) -> float | None:
    from backend.services.market_data_cache import get_market_data_cache

    return get_market_data_cache().get_ltp_value(symbol, exchange)


# -- order placement -------------------------------------------------------

def place_order(user_id: int, order_data: dict[str, Any]) -> tuple[bool, dict[str, Any], int]:
    """Sandbox equivalent of ``place_order_with_auth``."""
    symbol = order_data.get("symbol")
    exchange = order_data.get("exchange")
    action = (order_data.get("action") or "").upper()
    quantity = _parse_int(order_data.get("quantity"))
    pricetype = (order_data.get("pricetype") or "").upper()
    product = (order_data.get("product") or "").upper()
    price = _parse_float(order_data.get("price"))
    trigger_price = _parse_float(order_data.get("trigger_price"))
    strategy = order_data.get("strategy") or None

    # Validation gates (symbol exists, lot size, tick size, product/exchange
    # compatibility, CNC SELL inventory, post-squareoff block). Mirrors the
    # rejection rules openalgo enforces in its order_manager.
    ok, reason, info = validate_order(
        user_id=user_id,
        symbol=symbol,
        exchange=exchange,
        action=action,
        quantity=quantity,
        pricetype=pricetype,
        product=product,
        price=price,
        trigger_price=trigger_price,
    )
    if not ok:
        row = order_manager.create_order(
            user_id=user_id,
            symbol=symbol,
            exchange=exchange,
            action=action,
            quantity=quantity,
            pricetype=pricetype,
            product=product,
            price=price,
            trigger_price=trigger_price,
            strategy=strategy,
            margin_blocked=0.0,
            initial_status="rejected",
            rejection_reason=reason,
        )
        return False, {"status": "error", "message": reason, "orderid": row.orderid}, 400

    instrument_type = (
        info.instrument_type if info else ""
    ) or classify_from_symbol(symbol, exchange)

    # For margin blocking we need a reference price.
    #   * LIMIT / SL  → use the limit price (real, user-provided).
    #   * MARKET/SL-M → use a real LTP. We try the live tick cache first and
    #                   fall back to a broker quote API call. If both fail we
    #                   reject the order rather than silently using ``price=0``
    #                   (which would block 0 margin and accept an "order"
    #                   with no real price behind it). Same behaviour as
    #                   openalgo: no LTP → no fill, no acceptance.
    ref_price = price
    if pricetype in ("MARKET", "SL-M"):
        ltp = get_ltp_with_fallback(user_id, symbol, exchange)
        if not ltp or ltp <= 0:
            reason = (
                f"Cannot price {pricetype} order — no LTP available for "
                f"{symbol}/{exchange} (live tick cache empty and broker quote "
                f"fetch failed). Place a LIMIT order with an explicit price, "
                f"or retry once the symbol streams a tick."
            )
            row = order_manager.create_order(
                user_id=user_id,
                symbol=symbol,
                exchange=exchange,
                action=action,
                quantity=quantity,
                pricetype=pricetype,
                product=product,
                price=price,
                trigger_price=trigger_price,
                strategy=strategy,
                margin_blocked=0.0,
                initial_status="rejected",
                rejection_reason=reason,
            )
            return False, {"status": "error", "message": reason, "orderid": row.orderid}, 400
        ref_price = float(ltp)

    # Compute the *fresh* margin this order would require if it opened a new
    # position from flat. We then reduce it to ``actual_margin`` based on what
    # is already held in the position book — same logic as openalgo's sandbox:
    #   • opposite-direction order, qty <= existing qty  → block 0 (pure reduce)
    #   • opposite-direction order, qty  > existing qty  → block only the excess
    #   • same-direction or new position                  → block the full margin
    # This prevents the user from being charged twice for capital that's
    # already locked in the position they're closing.
    from backend.sandbox._db import session_scope

    with session_scope() as db:
        full_margin = fund_manager.compute_required_margin(
            db, ref_price, quantity, product,
            exchange=exchange, instrument_type=instrument_type, action=action,
        )

    actual_margin = full_margin
    snap = position_manager.get_position_snapshot(user_id, symbol, exchange, product)
    if snap is not None:
        existing_qty, _existing_margin = snap
        is_reducing = (
            (existing_qty > 0 and action == "SELL")
            or (existing_qty < 0 and action == "BUY")
        )
        if is_reducing:
            existing_abs = abs(existing_qty)
            if quantity <= existing_abs:
                actual_margin = 0.0
            else:
                excess_qty = quantity - existing_abs
                with session_scope() as db:
                    actual_margin = fund_manager.compute_required_margin(
                        db, ref_price, excess_qty, product,
                        exchange=exchange, instrument_type=instrument_type, action=action,
                    )

    margin = actual_margin
    blocked, err = fund_manager.block_margin(user_id, margin)
    if not blocked:
        # Still record a rejected order so the user can see it in the orderbook.
        row = order_manager.create_order(
            user_id=user_id,
            symbol=symbol,
            exchange=exchange,
            action=action,
            quantity=quantity,
            pricetype=pricetype,
            product=product,
            price=price,
            trigger_price=trigger_price,
            strategy=strategy,
            margin_blocked=0.0,
            initial_status="rejected",
            rejection_reason=err,
        )
        return False, {"status": "error", "message": err, "orderid": row.orderid}, 400

    # SL / SL-M orders sit at "trigger_pending" until the trigger fires.
    initial_status = "trigger_pending" if pricetype in ("SL", "SL-M") else "open"

    row = order_manager.create_order(
        user_id=user_id,
        symbol=symbol,
        exchange=exchange,
        action=action,
        quantity=quantity,
        pricetype=pricetype,
        product=product,
        price=price,
        trigger_price=trigger_price,
        strategy=strategy,
        margin_blocked=margin,
        initial_status=initial_status,
    )

    # Opportunistic immediate fill using whatever LTP we can find — cached
    # tick first, broker quote as fallback. This makes MARKET orders go
    # straight to ``complete`` even after market hours, matching openalgo's
    # behaviour where the simulator always fills MARKET at LTP if any LTP is
    # obtainable.
    ltp = get_ltp_with_fallback(user_id, symbol, exchange)
    if ltp is not None and ltp > 0:
        _try_fill_order(row, float(ltp))

    return True, {"status": "success", "orderid": row.orderid, "mode": "sandbox"}, 200


def modify_order(user_id: int, data: dict[str, Any]) -> tuple[bool, dict[str, Any], int]:
    orderid = data.get("orderid")
    if not orderid:
        return False, {"status": "error", "message": "orderid is required"}, 400

    updated = order_manager.modify_order(
        user_id=user_id,
        orderid=orderid,
        quantity=_parse_int(data.get("quantity")) or None,
        price=_parse_float(data.get("price")),
        trigger_price=_parse_float(data.get("trigger_price")),
        pricetype=data.get("pricetype"),
    )
    if updated is None:
        return False, {"status": "error", "message": "Order not found or not modifiable"}, 404
    return True, {"status": "success", "orderid": updated.orderid, "mode": "sandbox"}, 200


def cancel_order(user_id: int, orderid: str) -> tuple[bool, dict[str, Any], int]:
    cancelled = order_manager.cancel_order(user_id, orderid)
    if cancelled is None:
        return False, {"status": "error", "message": "Order not found or not cancellable"}, 404
    # Release the margin we were holding against this order.
    if cancelled.margin_blocked:
        fund_manager.release_margin(user_id, cancelled.margin_blocked)
    return True, {"status": "success", "orderid": orderid, "mode": "sandbox"}, 200


def cancel_all_orders(user_id: int) -> tuple[bool, dict[str, Any], int]:
    cancelled: list[str] = []
    for row in order_manager.list_cancellable_orders(user_id):
        c = order_manager.cancel_order(user_id, row.orderid)
        if c is not None:
            cancelled.append(c.orderid)
            if c.margin_blocked:
                fund_manager.release_margin(user_id, c.margin_blocked)
    return True, {"status": "success", "cancelled": cancelled, "mode": "sandbox"}, 200


def close_all_positions(user_id: int) -> tuple[bool, dict[str, Any], int]:
    """Place opposite MARKET orders to flatten every open position."""
    closed: list[str] = []
    for pos in position_manager.get_positions(user_id):
        qty = pos.get("netqty", 0) or 0
        if qty == 0:
            continue
        action = "SELL" if qty > 0 else "BUY"
        _, resp, _ = place_order(
            user_id,
            {
                "symbol": pos["symbol"],
                "exchange": pos["exchange"],
                "action": action,
                "quantity": abs(qty),
                "pricetype": "MARKET",
                "product": pos["product"],
                "price": 0,
                "trigger_price": 0,
                "strategy": "close_all",
            },
        )
        if resp.get("orderid"):
            closed.append(resp["orderid"])
    return True, {"status": "success", "closed": closed, "mode": "sandbox"}, 200


# -- reads -----------------------------------------------------------------

def get_orderbook(user_id: int) -> tuple[bool, dict[str, Any], int]:
    orders = [order_manager.to_dict(o) for o in order_manager.list_orders(user_id)]

    # Shape matches what orderbook_service returns for live mode.
    total = len(orders)
    complete = sum(1 for o in orders if o["order_status"] == "complete")
    open_ = sum(1 for o in orders if o["order_status"] in ("open", "trigger_pending"))
    rejected = sum(1 for o in orders if o["order_status"] == "rejected")
    cancelled = sum(1 for o in orders if o["order_status"] == "cancelled")

    return (
        True,
        {
            "status": "success",
            "mode": "sandbox",
            "data": {
                "orders": orders,
                "statistics": {
                    "total_orders": total,
                    "total_completed_orders": complete,
                    "total_open_orders": open_,
                    "total_rejected_orders": rejected,
                    "total_cancelled_orders": cancelled,
                    "total_buy_orders": sum(1 for o in orders if o["action"] == "BUY"),
                    "total_sell_orders": sum(1 for o in orders if o["action"] == "SELL"),
                },
            },
        },
        200,
    )


def get_tradebook(user_id: int) -> tuple[bool, dict[str, Any], int]:
    trades = [order_manager.trade_to_dict(t) for t in order_manager.list_trades(user_id)]
    return True, {"status": "success", "mode": "sandbox", "data": trades}, 200


def get_positions(user_id: int) -> tuple[bool, dict[str, Any], int]:
    positions = position_manager.get_positions(user_id)
    return True, {"status": "success", "mode": "sandbox", "data": positions}, 200


def get_holdings(user_id: int) -> tuple[bool, dict[str, Any], int]:
    """Return T+1-settled holdings + refreshed MTM. Empty until the EOD
    settlement scheduler has moved CNC longs across — see
    :mod:`backend.sandbox.t1_settle`."""
    payload = holdings_manager.get_holdings_for_user(user_id)
    return True, {"status": "success", "mode": "sandbox", "data": payload}, 200


def get_order_status(user_id: int, orderid: str) -> tuple[bool, dict[str, Any], int]:
    """Return a single sandbox order in the broker-compatible shape used by
    ``orderbook_service.get_order_status``. Matches openalgo's
    ``sandbox_get_order_status`` API surface."""
    row = order_manager.get_order(user_id, orderid)
    if row is None:
        return False, {"status": "error", "message": "Order not found"}, 404
    return (
        True,
        {"status": "success", "mode": "sandbox", "data": order_manager.to_dict(row)},
        200,
    )


def close_position(
    user_id: int, symbol: str, exchange: str, product: str
) -> tuple[bool, dict[str, Any], int]:
    """Submit an opposite MARKET order to flatten one specific position.

    No-op (returns success with ``orderid=None``) when the position is
    already flat or doesn't exist. Margin / realized PnL bookkeeping flows
    through the normal place_order → fill path, so this is just a
    convenience wrapper that picks the right action + qty.
    """
    snap = position_manager.get_position_snapshot(user_id, symbol, exchange, product)
    if snap is None:
        return True, {"status": "success", "orderid": None, "message": "no open position"}, 200
    qty, _ = snap
    if qty == 0:
        return True, {"status": "success", "orderid": None, "message": "position is flat"}, 200
    action = "SELL" if qty > 0 else "BUY"
    return place_order(
        user_id,
        {
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "quantity": abs(qty),
            "pricetype": "MARKET",
            "product": product,
            "price": 0,
            "trigger_price": 0,
            "strategy": "close_position",
        },
    )


def place_smart_order(
    user_id: int, order_data: dict[str, Any]
) -> tuple[bool, dict[str, Any], int]:
    """Adjust a position to a target quantity.

    Reads ``position_size`` from the order payload (signed: positive long,
    negative short, 0 flat), compares it against the current net position,
    and submits the delta as a MARKET order. Mirrors openalgo's
    ``sandbox_place_smart_order``.
    """
    symbol = order_data.get("symbol")
    exchange = order_data.get("exchange")
    product = (order_data.get("product") or "MIS").upper()
    try:
        target = int(str(order_data.get("position_size", 0)))
    except (TypeError, ValueError):
        return False, {"status": "error", "message": "position_size must be an integer"}, 400

    snap = position_manager.get_position_snapshot(user_id, symbol, exchange, product)
    current = snap[0] if snap is not None else 0
    delta = target - current
    if delta == 0:
        return True, {"status": "success", "orderid": None, "message": "already at target"}, 200

    action = "BUY" if delta > 0 else "SELL"
    return place_order(
        user_id,
        {
            **order_data,
            "action": action,
            "quantity": abs(delta),
            "pricetype": (order_data.get("pricetype") or "MARKET").upper(),
            "product": product,
        },
    )


def get_funds(user_id: int) -> tuple[bool, dict[str, Any], int]:
    return (
        True,
        {
            "status": "success",
            "mode": "sandbox",
            "data": fund_manager.get_funds_snapshot(user_id),
        },
        200,
    )


