"""
Pre-placement validation gates for sandbox orders.

A single ``validate_order`` call returns ``(ok, reason)`` — one place to keep
all the rejection rules openalgo enforces:

* Symbol exists in the symbol master.
* Price > 0 for LIMIT / SL.
* Trigger price > 0 for SL / SL-M.
* Quantity is a positive multiple of the F&O lot size.
* Price is a positive multiple of the tick size (LIMIT / SL only).
* Product / exchange combination is legal (CNC only on equity, etc).
* CNC SELL only against existing holdings + intraday buys.
* Post-squareoff order block — MIS orders are rejected after the configured
  cut-off time **unless** they reduce an existing position. The block lifts
  again at 09:00 IST the next day.

Inputs are kept primitive (strings + floats) so the same gate is reusable
from ``place_order`` and ``modify_order``. The squareoff-time check needs the
position book and the config table; everything else is symbol-master-local.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from backend.models.sandbox import SandboxConfig, SandboxHolding, SandboxPosition
from backend.sandbox._db import session_scope
from backend.sandbox.symbol_info import (
    EQUITY_EXCHANGES,
    SymbolInfo,
    classify_from_symbol,
    get_symbol_info,
    is_product_exchange_compatible,
)

logger = logging.getLogger(__name__)


IST = timezone(timedelta(hours=5, minutes=30))

# Exchange → squareoff config key. Mirrors the EXCHANGE_BUCKETS map in
# squareoff.py so the post-squareoff block uses the same cut-off times the
# scheduler already runs.
SQUAREOFF_CFG_KEY: dict[str, str] = {
    "NSE": "squareoff_nse_nfo_bse_bfo",
    "NFO": "squareoff_nse_nfo_bse_bfo",
    "BSE": "squareoff_nse_nfo_bse_bfo",
    "BFO": "squareoff_nse_nfo_bse_bfo",
    "CDS": "squareoff_cds",
    "BCD": "squareoff_cds",
    "MCX": "squareoff_mcx",
}

DEFAULT_SQUAREOFF: dict[str, str] = {
    "squareoff_nse_nfo_bse_bfo": "15:15",
    "squareoff_cds": "16:45",
    "squareoff_mcx": "23:30",
}


def _read_squareoff_time(exchange: str) -> tuple[int, int] | None:
    """Returns ``(hour, minute)`` or ``None`` if exchange has no squareoff."""
    key = SQUAREOFF_CFG_KEY.get(exchange.upper())
    if key is None:
        return None
    with session_scope() as db:
        row = db.execute(
            select(SandboxConfig).where(SandboxConfig.key == key)
        ).scalar_one_or_none()
        raw = row.value if row else DEFAULT_SQUAREOFF[key]
    try:
        hh, mm = raw.strip().split(":", 1)
        return int(hh), int(mm)
    except Exception:
        return None


def _is_multiple(value: float, step: float, tol: float = 1e-6) -> bool:
    """Float-safe ``value % step == 0``."""
    if step <= 0:
        return True  # nothing to enforce
    n = round(value / step)
    return abs(n * step - value) <= tol


def _existing_long_qty(user_id: int, symbol: str, exchange: str) -> int:
    """Net long quantity available for a CNC SELL — sums the user's holding
    (settled delivery) and the same-day intraday CNC long position. Matches
    openalgo's "you must own these shares" rule."""
    with session_scope() as db:
        holding_qty = db.execute(
            select(SandboxHolding.quantity).where(
                SandboxHolding.user_id == user_id,
                SandboxHolding.symbol == symbol,
                SandboxHolding.exchange == exchange,
            )
        ).scalar() or 0
        position_qty = db.execute(
            select(SandboxPosition.net_quantity).where(
                SandboxPosition.user_id == user_id,
                SandboxPosition.symbol == symbol,
                SandboxPosition.exchange == exchange,
                SandboxPosition.product == "CNC",
            )
        ).scalar() or 0
    return int(holding_qty) + max(0, int(position_qty))


def _is_reducing(user_id: int, symbol: str, exchange: str, product: str, action: str) -> bool:
    """``True`` when (action) is opposite the existing (product) position. Used
    by the squareoff-time block to allow exits even after cut-off."""
    with session_scope() as db:
        net = db.execute(
            select(SandboxPosition.net_quantity).where(
                SandboxPosition.user_id == user_id,
                SandboxPosition.symbol == symbol,
                SandboxPosition.exchange == exchange,
                SandboxPosition.product == product,
            )
        ).scalar()
    if net is None or int(net) == 0:
        return False
    return (int(net) > 0 and action == "SELL") or (int(net) < 0 and action == "BUY")


