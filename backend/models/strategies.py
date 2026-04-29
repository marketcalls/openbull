"""
Saved option strategies (Strategy Builder + Strategy Portfolio).

A row here is a user-curated multi-leg option setup — saved from the Strategy
Builder, reloaded into it via ``?load=<id>``, and tracked live in the Strategy
Portfolio. The legs are persisted as JSONB so the schema stays loose enough
to absorb future UI fields (custom tags, manual exit prices, broker order ids
on basket-execute, etc.) without a migration per change.

Multi-user — every row is scoped by ``user_id`` and queries always filter on
the authenticated user. Tagged with ``mode`` so live-saved and sandbox-saved
strategies can coexist in the same table without cross-contaminating P&L
views.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.database import Base


class Strategy(Base):
    """A saved multi-leg option strategy.

    The ``legs`` JSONB column stores a list of leg dicts. Each leg looks like:

    .. code-block:: json

        {
          "id": "<uuid>",
          "action": "BUY" | "SELL",
          "option_type": "CE" | "PE",
          "strike": 25000.0,
          "lots": 1,
          "lot_size": 75,
          "expiry_date": "28OCT25",
          "symbol": "NIFTY28OCT2525000CE",
          "entry_price": 100.5,
          "exit_price": null,
          "status": "open" | "closed" | "expired",
          "entry_time": "2026-04-29T10:30:00Z",
          "exit_time": null
        }

    Per-leg ``expiry_date`` is what allows calendar / diagonal strategies —
    the top-level ``expiry_date`` column is just the nearest leg's expiry
    used for list filtering.
    """

    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(200), nullable=False)
    underlying = Column(String(50), nullable=False)
    exchange = Column(String(20), nullable=False)
    expiry_date = Column(String(20), nullable=True)

    # Trading mode active when the strategy was saved. Lets a "live" save and
    # a "sandbox" save of the same setup live in the same table without
    # P&L bleed between them. Indexed via the composite below.
    mode = Column(String(20), nullable=False, default="live")

    # active | closed | expired. "expired" is set by a future EOD job when
    # every leg's expiry has passed; "closed" is set when the user marks the
    # strategy closed in the Portfolio UI.
    status = Column(String(20), nullable=False, default="active")

    legs = Column(JSONB, nullable=False)
    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_strategies_user_mode_status",
            "user_id",
            "mode",
            "status",
        ),
        Index("idx_strategies_user_underlying", "user_id", "underlying"),
    )
