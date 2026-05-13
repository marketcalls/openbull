"""Signal-mode strategy columns.

Adds two columns to ``sm_strategy`` for the signal-driven strategy mode
documented in ``docs/plan/strategy-signal-mode.md``:

* ``strategy_kind``  TEXT NOT NULL DEFAULT 'batch'
* ``direction``      TEXT NOT NULL DEFAULT 'both'

The defaults are chosen so every existing row keeps its current
semantics without backfill. Batch-mode strategies have always behaved as
``strategy_kind='batch'`` with no direction filtering, which is exactly
what ``'batch'`` + ``'both'`` encode.

Per-leg signal-mode fields (``symbol``, ``exchange``, ``side``, ``qty``)
live inside the existing ``legs`` JSONB column and need no DDL.

Revision ID: 20260513_signal_mode
Revises: 20260429_strategies
Create Date: 2026-05-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260513_signal_mode"
down_revision: Union[str, None] = "20260429_strategies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("sm_strategy", "strategy_kind"):
        op.add_column(
            "sm_strategy",
            sa.Column(
                "strategy_kind",
                sa.String(length=20),
                nullable=False,
                server_default="batch",
            ),
        )
    if not _column_exists("sm_strategy", "direction"):
        op.add_column(
            "sm_strategy",
            sa.Column(
                "direction",
                sa.String(length=20),
                nullable=False,
                server_default="both",
            ),
        )


def downgrade() -> None:
    if _column_exists("sm_strategy", "direction"):
        op.drop_column("sm_strategy", "direction")
    if _column_exists("sm_strategy", "strategy_kind"):
        op.drop_column("sm_strategy", "strategy_kind")
