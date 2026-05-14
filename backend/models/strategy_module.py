"""
Strategy Module — SQLAlchemy ORM models.

Six tables, all `sm_` prefixed to namespace away from the legacy ``strategies``
table (saved Strategy Builder strategies) which remains untouched.

Schema mirrors the design in ``docs/plan/strategy-module.md``:

* ``sm_strategy``                 — strategy config (legs jsonb, risk params, scheduler, webhook token hash)
* ``sm_strategy_run``             — every activation of a strategy from start to stop
* ``sm_strategy_order``           — every order the engine places (audit-grade)
* ``sm_strategy_checkpoint``      — periodic runtime snapshots for crash recovery
* ``sm_webhook_event``            — every TradingView webhook hit, accepted or rejected
* ``sm_strategy_event``           — risk-event audit trail (SL hit, target hit, lock-profit, …)

Phase 1 ships the schema + CRUD; later phases populate run / order / event /
checkpoint tables as the engine comes online.

Timestamps stored as ``timestamptz`` (PG-aware UTC). API-layer renders them in
IST per ``docs/plan/strategy-module.md`` Section 4.4.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB

from backend.database import Base


class SmStrategy(Base):
    """Strategy config row. One per saved strategy.

    The ``legs`` JSONB column is the source of truth for leg definitions — we
    keep it loose so wizard UI changes don't force a migration. Per-leg shape
    is validated via Pydantic at the API boundary.

    ``webhook_token_hash`` is the SHA-256 hex digest of the per-strategy URL
    token. Plaintext is shown to the user once on create/rotate and never
    stored. Unique-indexed for O(1) webhook dispatch lookups.
    """

    __tablename__ = "sm_strategy"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(200), nullable=False)

    # Coexistence discriminator. 'batch' = the original multi-leg options
    # spread mode (start/stop webhook actions). 'signal' = TradingView-style
    # per-leg signals (long_entry/long_exit/short_entry/short_exit). Default
    # 'batch' so every existing row stays semantically unchanged.
    # See docs/plan/strategy-signal-mode.md section 3.1.
    strategy_kind = Column(String(20), nullable=False, default="batch")

    # Signal-mode direction filter (long_only / short_only / both). Ignored
    # for batch-mode strategies. Default 'both' = no filtering.
    direction = Column(String(20), nullable=False, default="both")

    universe_tab = Column(String(30), nullable=False)
    # For signal-mode strategies the underlying / underlying_exchange are
    # nominal - each leg carries its own symbol/exchange. Kept non-null for
    # backwards compatibility with batch-mode rows; signal-mode wizard sets
    # them from the first leg or to a sentinel like 'MULTI'.
    underlying = Column(String(50), nullable=False)
    underlying_exchange = Column(String(20), nullable=False)

    strategy_type = Column(String(20), nullable=False)
    entry_time = Column(Time, nullable=True)
    exit_time = Column(Time, nullable=True)

    product = Column(String(10), nullable=False, default="NRML")
    pricetype = Column(String(10), nullable=False, default="MARKET")

    legs = Column(JSONB, nullable=False)

    overall_sl_mtm = Column(Numeric(18, 2), nullable=True)
    overall_target_mtm = Column(Numeric(18, 2), nullable=True)
    lock_profit = Column(JSONB, nullable=True)
    trail_sl_to_entry = Column(Boolean, nullable=False, default=False)

    scheduler = Column(JSONB, nullable=True)

    live_enabled = Column(Boolean, nullable=False, default=False)
    webhook_token_hash = Column(String(64), nullable=False, unique=True)
    webhook_ip_allowlist = Column(JSONB, nullable=True)
    # Kill-switch / isolation flag. When True the webhook handler refuses
    # every incoming signal (and start/stop for batch) for this strategy
    # with HTTP 403 and audit label 'rejected_locked'. Flipped on by the
    # /kill_switch endpoint (also cancels pending orders + flattens open
    # positions). Cleared by the explicit /unlock_webhook endpoint.
    webhook_locked = Column(Boolean, nullable=False, default=False)
    daily_loss_limit_inr = Column(Numeric(18, 2), nullable=True)

    status = Column(String(20), nullable=False, default="stopped")
    # FK back to sm_strategy_run.id — circular, so use_alter creates the
    # constraint after both tables exist.
    current_run_id = Column(
        Integer,
        ForeignKey("sm_strategy_run.id", use_alter=True, name="fk_sm_strategy_current_run"),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_sm_strategy_user_name"),
        Index("ix_sm_strategy_user_status", "user_id", "status"),
    )


class SmStrategyRun(Base):
    """One activation of a strategy. Multiple runs over a strategy's lifetime."""

    __tablename__ = "sm_strategy_run"

    id = Column(Integer, primary_key=True)
    strategy_id = Column(
        Integer,
        ForeignKey("sm_strategy.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(String(10), nullable=False)
    broker = Column(String(50), nullable=False)

    started_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    stop_reason = Column(String(30), nullable=True)

    pnl_realized = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_peak = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_trough = Column(Numeric(18, 2), nullable=False, default=0)

    trigger_source = Column(String(20), nullable=False, default="manual")
    webhook_event_id = Column(
        Integer,
        ForeignKey("sm_webhook_event.id", use_alter=True, name="fk_sm_run_webhook_event"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_sm_run_strategy_started", "strategy_id", "started_at"),
    )


class SmStrategyOrder(Base):
    """Every order the engine places — entry, exit-by-rule, manual close.

    Audit-grade: rows are append-only after creation; status is updated as
    fills come back. ``broker_order_id`` is the broker's order reference for
    live runs and a ``SANDBOX-<n>`` synthetic id for sandbox runs.
    """

    __tablename__ = "sm_strategy_order"

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer,
        ForeignKey("sm_strategy_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_id = Column(Integer, nullable=False)
    kind = Column(String(30), nullable=False)

    broker_order_id = Column(String(100), nullable=True, index=True)
    symbol = Column(String(100), nullable=False)
    exchange = Column(String(20), nullable=False)
    action = Column(String(10), nullable=False)
    qty = Column(Integer, nullable=False)
    pricetype = Column(String(10), nullable=False)
    price = Column(Numeric(18, 4), nullable=False, default=0)
    trigger_price = Column(Numeric(18, 4), nullable=False, default=0)

    status = Column(String(20), nullable=False, default="pending")
    placed_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    filled_at = Column(DateTime(timezone=True), nullable=True)
    avg_fill_price = Column(Numeric(18, 4), nullable=True)
    filled_qty = Column(Integer, nullable=True)
    reject_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sm_order_run_placed", "run_id", "placed_at"),
    )


class SmStrategyCheckpoint(Base):
    """Periodic snapshot of runtime state for crash recovery (~30s cadence)."""

    __tablename__ = "sm_strategy_checkpoint"

    id = Column(BigInteger, primary_key=True)
    run_id = Column(
        Integer,
        ForeignKey("sm_strategy_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pnl_realized = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_unrealized = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_total = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_peak = Column(Numeric(18, 2), nullable=False, default=0)
    pnl_trough = Column(Numeric(18, 2), nullable=False, default=0)

    lock_floor = Column(Numeric(18, 2), nullable=True)
    trail_to_entry_active = Column(Boolean, nullable=False, default=False)

    leg_state = Column(JSONB, nullable=False)


class SmWebhookEvent(Base):
    """Audit row for every inbound TradingView webhook — accepted or rejected.

    ``strategy_id`` is nullable because requests with an unknown URL token
    can't be resolved to a strategy (logged with ``result='rejected_token'``).
    The token plaintext is **never** persisted to ``payload``; the redactor
    in the webhook handler strips it before save.
    """

    __tablename__ = "sm_webhook_event"

    id = Column(Integer, primary_key=True)
    strategy_id = Column(
        Integer,
        ForeignKey("sm_strategy.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action = Column(String(20), nullable=True)
    mode = Column(String(10), nullable=True)
    payload = Column(JSONB, nullable=True)

    ip = Column(INET, nullable=True)
    user_agent = Column(String(255), nullable=True)
    received_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    result = Column(String(50), nullable=False)
    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sm_webhook_strategy_received", "strategy_id", "received_at"),
    )


class SmStrategyEvent(Base):
    """Risk-event audit trail — SL hit, target hit, lock-profit, etc.

    Every state-changing action the engine produces is persisted here AND
    broadcast over the strategy WS. ``strategy_id`` and ``user_id`` are
    denormalized for forensic queries that don't join.
    """

    __tablename__ = "sm_strategy_event"

    id = Column(Integer, primary_key=True)
    # Nullable so config-layer events (created/updated/deleted, before any run
    # exists) can share the same audit table as runtime risk events.
    run_id = Column(
        Integer,
        ForeignKey("sm_strategy_run.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    strategy_id = Column(
        Integer,
        ForeignKey("sm_strategy.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    ts = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind = Column(String(40), nullable=False)
    severity = Column(String(10), nullable=False, default="info")
    leg_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_sm_event_strategy_ts", "strategy_id", "ts"),
        Index("ix_sm_event_user_ts", "user_id", "ts"),
    )
