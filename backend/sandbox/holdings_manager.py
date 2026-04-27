"""
Sandbox holdings book.

Holdings live in ``sandbox_holdings`` and are populated by T+1 settlement
(:mod:`backend.sandbox.t1_settle`) when long CNC positions cross the EOD
boundary. Once settled they behave like real demat holdings: margin is no
longer locked against them (the cash is now "in" the asset), and they
mark-to-market against live LTP for the dashboard.

This module is read/refresh only — the settlement *write* path is in
``t1_settle``. Public surface is one function: :func:`get_holdings_for_user`,
which returns a broker-compatible holdings payload (matches the live
holdings_service shape) including refreshed LTP / unrealized PnL.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from backend.models.sandbox import SandboxHolding
from backend.sandbox._db import session_scope
from backend.sandbox.quote_helper import get_ltp as get_ltp_with_fallback

logger = logging.getLogger(__name__)


def _refresh_holding_mtm(user_id: int, h: SandboxHolding) -> None:
    """Refresh ltp / pnl / pnlpercent on a single row using the broker-fallback
    quote helper. Skipped silently if no real LTP is obtainable — the row's
    last real values stay so the UI never shows a fake zero."""
    ltp = get_ltp_with_fallback(user_id, h.symbol, h.exchange)
    if ltp is None or ltp <= 0:
        return
    h.ltp = round(float(ltp), 4)
    h.pnl = round((h.ltp - h.average_price) * h.quantity, 2)
    if h.average_price > 0:
        h.pnlpercent = round(((h.ltp - h.average_price) / h.average_price) * 100.0, 4)


def get_holdings_for_user(user_id: int) -> dict[str, Any]:
    """Return the broker-compatible holdings payload for ``user_id``. Shape
    mirrors what ``holdings_service`` returns for live mode so the same
    front-end component renders both."""
    holdings_list: list[dict[str, Any]] = []
    total_holding_value = 0.0
    total_inv_value = 0.0
    total_pnl = 0.0

    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxHolding).where(SandboxHolding.user_id == user_id)
            )
            .scalars()
            .all()
        )
        for h in rows:
            _refresh_holding_mtm(user_id, h)
            inv_value = round(h.average_price * h.quantity, 2)
            holding_value = round(h.ltp * h.quantity, 2) if h.ltp else inv_value
            total_inv_value += inv_value
            total_holding_value += holding_value
            total_pnl += float(h.pnl or 0.0)
            holdings_list.append(
                {
                    "symbol": h.symbol,
                    "exchange": h.exchange,
                    "quantity": int(h.quantity),
                    "product": "CNC",
                    "average_price": round(h.average_price, 2),
                    "averageprice": round(h.average_price, 2),
                    "ltp": round(h.ltp or 0.0, 2),
                    "pnl": round(float(h.pnl or 0.0), 2),
                    "pnlpercent": round(float(h.pnlpercent or 0.0), 2),
                    "settlement_date": h.settlement_date or "",
                }
            )

    pnl_pct = (
        round((total_pnl / total_inv_value) * 100.0, 2) if total_inv_value > 0 else 0.0
    )
    return {
        "holdings": holdings_list,
        "statistics": {
            "totalholdingvalue": round(total_holding_value, 2),
            "totalinvvalue": round(total_inv_value, 2),
            "totalprofitandloss": round(total_pnl, 2),
            "totalpnlpercentage": pnl_pct,
        },
    }
