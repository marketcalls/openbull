"""IST timestamp formatting for strategy module API responses.

Storage stays UTC (PG ``timestamptz``) — these helpers convert to IST at the
API boundary. Every API/WS payload carrying a timestamp uses ``format_ist``
so the wire format is consistent: ISO 8601 with explicit ``+05:30`` offset.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def format_ist(dt: datetime | None) -> str | None:
    """Render a UTC-aware datetime as an IST ISO 8601 string.

    Returns ``None`` if input is ``None`` so optional columns stay optional.
    Naive datetimes are assumed UTC (matches PG ``timestamptz`` behaviour).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).isoformat(timespec="milliseconds")


def now_utc() -> datetime:
    """Tz-aware UTC now. Use everywhere a timestamp is set in code (never naive)."""
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    """Tz-aware now in Asia/Kolkata.

    Used by signal-mode engine checks for intraday entry/exit windows
    (the strategy's entry_time/exit_time are stored as IST-local time
    of day per design section 4.4).
    """
    return datetime.now(IST)