def is_after_squareoff(exchange: str, now: datetime | None = None) -> bool:
    """``True`` between the squareoff cut-off and 09:00 IST next day. Crypto
    and unknown exchanges return ``False`` (always tradable). Currency cut-off
    of 16:45 means the block window for CDS is 16:45 → 09:00 next day, etc."""
    parsed = _read_squareoff_time(exchange)
    if parsed is None:
        return False
    hh, mm = parsed
    now = now or datetime.now(tz=IST)
    cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    # Window is [cutoff, next_day_market_open). MCX cut-off is 23:30 so the
    # block effectively lasts 9.5 hours; CDS/NCDEX cut-off is afternoon so the
    # block lasts overnight; equity cut-off 15:15 → block 17h 45m.
    if hh >= 9:
        return now >= cutoff or now < market_open
    return now < market_open and now >= cutoff


def validate_order(
    *,
    user_id: int,
    symbol: str,
    exchange: str,
    action: str,
    quantity: int,
    pricetype: str,
    product: str,
    price: float,
    trigger_price: float,
) -> tuple[bool, str, SymbolInfo | None]:
    """Run every pre-placement gate. Returns
    ``(ok, rejection_reason, symbol_info_or_None)``.

    ``symbol_info`` is returned alongside the verdict so the caller doesn't
    have to look it up twice — it's needed for instrument-aware margin sizing
    after the validation gate passes.
    """
    action = (action or "").upper()
    pricetype = (pricetype or "").upper()
    product = (product or "").upper()
    exchange = (exchange or "").upper()

    if action not in ("BUY", "SELL"):
        return False, "action must be BUY or SELL", None
    if pricetype not in ("MARKET", "LIMIT", "SL", "SL-M"):
        return False, f"unsupported pricetype: {pricetype}", None
    if product not in ("MIS", "NRML", "CNC"):
        return False, f"unsupported product: {product}", None
    if quantity <= 0:
        return False, "quantity must be > 0", None

    if not is_product_exchange_compatible(product, exchange):
        return False, (
            f"product {product} not supported on {exchange} "
            f"(CNC: NSE/BSE only; NRML: derivatives only)"
        ), None

    info = get_symbol_info(symbol, exchange)
    if info is None:
        return False, f"unknown symbol {symbol} on {exchange}", None

    instrument_type = info.instrument_type or classify_from_symbol(symbol, exchange)

    is_fno = exchange not in EQUITY_EXCHANGES
    if is_fno and info.lot_size > 1:
        if quantity % info.lot_size != 0:
            return False, (
                f"F&O quantity must be a multiple of lot size "
                f"({info.lot_size}); got {quantity}"
            ), info

    if pricetype in ("LIMIT", "SL"):
        if price <= 0:
            return False, f"price must be > 0 for {pricetype} orders", info
        if info.tick_size > 0 and not _is_multiple(price, info.tick_size):
            return False, (
                f"price {price} is not a multiple of tick size {info.tick_size}"
            ), info
    if pricetype in ("SL", "SL-M"):
        if trigger_price <= 0:
            return False, f"trigger_price must be > 0 for {pricetype} orders", info
        if info.tick_size > 0 and not _is_multiple(trigger_price, info.tick_size):
            return False, (
                f"trigger_price {trigger_price} is not a multiple of tick size "
                f"{info.tick_size}"
            ), info

    # CNC SELL must be against existing inventory (holdings + same-day CNC longs).
    # Real exchanges reject naked short-deliveries; we follow openalgo's rule.
    if product == "CNC" and action == "SELL":
        owned = _existing_long_qty(user_id, symbol, exchange)
        if quantity > owned:
            return False, (
                f"CNC SELL of {quantity} {symbol} exceeds owned quantity {owned} "
                f"(holdings + intraday CNC longs)"
            ), info

    # MIS post-squareoff block. Reducing exits stay open.
    if product == "MIS" and is_after_squareoff(exchange):
        if not _is_reducing(user_id, symbol, exchange, product, action):
            return False, (
                f"MIS orders for {exchange} are blocked after squareoff time "
                f"(open positions can still be reduced; new entries resume at 09:00 IST)"
            ), info

    return True, "", info
