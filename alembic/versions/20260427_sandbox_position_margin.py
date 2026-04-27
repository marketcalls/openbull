"""Sandbox margin transfer-on-fill: position-level margin tracking.

Phase 2c. Aligns the sandbox margin lifecycle with openalgo:

* margin booked at order placement is transferred to the position when the
  order fills (was previously released back to ``available`` on fill, which
  let users re-deploy the same capital indefinitely);
* on close / reduce, margin is released from the position pro-rata together
  with realized PnL in a single transaction.

The two new columns this migration adds make that bookkeeping possible:

* ``sandbox_positions.margin_blocked`` — current margin attached to the
  position (= sum of order margins transferred minus pro-rata releases).
* ``sandbox_positions.today_realized_pnl`` and
  ``sandbox_funds.today_realized_pnl`` — session-only realized PnL bucket
  (cumulative ``realized_pnl`` is preserved across daily resets).

This file is mirrored by an idempotent in-place migration in
``backend/utils/schema_migrations.py`` so existing dev databases (which were
created via ``Base.metadata.create_all`` without alembic stamping) get the
columns on the next app start. To stay compatible with both paths we detect
existing columns here and skip them — running ``migrate_all.py`` does both
steps in sequence and must remain a no-op when called twice.

Revision ID: 20260427_sbx_margin
Revises:
Create Date: 2026-04-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260427_sbx_margin"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _add_if_missing(table: str, name: str, column_type: sa.types.TypeEngine) -> None:
    if name in _existing_columns(table):
        return
    op.add_column(
        table,
        sa.Column(name, column_type, nullable=False, server_default=sa.text("0")),
    )


def upgrade() -> None:
    _add_if_missing("sandbox_positions", "margin_blocked", sa.Float())
    _add_if_missing("sandbox_positions", "today_realized_pnl", sa.Float())
    _add_if_missing("sandbox_funds", "today_realized_pnl", sa.Float())


def downgrade() -> None:
    for table, col in (
        ("sandbox_funds", "today_realized_pnl"),
        ("sandbox_positions", "today_realized_pnl"),
        ("sandbox_positions", "margin_blocked"),
    ):
        if col in _existing_columns(table):
            op.drop_column(table, col)
