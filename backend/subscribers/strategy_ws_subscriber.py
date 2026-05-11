"""Bus → WebSocket bridge.

Every published strategy event is fanned out to connected UI clients via
the per-strategy queue in :mod:`backend.strategy.broadcast`. Lives in
``subscribers/`` (next to the audit subscriber) so all event-bus
consumers are wired in one place — :func:`backend.subscribers.register_all`.
"""

from __future__ import annotations

import logging

from backend.events.strategy_events import StrategyConfigEvent
from backend.strategy import broadcast
from backend.strategy.time_utils import format_ist, now_utc

logger = logging.getLogger(__name__)


def push_event(event: StrategyConfigEvent) -> None:
    """Translate a bus event to the WS ``event`` frame shape (plan Section 7.2)."""
    try:
        kind = event.topic.split(".", 1)[1] if "." in event.topic else event.topic
        broadcast.push_event(int(event.strategy_id), {
            "type": "event",
            "event_id": None,  # Phase 6: not paired with the DB row id; Phase 7+
            "ts_ist": format_ist(now_utc()),
            "ts_ms_utc": int(now_utc().timestamp() * 1000),
            "kind": kind,
            "severity": event.severity or "info",
            "leg_id": event.leg_id,
            "message": event.message or "",
            "payload": event.payload or {},
        })
    except Exception:
        logger.exception("Failed to push event %s to WS", event.topic)
