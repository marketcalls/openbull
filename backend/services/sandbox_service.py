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

from backend.sandbox import fund_manager, order_manager, position_manager
from backend.sandbox.execution_engine import _try_fill_order

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

    if quantity <= 0:
        return False, {"status": "error", "message": "quantity must be > 0"}, 400

    # For margin blocking we need a reference price. MARKET/SL-M use LTP; LIMIT/SL
    # use the limit price. If LTP is absent we block against ``price`` if set,
    # else assume 0 (no margin — acceptable for simulation).
    ref_price = price
    if pricetype in ("MARKET", "SL-M"):
        ltp = _current_ltp(symbol, exchange)
        ref_price = ltp if (ltp and ltp > 0) else price

    margin = fund_manager.compute_required_margin(
        db=_UnboundDb(), price=ref_price, quantity=quantity, product=product
    ) if False else 0.0  # avoid double-session; we compute inside a scoped call below
    # Compute margin in a scoped session to avoid opening two nested ones.
    from backend.sandbox._db import session_scope

    with session_scope() as db:
        margin = fund_manager.compute_required_margin(db, ref_price, quantity, product)

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

    # Opportunistic immediate fill using cached LTP. If the tick stream is live
    # the MARKET order fills right here; LIMIT / SL fill only if LTP already
    # matches the trigger. Otherwise the execution engine picks them up later.
    ltp = _current_ltp(symbol, exchange)
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
    """Phase 2a: sandbox holdings are always empty (T+1 settlement ships in 2b)."""
    return (
        True,
        {
            "status": "success",
            "mode": "sandbox",
            "data": {
                "holdings": [],
                "statistics": {
                    "totalholdingvalue": 0.0,
                    "totalinvvalue": 0.0,
                    "totalprofitandloss": 0.0,
                    "totalpnlpercentage": 0.0,
                },
            },
        },
        200,
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


class _UnboundDb:
    """Sentinel placeholder — never actually used (kept for readable guard above)."""
