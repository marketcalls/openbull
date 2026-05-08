"""Typed event classes published on the OpenBull event bus.

Events are immutable dataclasses with a ``topic`` string and domain-specific
fields. Subscribers register on topics; publishers don't know who's listening.

Phase 1 ships strategy-module CRUD events; later phases add runtime events
(SL hit, target hit, lock-profit, etc.) and order events.
"""

from backend.events.strategy_events import (
    LegCloseManualEvent,
    LegEntryFilledEvent,
    LegEntryPlacedEvent,
    LegEntryRejectedEvent,
    LegExitFilledEvent,
    LegExitPlacedEvent,
    LegExitRejectedEvent,
    LegSlHitEvent,
    LegTargetHitEvent,
    LegTrailAdvancedEvent,
    LegTrailArmedEvent,
    LiveEnabledEvent,
    LockProfitArmedEvent,
    LockProfitFloorAdvancedEvent,
    LockProfitTriggeredEvent,
    OverallSlHitEvent,
    OverallTargetHitEvent,
    RunStartedEvent,
    RunStoppedEvent,
    StrategyConfigEvent,
    StrategyCreatedEvent,
    StrategyDeletedEvent,
    StrategyUpdatedEvent,
    TrailToEntryActivatedEvent,
    WebhookTokenRotatedEvent,
)

__all__ = [
    # config events (Phase 1)
    "StrategyConfigEvent",
    "StrategyCreatedEvent",
    "StrategyUpdatedEvent",
    "StrategyDeletedEvent",
    "WebhookTokenRotatedEvent",
    "LiveEnabledEvent",
    # runtime events (Phase 4+)
    "RunStartedEvent",
    "RunStoppedEvent",
    "LegEntryPlacedEvent",
    "LegEntryFilledEvent",
    "LegEntryRejectedEvent",
    "LegExitPlacedEvent",
    "LegExitFilledEvent",
    "LegExitRejectedEvent",
    "LegSlHitEvent",
    "LegTargetHitEvent",
    "LegTrailArmedEvent",
    "LegTrailAdvancedEvent",
    "LegCloseManualEvent",
    "OverallSlHitEvent",
    "OverallTargetHitEvent",
    "LockProfitArmedEvent",
    "LockProfitFloorAdvancedEvent",
    "LockProfitTriggeredEvent",
    "TrailToEntryActivatedEvent",
]
