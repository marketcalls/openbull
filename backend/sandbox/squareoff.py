"""
Auto square-off of MIS intraday positions at configured exchange cut-off times.

For each user with an open MIS position, this module submits a MARKET order
in the opposite direction via :mod:`backend.services.sandbox_service`. The
execution engine's tick path / poll fallback fills the order at LTP on the
next tick.

One function per exchange group is exposed so the scheduler can call them
independently at different IST cut-offs (NSE/NFO/BSE/BFO 15:15,
CDS 16:45, MCX 23:30 by default).
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from backend.models.sandbox import SandboxOrder, SandboxPosition
from backend.sandbox import fund_manager
from backend.sandbox._db import session_scope
from backend.services import sandbox_service

logger = logging.getLogger(__name__)


# Exchange → bucket mapping. Same groups openalgo uses.
EXCHANGE_BUCKETS: dict[str, list[str]] = {
    "nse_nfo_bse_bfo": ["NSE", "NFO", "BSE", "BFO"],
    "cds": ["CDS", "BCD"],
    "mcx": ["MCX"],
}


def _cancel_pending_mis_orders(exchanges: Iterable[str]) -> int:
    """Cancel MIS limit/SL orders that are still ``open`` or
    ``trigger_pending`` at squareoff time, releasing each one's blocked
    margin. Mirrors openalgo's ``_cancel_open_mis_orders`` — without it
    a stale MIS limit could fill in the next session and silently open a
    fresh intraday position the user never authorised.
    """
    exchanges = list(exchanges)
    cancelled = 0
    margin_releases: dict[int, float] = {}

    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxOrder).where(
                    SandboxOrder.product == "MIS",
                    SandboxOrder.status.in_(("open", "trigger_pending")),
                    SandboxOrder.exchange.in_(exchanges),
                )
            )
            .scalars()
            .all()
        )
        for o in rows:
            margin = float(o.margin_blocked or 0.0)
            if margin > 0:
                margin_releases[o.user_id] = margin_releases.get(o.user_id, 0.0) + margin
            o.status = "cancelled"
            o.rejection_reason = "Auto-cancelled at MIS squareoff"
            o.margin_blocked = 0.0
            cancelled += 1

    for uid, amount in margin_releases.items():
        try:
            fund_manager.release_margin(uid, amount, realized_pnl=0.0)
        except Exception:
            logger.exception(
                "sandbox squareoff: release_margin failed for user=%d amount=%.2f",
                uid, amount,
            )

    if cancelled:
        logger.info(
            "sandbox squareoff: cancelled %d pending MIS order(s) on %s",
            cancelled, exchanges,
        )
    return cancelled


def _open_mis_positions(exchanges: Iterable[str]) -> list[dict]:
    """Return a list of ``{user_id, symbol, exchange, product, net_quantity}``
    for every non-zero MIS position whose exchange is in the given list."""
    exchanges = list(exchanges)
    rows: list[dict] = []
    with session_scope() as db:
        q = db.execute(
            select(
                SandboxPosition.user_id,
                SandboxPosition.symbol,
                SandboxPosition.exchange,
                SandboxPosition.product,
                SandboxPosition.net_quantity,
            ).where(
                SandboxPosition.product == "MIS",
                SandboxPosition.net_quantity != 0,
                SandboxPosition.exchange.in_(exchanges),
            )
        )
        for user_id, symbol, exchange, product, net_qty in q.all():
            rows.append(
                {
                    "user_id": user_id,
                    "symbol": symbol,
                    "exchange": exchange,
                    "product": product,
                    "net_quantity": int(net_qty),
                }
            )
    return rows


def squareoff_bucket(bucket_key: str) -> int:
    """Square off every MIS position in the given exchange bucket.

    Two-step: (1) cancel any pending MIS limit/SL orders in the bucket
    so they can't fill in a future session and (2) place reverse market
    orders against open MIS positions. Returns the number of reverse
    orders placed.
    """
    exchanges = EXCHANGE_BUCKETS.get(bucket_key)
    if not exchanges:
        return 0

    # Step 1: cancel pending MIS orders + release their margin.
    try:
        _cancel_pending_mis_orders(exchanges)
    except Exception:
        logger.exception("sandbox squareoff: cancel-pending step failed for %s", bucket_key)

    # Step 2: reverse open MIS positions.
    positions = _open_mis_positions(exchanges)
    if not positions:
        return 0

    placed = 0
    for p in positions:
        qty = p["net_quantity"]
        if qty == 0:
            continue
        action = "SELL" if qty > 0 else "BUY"
        order = {
            "symbol": p["symbol"],
            "exchange": p["exchange"],
            "action": action,
            "quantity": abs(qty),
            "pricetype": "MARKET",
            "product": "MIS",
            "price": 0,
            "trigger_price": 0,
            "strategy": "auto_squareoff",
        }
        try:
            ok, resp, status = sandbox_service.place_order(p["user_id"], order)
            if ok:
                placed += 1
                logger.info(
                    "sandbox squareoff: user=%d %s %s qty=%d -> %s",
                    p["user_id"], action, p["symbol"], abs(qty),
                    resp.get("orderid"),
                )
            else:
                logger.warning(
                    "sandbox squareoff failed: user=%d %s %s: %s (HTTP %d)",
                    p["user_id"], action, p["symbol"], resp.get("message"), status,
                )
        except Exception:
            logger.exception(
                "sandbox squareoff threw for user=%d %s %s",
                p["user_id"], action, p["symbol"],
            )
    return placed
