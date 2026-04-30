"""
Session-boundary helpers for the sandbox.

All sandbox EOD rollover logic — orderbook/tradebook windowing, T+1 cut-off,
today_realized_pnl reset, day_buy/sell counter reset — pivots on a single
wall-clock value: the start of the *current* sandbox session.

Default boundary is 00:00 IST. Configurable via ``sandbox_config`` key
``session_expiry_time`` (HH:MM, IST) so a user trading the MCX night bucket
can shift it to e.g. 03:00 if they prefer the openalgo default.

Returned datetimes are timezone-aware (IST). Callers that compare against
DB columns declared ``DateTime(timezone=True)`` can pass the result
straight into a SQLAlchemy filter.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from backend.sandbox.config import _read_int  # noqa: F401 — re-export shape

IST = timezone(timedelta(hours=5, minutes=30))


def _read_session_expiry_str() -> str:
    """Return the configured session-expiry time as ``"HH:MM"``.

    Uses ``sandbox_config.session_expiry_time`` when set, falls back to
    ``"00:00"``. Read each call rather than cached so a runtime config
    edit takes effect on the next read without a process restart.
    """
    from sqlalchemy import select
    from backend.models.sandbox import SandboxConfig
    from backend.sandbox._db import session_scope

    try:
        with session_scope() as db:
            row = db.execute(
                select(SandboxConfig).where(SandboxConfig.key == "session_expiry_time")
            ).scalar_one_or_none()
            if row and row.value:
                return str(row.value).strip()
    except Exception:
        # If anything goes wrong (table missing during early bootstrap,
        # transient DB blip), fall back to the safe default rather than
        # blocking a position read.
        pass
    return "00:00"


def _parse_hhmm(s: str) -> time:
    try:
        h_str, m_str = s.split(":", 1)
        return time(int(h_str), int(m_str))
    except (ValueError, AttributeError):
        return time(0, 0)


def session_start_ist(now: datetime | None = None) -> datetime:
    """Return the IST datetime of the most recent session boundary.

    If ``now`` is past today's expiry-time, the boundary is *today* at
    that time; otherwise the boundary is *yesterday* at that time.

    Example with the default 00:00 IST:
      now = 2026-04-30 14:33 IST  ->  2026-04-30 00:00 IST
      now = 2026-05-01 03:30 IST  ->  2026-05-01 00:00 IST

    Example with session_expiry_time=03:00 (openalgo default):
      now = 2026-04-30 14:33 IST  ->  2026-04-30 03:00 IST
      now = 2026-04-30 02:50 IST  ->  2026-04-29 03:00 IST  (still yesterday's session)
    """
    if now is None:
        now = datetime.now(tz=IST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    expiry = _parse_hhmm(_read_session_expiry_str())
    today_boundary = now.replace(
        hour=expiry.hour, minute=expiry.minute, second=0, microsecond=0
    )
    if now >= today_boundary:
        return today_boundary
    return today_boundary - timedelta(days=1)
