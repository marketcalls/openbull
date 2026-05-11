"""Subscriber registration — wires every event subscriber to the bus.

Call :func:`register_all` once during app startup. New subscribers can be
added without touching publishers — that's the point of the event bus.
"""

from backend.subscribers import strategy_audit_subscriber, strategy_ws_subscriber
from backend.utils.event_bus import bus
from backend.utils.logging import get_logger

logger = get_logger(__name__)


# Every strategy event topic that should be persisted to sm_strategy_event.
# Listed explicitly so a typo in a topic string fails registration loudly
# rather than silently dropping events on the floor.
_STRATEGY_AUDIT_TOPICS: tuple[str, ...] = (
    # Phase 1 — config events
    "strategy.created",
    "strategy.updated",
    "strategy.deleted",
    "strategy.webhook_token_rotated",
    "strategy.live_enabled",
    # Phase 4+ — runtime events (subscriber is stable, publishers come later)
    "strategy.run_started",
    "strategy.run_stopped",
    "strategy.leg_entry_placed",
    "strategy.leg_entry_filled",
    "strategy.leg_entry_rejected",
    "strategy.leg_exit_placed",
    "strategy.leg_exit_filled",
    "strategy.leg_exit_rejected",
    "strategy.leg_sl_hit",
    "strategy.leg_target_hit",
    "strategy.leg_trail_armed",
    "strategy.leg_trail_advanced",
    "strategy.leg_close_manual",
    "strategy.overall_sl_hit",
    "strategy.overall_target_hit",
    "strategy.lock_profit_armed",
    "strategy.lock_profit_floor_advanced",
    "strategy.lock_profit_triggered",
    "strategy.trail_to_entry_activated",
)


def register_all() -> None:
    """Register every subscriber on every relevant topic."""
    for topic in _STRATEGY_AUDIT_TOPICS:
        bus.subscribe(
            topic,
            strategy_audit_subscriber.persist_event,
            f"audit:{topic}",
        )
        bus.subscribe(
            topic,
            strategy_ws_subscriber.push_event,
            f"ws:{topic}",
        )
    logger.info(
        "EventBus: %d audit + %d ws-broadcast subscriptions registered",
        len(_STRATEGY_AUDIT_TOPICS), len(_STRATEGY_AUDIT_TOPICS),
    )
