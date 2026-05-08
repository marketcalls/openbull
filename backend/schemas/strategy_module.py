"""Pydantic request/response schemas for the strategy module CRUD API.

Strict mode (``extra='forbid'``) is on every schema — unknown fields are
rejected with 422 instead of silently dropped (Section 14.5 of the plan).

Response timestamps are rendered in IST ISO 8601 via
:func:`backend.strategy.time_utils.format_ist`.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.strategy.time_utils import format_ist

# ----------------------------------------------------------------------------
# Leg / sub-objects
# ----------------------------------------------------------------------------


class TrailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(0, ge=0, description="Favorable move (pts) before trail arms")
    y: float = Field(0, ge=0, description="Trail step (pts) once armed")


class Leg(BaseModel):
    """One leg in a strategy.

    Validation rules per the plan (Section 4.1.1):

    * ``segment="options"`` requires ``option_type`` and ``strike_mode``.
    * ``strike_mode="atm"`` requires ``atm_offset``.
    * ``strike_mode="strike"`` requires ``strike_value``.
    * ``expiry`` value is gated by the parent strategy's ``universe_tab`` —
      enforced at the strategy level, not here, so a leg validates standalone.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., ge=1, description="1-indexed leg number, unique within strategy")
    segment: Literal["options", "futures", "cash"]
    expiry: Literal["weekly", "monthly", "current", "next"]
    lots: int = Field(..., gt=0, le=50)
    position: Literal["B", "S"]

    option_type: Optional[Literal["CE", "PE"]] = None
    strike_mode: Optional[Literal["atm", "strike"]] = None
    atm_offset: Optional[str] = Field(
        None,
        pattern=r"^(ATM|ATM[+-]\d+|ITM\d+|OTM\d+)$",
        description="ATM, ATM+1, ATM-1, ITM2, OTM3, etc.",
    )
    strike_value: Optional[float] = Field(None, gt=0)

    target_pts: Optional[float] = Field(None, ge=0)
    sl_pts: Optional[float] = Field(None, ge=0)
    trail: TrailConfig = Field(default_factory=TrailConfig)

    momentum: Optional[dict] = Field(default=None, description="v1 stub — not evaluated")

    @model_validator(mode="after")
    def _validate_segment_fields(self) -> "Leg":
        if self.segment == "options":
            if self.option_type is None:
                raise ValueError("option_type required when segment='options'")
            if self.strike_mode is None:
                raise ValueError("strike_mode required when segment='options'")
            if self.strike_mode == "atm" and not self.atm_offset:
                raise ValueError("atm_offset required when strike_mode='atm'")
            if self.strike_mode == "strike" and self.strike_value is None:
                raise ValueError("strike_value required when strike_mode='strike'")
        else:
            # Non-options legs must not carry option-only fields.
            if self.option_type is not None:
                raise ValueError("option_type only valid when segment='options'")
            if self.strike_mode is not None:
                raise ValueError("strike_mode only valid when segment='options'")
        return self


class LockProfitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["lock", "lock_and_trail"]
    if_profit_reaches: float = Field(..., gt=0)
    lock_profit: float = Field(..., ge=0)
    trail_step: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _trail_step_required_for_lock_and_trail(self) -> "LockProfitConfig":
        if self.mode == "lock_and_trail" and self.trail_step is None:
            raise ValueError("trail_step required when mode='lock_and_trail'")
        if self.lock_profit > self.if_profit_reaches:
            raise ValueError("lock_profit must be <= if_profit_reaches")
        return self


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    days: List[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]] = Field(
        default_factory=lambda: ["MON", "TUE", "WED", "THU", "FRI"]
    )
    start_time: str = Field("09:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    auto_stop_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    default_mode: Literal["live", "sandbox"] = "sandbox"


class WebhookIpAllowlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cidr: str = Field(..., min_length=1, max_length=43)
    label: Optional[str] = Field(None, max_length=100)


# ----------------------------------------------------------------------------
# Create / Update / Out
# ----------------------------------------------------------------------------


_STRATEGY_TYPE = Literal["intraday", "positional"]
_UNIVERSE_TAB = Literal["weekly_monthly", "monthly_only", "stocks_fno", "mcx", "delta"]
_PRODUCT = Literal["NRML", "MIS", "CNC"]
_PRICETYPE = Literal["MARKET", "LIMIT"]


class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    universe_tab: _UNIVERSE_TAB
    underlying: str = Field(..., min_length=1, max_length=50)
    underlying_exchange: str = Field(..., min_length=1, max_length=20)

    strategy_type: _STRATEGY_TYPE
    entry_time: Optional[time] = None
    exit_time: Optional[time] = None

    product: _PRODUCT = "NRML"
    pricetype: _PRICETYPE = "MARKET"

    legs: List[Leg] = Field(..., min_length=1, max_length=10)

    overall_sl_mtm: Optional[float] = Field(None, ge=0)
    overall_target_mtm: Optional[float] = Field(None, ge=0)
    lock_profit: Optional[LockProfitConfig] = None
    trail_sl_to_entry: bool = False

    scheduler: Optional[SchedulerConfig] = None

    webhook_ip_allowlist: Optional[List[WebhookIpAllowlistEntry]] = None
    daily_loss_limit_inr: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _validate_intraday_times(self) -> "StrategyCreate":
        if self.strategy_type == "intraday":
            if self.entry_time is None or self.exit_time is None:
                raise ValueError("entry_time and exit_time required for intraday strategies")
            if self.entry_time >= self.exit_time:
                raise ValueError("entry_time must be before exit_time")
        return self

    @field_validator("legs")
    @classmethod
    def _unique_leg_ids(cls, legs: List[Leg]) -> List[Leg]:
        ids = [leg.id for leg in legs]
        if len(set(ids)) != len(ids):
            raise ValueError("leg ids must be unique within a strategy")
        return legs


class StrategyUpdate(BaseModel):
    """Partial update — every field optional. PATCH-style.

    PATCH is rejected (409) when ``status != 'stopped'`` at the router layer.
    Mass-assignment of internal fields like ``webhook_token_hash``, ``user_id``,
    ``status``, ``current_run_id`` is impossible because they're not in this
    schema (Pydantic strict mode rejects them).
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    universe_tab: Optional[_UNIVERSE_TAB] = None
    underlying: Optional[str] = Field(None, min_length=1, max_length=50)
    underlying_exchange: Optional[str] = Field(None, min_length=1, max_length=20)

    strategy_type: Optional[_STRATEGY_TYPE] = None
    entry_time: Optional[time] = None
    exit_time: Optional[time] = None

    product: Optional[_PRODUCT] = None
    pricetype: Optional[_PRICETYPE] = None

    legs: Optional[List[Leg]] = Field(None, min_length=1, max_length=10)

    overall_sl_mtm: Optional[float] = Field(None, ge=0)
    overall_target_mtm: Optional[float] = Field(None, ge=0)
    lock_profit: Optional[LockProfitConfig] = None
    trail_sl_to_entry: Optional[bool] = None

    scheduler: Optional[SchedulerConfig] = None

    webhook_ip_allowlist: Optional[List[WebhookIpAllowlistEntry]] = None
    daily_loss_limit_inr: Optional[float] = Field(None, gt=0)

    @field_validator("legs")
    @classmethod
    def _unique_leg_ids(cls, legs: Optional[List[Leg]]) -> Optional[List[Leg]]:
        if legs is None:
            return legs
        ids = [leg.id for leg in legs]
        if len(set(ids)) != len(ids):
            raise ValueError("leg ids must be unique within a strategy")
        return legs


# ----------------------------------------------------------------------------
# Output (read)
# ----------------------------------------------------------------------------


def _decimal_to_float(v):
    """Pydantic serializer helper for Numeric columns coming back as Decimal."""
    if v is None:
        return None
    return float(v)


class StrategyOut(BaseModel):
    """Detail / single-fetch response. Webhook URL is included; token is NOT."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    universe_tab: str
    underlying: str
    underlying_exchange: str
    strategy_type: str
    entry_time: Optional[str]
    exit_time: Optional[str]
    product: str
    pricetype: str
    legs: List[Leg]
    overall_sl_mtm: Optional[float]
    overall_target_mtm: Optional[float]
    lock_profit: Optional[LockProfitConfig]
    trail_sl_to_entry: bool
    scheduler: Optional[SchedulerConfig]

    live_enabled: bool
    webhook_url: str = Field(..., description="Public webhook URL for this strategy (token embedded)")
    webhook_ip_allowlist: Optional[List[WebhookIpAllowlistEntry]]
    daily_loss_limit_inr: Optional[float]

    status: str
    current_run_id: Optional[int]

    created_at: str
    updated_at: str


class StrategyListItem(BaseModel):
    """Lighter shape for list view — drops legs/scheduler/etc.

    The list page in v1 still wants P&L columns (Section 3.1) but they require
    the engine which doesn't exist yet. Phase 1 returns realized/unrealized/total
    as 0 placeholders; Phase 6 wires them to live state.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    universe_tab: str
    underlying: str
    strategy_type: str
    status: str
    live_enabled: bool

    pnl_realized: float = 0.0
    pnl_unrealized: float = 0.0
    pnl_total: float = 0.0

    created_at: str
    updated_at: str


class StrategyCreateResponse(BaseModel):
    """Special response for create + rotate: includes one-time-view webhook token."""

    model_config = ConfigDict(extra="forbid")

    strategy: StrategyOut
    webhook_token: str = Field(
        ...,
        description=(
            "PLAINTEXT webhook token. Shown ONCE — copy it now. "
            "It is hashed in the DB and cannot be retrieved later."
        ),
    )
