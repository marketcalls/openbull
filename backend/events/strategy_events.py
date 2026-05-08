"""Strategy module event types.

Events fall into two layers:

1. **Config events** (Phase 1) — fired from the CRUD router on strategy
   create / update / delete / token rotate / live-enable. ``run_id=None``.

2. **Runtime events** (Phase 4+) — fired from the engine while a run is
   live. Always carry ``run_id``. These are the canonical risk-event
   audit trail (SL hit, target hit, lock-profit, etc.) and are persisted
   to ``sm_strategy_event``.

Every event maps to one of the ``kind`` enum values documented in the
plan (Section 4.1.5). Topic strings are the same as the kind for routing
simplicity. Severity is per-event so the audit subscriber can colour rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.utils.event_bus import Event


@dataclass
class StrategyConfigEvent(Event):
    """Base for config-layer events. ``run_id`` is always None."""

    user_id: int = 0
    strategy_id: int = 0
    run_id: Optional[int] = None
    leg_id: Optional[int] = None
    severity: str = "info"
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


# --- config events (Phase 1) -------------------------------------------------


@dataclass
class StrategyCreatedEvent(StrategyConfigEvent):
    topic: str = "strategy.created"


@dataclass
class StrategyUpdatedEvent(StrategyConfigEvent):
    topic: str = "strategy.updated"


@dataclass
class StrategyDeletedEvent(StrategyConfigEvent):
    topic: str = "strategy.deleted"


@dataclass
class WebhookTokenRotatedEvent(StrategyConfigEvent):
    topic: str = "strategy.webhook_token_rotated"


@dataclass
class LiveEnabledEvent(StrategyConfigEvent):
    """Fired when a user explicitly opts a strategy into live mode (Phase 7+)."""
    topic: str = "strategy.live_enabled"


# --- run lifecycle events (Phase 4+) -----------------------------------------


@dataclass
class RunStartedEvent(StrategyConfigEvent):
    topic: str = "strategy.run_started"


@dataclass
class RunStoppedEvent(StrategyConfigEvent):
    topic: str = "strategy.run_stopped"


# --- leg entry / exit events (Phase 4+) --------------------------------------


@dataclass
class LegEntryPlacedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_entry_placed"


@dataclass
class LegEntryFilledEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_entry_filled"


@dataclass
class LegEntryRejectedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_entry_rejected"


@dataclass
class LegExitPlacedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_exit_placed"


@dataclass
class LegExitFilledEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_exit_filled"


@dataclass
class LegExitRejectedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_exit_rejected"


# --- per-leg risk events (Phase 6) -------------------------------------------


@dataclass
class LegSlHitEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_sl_hit"


@dataclass
class LegTargetHitEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_target_hit"


@dataclass
class LegTrailArmedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_trail_armed"


@dataclass
class LegTrailAdvancedEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_trail_advanced"


@dataclass
class LegCloseManualEvent(StrategyConfigEvent):
    topic: str = "strategy.leg_close_manual"


# --- strategy-level risk events (Phase 7) ------------------------------------


@dataclass
class OverallSlHitEvent(StrategyConfigEvent):
    topic: str = "strategy.overall_sl_hit"


@dataclass
class OverallTargetHitEvent(StrategyConfigEvent):
    topic: str = "strategy.overall_target_hit"


@dataclass
class LockProfitArmedEvent(StrategyConfigEvent):
    topic: str = "strategy.lock_profit_armed"


@dataclass
class LockProfitFloorAdvancedEvent(StrategyConfigEvent):
    topic: str = "strategy.lock_profit_floor_advanced"


@dataclass
class LockProfitTriggeredEvent(StrategyConfigEvent):
    topic: str = "strategy.lock_profit_triggered"


@dataclass
class TrailToEntryActivatedEvent(StrategyConfigEvent):
    topic: str = "strategy.trail_to_entry_activated"
