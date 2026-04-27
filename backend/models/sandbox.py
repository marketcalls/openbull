"""
Sandbox (simulated trading) tables.

These live in the same Postgres database as the rest of the app — no
separate SQLite file like openalgo uses. Partitioned by ``user_id`` so
every user has an independent simulated account.

Populated exclusively by :mod:`backend.sandbox` and
:mod:`backend.services.sandbox_service`. Live-mode code never touches these.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)

from backend.database import Base


class SandboxOrder(Base):
    """One row per simulated order. Fills produce rows in SandboxTrade."""

    __tablename__ = "sandbox_orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    orderid = Column(String(40), unique=True, nullable=False, index=True)

    symbol = Column(String(100), nullable=False)
    exchange = Column(String(20), nullable=False)
    action = Column(String(8), nullable=False)  # BUY | SELL
    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, nullable=False, default=0)
    pricetype = Column(String(10), nullable=False)  # MARKET | LIMIT | SL | SL-M
    product = Column(String(10), nullable=False)  # CNC | NRML | MIS
    price = Column(Float, nullable=False, default=0.0)
    trigger_price = Column(Float, nullable=False, default=0.0)
    average_price = Column(Float, nullable=False, default=0.0)

    # open | complete | cancelled | rejected | trigger_pending
    status = Column(String(20), nullable=False, default="open", index=True)
    rejection_reason = Column(String(500), nullable=True)

    strategy = Column(String(100), nullable=True)
    margin_blocked = Column(Float, nullable=False, default=0.0)

    order_timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    update_timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_sbx_orders_user_status", "user_id", "status"),
        Index("idx_sbx_orders_symbol_status", "symbol", "exchange", "status"),
    )


class SandboxTrade(Base):
    """Execution records. Created when a SandboxOrder fills (fully or partially)."""

    __tablename__ = "sandbox_trades"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    orderid = Column(String(40), nullable=False, index=True)
    tradeid = Column(String(40), unique=True, nullable=False)

    symbol = Column(String(100), nullable=False)
    exchange = Column(String(20), nullable=False)
    action = Column(String(8), nullable=False)
    quantity = Column(Integer, nullable=False)
    average_price = Column(Float, nullable=False)
    product = Column(String(10), nullable=False)
    strategy = Column(String(100), nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sbx_trades_user_ts", "user_id", "timestamp"),
    )


class SandboxPosition(Base):
    """Running net position per (user, symbol, exchange, product). Updated on every fill."""

    __tablename__ = "sandbox_positions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(100), nullable=False)
    exchange = Column(String(20), nullable=False)
    product = Column(String(10), nullable=False)

    net_quantity = Column(Integer, nullable=False, default=0)
    average_price = Column(Float, nullable=False, default=0.0)
    ltp = Column(Float, nullable=False, default=0.0)
    pnl = Column(Float, nullable=False, default=0.0)  # unrealized + realized for this symbol
    realized_pnl = Column(Float, nullable=False, default=0.0)
    today_realized_pnl = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)

    # Margin currently locked against this position. Transferred from
    # ``SandboxOrder.margin_blocked`` when the order fills; pro-rata released
    # back to ``SandboxFund.available`` when the position is reduced or closed.
    margin_blocked = Column(Float, nullable=False, default=0.0)

    # Intraday tracking — reset daily by squareoff scheduler (Phase 2b)
    day_buy_quantity = Column(Integer, nullable=False, default=0)
    day_buy_value = Column(Float, nullable=False, default=0.0)
    day_sell_quantity = Column(Integer, nullable=False, default=0)
    day_sell_value = Column(Float, nullable=False, default=0.0)

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_sbx_positions_user_sym", "user_id", "symbol", "exchange", "product", unique=True),
    )


class SandboxHolding(Base):
    """T+1 settled CNC positions. Phase 2a stores the schema but defers settlement logic to 2b."""

    __tablename__ = "sandbox_holdings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(100), nullable=False)
    exchange = Column(String(20), nullable=False)

    quantity = Column(Integer, nullable=False, default=0)
    average_price = Column(Float, nullable=False, default=0.0)
    ltp = Column(Float, nullable=False, default=0.0)
    pnl = Column(Float, nullable=False, default=0.0)
    pnlpercent = Column(Float, nullable=False, default=0.0)

    # T+1 settlement date — populated when the position is moved into
    # holdings by the EOD scheduler. Lets the UI show "settled on dd-MMM-yyyy"
    # the same way openalgo's holdings table does.
    settlement_date = Column(String(10), nullable=True)  # "YYYY-MM-DD" IST

    added_on = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sbx_holdings_user_sym", "user_id", "symbol", "exchange", unique=True),
    )


class SandboxFund(Base):
    """User's simulated capital + margin usage + PnL. One row per user."""

    __tablename__ = "sandbox_funds"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False, index=True)

    starting_capital = Column(Float, nullable=False, default=10_000_000.0)  # 1 Cr
    available = Column(Float, nullable=False, default=10_000_000.0)
    used_margin = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    today_realized_pnl = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)

    reset_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SandboxConfig(Base):
    """Global sandbox configuration key/value store (capital, leverage, squareoff times…)."""

    __tablename__ = "sandbox_config"

    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    description = Column(String(500), nullable=True)
    is_editable = Column(Boolean, nullable=False, default=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SandboxDailyPnL(Base):
    """End-of-day P&L snapshot per user. One row per (user_id, snapshot_date)."""

    __tablename__ = "sandbox_daily_pnl"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    snapshot_date = Column(String(10), nullable=False)  # "YYYY-MM-DD" IST

    starting_capital = Column(Float, nullable=False, default=0.0)
    available = Column(Float, nullable=False, default=0.0)
    used_margin = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    positions_pnl = Column(Float, nullable=False, default=0.0)
    holdings_pnl = Column(Float, nullable=False, default=0.0)
    trades_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sbx_daily_pnl_user_date", "user_id", "snapshot_date", unique=True),
    )
