"""
Startup catch-up tasks for the sandbox scheduler.

The polling scheduler fires once per minute and only acts when the wall
clock has crossed a configured time *and* the job hasn't already run today.
That breaks down if the app was *down* at the scheduled minute — the job's
last-run bookkeeping never advances, so it would silently re-run at the
next scheduled minute (potentially much later) without recovering missed
work.

This module is what openalgo calls ``catch_up_processor``. It runs once at
startup and:

* Closes intraday MIS positions that survived past their bucket's squareoff
  cut-off — the position book is liquidated at last-known LTP and margin is
  released. Today's realized-PnL is *not* touched (yesterday's closes don't
  contaminate today's session bucket).
* Settles long CNC positions whose ``updated_at`` is from a previous
  trading day, moving them into holdings with margin transfer.
* Zeroes ``today_realized_pnl`` on funds + positions whose row is older
  than the last session boundary.
* Auto-closes expired F&O contracts (options at 0, futures at last LTP).
* Best-effort: backfills daily P&L snapshots for missed days.

Idempotent. Safe to run on every startup.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update

from backend.models.sandbox import (
    SandboxFund,
    SandboxPosition,
)
from backend.sandbox import fund_manager, t1_settle, daily_reset, pnl_snapshot
from backend.sandbox._db import session_scope
from backend.sandbox.quote_helper import get_ltp as get_ltp_with_fallback
from backend.sandbox.symbol_info import classify_from_symbol

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _today_start_ist() -> datetime:
    n = datetime.now(tz=IST)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def _close_stale_mis_positions() -> int:
    """Settle any MIS position whose row is from before today.

    Long positions are closed at the position's stored LTP if available,
    else at the average price (no PnL). Realized PnL flows into the
    *cumulative* ``realized_pnl`` bucket only — ``today_realized_pnl`` is
    never bumped from a yesterday-flavoured close. Margin is released
    through the normal :func:`fund_manager.release_margin` path.
    """
    today_start = _today_start_ist()
    closed = 0
    margin_releases: dict[int, tuple[float, float]] = {}  # uid -> (margin, realized)

    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxPosition).where(
                    SandboxPosition.product == "MIS",
                    SandboxPosition.net_quantity != 0,
                    SandboxPosition.updated_at < today_start,
                )
            )
            .scalars()
            .all()
        )
        for pos in rows:
            ltp = get_ltp_with_fallback(pos.user_id, pos.symbol, pos.exchange)
            close_price = float(ltp) if (ltp and ltp > 0) else float(pos.average_price or 0.0)
            qty = pos.net_quantity
            if qty > 0:
                realized = (close_price - pos.average_price) * qty
            else:
                realized = (pos.average_price - close_price) * abs(qty)
            margin = float(pos.margin_blocked or 0.0)

            pos.realized_pnl = round(float(pos.realized_pnl or 0.0) + realized, 4)
            pos.net_quantity = 0
            pos.average_price = 0.0
            pos.margin_blocked = 0.0
            pos.unrealized_pnl = 0.0
            pos.pnl = round(pos.realized_pnl, 4)

            agg = margin_releases.get(pos.user_id, (0.0, 0.0))
            margin_releases[pos.user_id] = (
                round(agg[0] + margin, 2),
                round(agg[1] + realized, 4),
            )
            closed += 1

    # Release margin / book realized PnL — but keep realized out of
    # today_realized_pnl by *not* using release_margin's combined call. We
    # add the cumulative ``realized_pnl`` and ``available`` directly via a
    # split: release the margin (no PnL), then bump cumulative realized
    # without touching today's bucket.
    for uid, (margin, realized) in margin_releases.items():
        try:
            if margin > 0:
                fund_manager.release_margin(uid, margin, realized_pnl=0.0)
            if realized:
                # Bypass today_realized_pnl — yesterday's close doesn't
                # belong to today's session metric. We commit straight to
                # ``realized_pnl`` + ``available``.
                with session_scope() as db:
                    funds = fund_manager.ensure_fund_row(db, uid)
                    funds.realized_pnl += realized
                    funds.available += realized
        except Exception:
            logger.exception("catch_up: stale-MIS close failed for user %d", uid)

    if closed:
        logger.info("catch_up: closed %d stale MIS positions", closed)
    return closed


def _settle_stale_cnc_positions() -> int:
    """Run T+1 settlement for CNC longs whose row is from before today."""
    today_start = _today_start_ist()
    has_stale = False
    with session_scope() as db:
        any_row = db.execute(
            select(SandboxPosition.id).where(
                SandboxPosition.product == "CNC",
                SandboxPosition.net_quantity > 0,
                SandboxPosition.updated_at < today_start,
            ).limit(1)
        ).scalar_one_or_none()
        has_stale = any_row is not None
    if not has_stale:
        return 0
    return t1_settle.settle_cnc_to_holdings()


def _close_expired_fno_positions() -> int:
    """Auto-settle F&O positions whose contract has expired.

    Options expire worthless (settled at 0). Futures settle at their last
    known LTP. Margin is released back to *available*; realized PnL flows
    into cumulative ``realized_pnl`` only — same session-isolation rule as
    stale MIS closes.

    Expiry detection prefers the symbol-name encoding (``NIFTY28APR26...``
    -> 28-Apr-2026) and falls back to the symbol-master ``expiry`` field
    when the symbol doesn't carry a ``DDMMMYY``. This matters because the
    master-contract download wipes and re-inserts the symtoken table on
    every refresh, dropping expired symbols — so by the time we look the
    contract up, ``SymToken`` is likely already gone. Without the
    name-based parser the expired position would be silently skipped and
    the MTM updater would keep polling its dead symbol.
    """
    today = datetime.now(tz=IST).date()
    closed = 0
    margin_releases: dict[int, tuple[float, float]] = {}

    from backend.models.symbol import SymToken

    with session_scope() as db:
        rows = (
            db.execute(
                select(SandboxPosition).where(
                    SandboxPosition.product.in_(("MIS", "NRML")),
                    SandboxPosition.net_quantity != 0,
                    SandboxPosition.exchange.in_(("NFO", "BFO", "MCX", "CDS", "BCD")),
                )
            )
            .scalars()
            .all()
        )
        for pos in rows:
            sym = db.execute(
                select(SymToken).where(
                    SymToken.symbol == pos.symbol,
                    SymToken.exchange == pos.exchange,
                )
            ).scalar_one_or_none()

            sym_expiry_field = sym.expiry if sym is not None else None
            expiry_date = get_contract_expiry(pos.symbol, pos.exchange, sym_expiry_field)
            if expiry_date is None or expiry_date >= today:
                continue

            # Instrument type — prefer the master-contract row, but fall back
            # to symbol-name classification when the row is gone (the common
            # case for expired contracts).
            if sym is not None and sym.instrumenttype:
                instrument_type = sym.instrumenttype.upper()
            else:
                instrument_type = classify_from_symbol(pos.symbol, pos.exchange).upper()

            if instrument_type in ("CE", "PE"):
                close_price = 0.0  # options expire worthless
            else:
                # Futures: best LTP we can find, else fall back to avg
                ltp = float(pos.ltp or 0.0) or get_ltp_with_fallback(
                    pos.user_id, pos.symbol, pos.exchange
                ) or pos.average_price
                close_price = float(ltp or 0.0)

            qty = pos.net_quantity
            if qty > 0:
                realized = (close_price - pos.average_price) * qty
            else:
                realized = (pos.average_price - close_price) * abs(qty)
            margin = float(pos.margin_blocked or 0.0)

            pos.realized_pnl = round(float(pos.realized_pnl or 0.0) + realized, 4)
            pos.net_quantity = 0
            pos.average_price = 0.0
            pos.margin_blocked = 0.0
            pos.unrealized_pnl = 0.0
            pos.pnl = round(pos.realized_pnl, 4)

            agg = margin_releases.get(pos.user_id, (0.0, 0.0))
            margin_releases[pos.user_id] = (
                round(agg[0] + margin, 2),
                round(agg[1] + realized, 4),
            )
            closed += 1

    for uid, (margin, realized) in margin_releases.items():
        try:
            if margin > 0:
                fund_manager.release_margin(uid, margin, realized_pnl=0.0)
            if realized:
                with session_scope() as db:
                    funds = fund_manager.ensure_fund_row(db, uid)
                    funds.realized_pnl += realized
                    funds.available += realized
        except Exception:
            logger.exception("catch_up: expired-contract close failed for user %d", uid)

    if closed:
        logger.info("catch_up: settled %d expired F&O positions", closed)
    return closed


def _parse_expiry(s: str) -> date | None:
    """Parse the symbol-master ``expiry`` field.

    OpenBull's symtoken stores expiry as a date-ish string; we accept the
    common formats: ``YYYY-MM-DD`` and ``DD-MMM-YYYY`` (e.g. 27-FEB-2026).
    Returns ``None`` if the format isn't recognised so we don't accidentally
    settle a healthy position.
    """
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d%b%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_FNO_EXCHANGES_FOR_EXPIRY = ("NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX")
_SYMBOL_EXPIRY_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")
_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_expiry_from_symbol(symbol: str, exchange: str) -> date | None:
    """Parse expiry date from F&O symbol name.

    Mirrors openalgo's ``parse_expiry_from_symbol``. Looks for a
    ``DDMMMYY`` substring (e.g. ``28APR26``) which is the openalgo
    canonical expiry encoding used by NIFTY28APR2624100PE,
    BANKNIFTY30APR26FUT, etc. Returns ``None`` for non-F&O exchanges
    or when the pattern isn't present.

    This is the *primary* expiry source: master-contract refreshes drop
    expired symbols from SymToken, so falling back to the DB alone misses
    the very positions we need to settle.
    """
    if exchange not in _FNO_EXCHANGES_FOR_EXPIRY:
        return None
    m = _SYMBOL_EXPIRY_RE.search(symbol or "")
    if not m:
        return None
    try:
        day = int(m.group(1))
        month = _MONTH_MAP.get(m.group(2))
        if not month:
            return None
        year = 2000 + int(m.group(3))
        return date(year, month, day)
    except (ValueError, KeyError):
        return None


def get_contract_expiry(
    symbol: str, exchange: str, sym_expiry_field: str | None = None
) -> date | None:
    """Resolve a contract's expiry, preferring the symbol-name encoding.

    ``sym_expiry_field`` is the optional ``SymToken.expiry`` value, used
    only as a fallback when the symbol name doesn't carry an embedded
    DDMMMYY (e.g. instrument types we don't know about).
    """
    parsed = parse_expiry_from_symbol(symbol, exchange)
    if parsed is not None:
        return parsed
    if sym_expiry_field:
        return _parse_expiry(sym_expiry_field)
    return None


def run_catch_up_tasks() -> dict[str, int]:
    """Public entry point. Each step is wrapped in try/except so one
    failing recovery doesn't block the others. Returns a counts dict
    suitable for logging or surfacing on the /sandbox/squareoff-status
    endpoint."""
    counts = {
        "stale_mis_closed": 0,
        "stale_cnc_settled": 0,
        "expired_fno_closed": 0,
        "today_pnl_rows_reset": 0,
    }
    try:
        counts["stale_mis_closed"] = _close_stale_mis_positions()
    except Exception:
        logger.exception("catch_up: stale MIS step raised")
    try:
        counts["stale_cnc_settled"] = _settle_stale_cnc_positions()
    except Exception:
        logger.exception("catch_up: stale CNC step raised")
    try:
        counts["expired_fno_closed"] = _close_expired_fno_positions()
    except Exception:
        logger.exception("catch_up: expired FnO step raised")
    try:
        counts["today_pnl_rows_reset"] = daily_reset.reset_today_pnl()
    except Exception:
        logger.exception("catch_up: today_realized_pnl reset raised")
    try:
        # Best-effort: snapshot yesterday if not present.
        yesterday = (datetime.now(tz=IST).date() - timedelta(days=1))
        pnl_snapshot.snapshot_for_date(yesterday)
    except Exception:
        logger.exception("catch_up: pnl_snapshot backfill raised")
    logger.info("sandbox catch_up done: %s", counts)
    return counts
