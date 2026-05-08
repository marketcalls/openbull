"""Strategy audit subscriber.

Persists every strategy event published on the bus into ``sm_strategy_event``.
Runs in the EventBus thread pool (sync), uses a synchronous SQLAlchemy engine
against ``sync_database_url`` — same pattern as ``api_log_writer`` so the
async request loop never waits on the audit write.

Failures are isolated by the bus's ``_safe_call`` wrapper — a logging failure
won't take down the publisher.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.events.strategy_events import StrategyConfigEvent
from backend.models.strategy_module import SmStrategyEvent

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    """Sync session factory — built lazily so settings are loaded first.

    Cached so all subscriber threads share one engine + connection pool.
    """
    engine = create_engine(
        get_settings().sync_database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _kind_from_topic(topic: str) -> str:
    """Topic ``strategy.leg_sl_hit`` → kind ``leg_sl_hit`` for the audit row."""
    return topic.split(".", 1)[1] if "." in topic else topic


def persist_event(event: StrategyConfigEvent) -> None:
    """Insert one row into sm_strategy_event for the published event."""
    factory = _session_factory()
    try:
        with factory() as session:
            row = SmStrategyEvent(
                run_id=event.run_id,
                strategy_id=event.strategy_id,
                user_id=event.user_id,
                kind=_kind_from_topic(event.topic),
                severity=event.severity or "info",
                leg_id=event.leg_id,
                message=event.message or "",
                payload=event.payload or None,
            )
            session.add(row)
            session.commit()
    except Exception:
        # Wrapped by EventBus._safe_call too, but we log here for context.
        logger.exception(
            "Failed to persist strategy event topic=%s strategy_id=%s",
            event.topic, event.strategy_id,
        )
