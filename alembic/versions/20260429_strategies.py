"""Saved strategies table.

Creates the ``strategies`` table that backs the Strategy Builder save/load
flow and the Strategy Portfolio page. Mirrors
:class:`backend.models.strategies.Strategy`.

Idempotent: skips creation if the table is already present (which it will
be on dev databases that came up via ``Base.metadata.create_all`` before
this migration was authored).

Revision ID: 20260429_strategies
Revises: 20260427_sbx_margin
Create Date: 2026-04-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260429_strategies"
down_revision: Union[str, None] = "20260427_sbx_margin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not _table_exists(table):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade() -> None:
    if not _table_exists("strategies"):
        op.create_table(
            "strategies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("underlying", sa.String(length=50), nullable=False),
            sa.Column("exchange", sa.String(length=20), nullable=False),
            sa.Column("expiry_date", sa.String(length=20), nullable=True),
            sa.Column(
                "mode",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'live'"),
            ),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'active'"),
            ),
            sa.Column("legs", JSONB(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _index_exists("strategies", "idx_strategies_user_mode_status"):
        op.create_index(
            "idx_strategies_user_mode_status",
            "strategies",
            ["user_id", "mode", "status"],
        )

    if not _index_exists("strategies", "idx_strategies_user_underlying"):
        op.create_index(
            "idx_strategies_user_underlying",
            "strategies",
            ["user_id", "underlying"],
        )


def downgrade() -> None:
    if _index_exists("strategies", "idx_strategies_user_underlying"):
        op.drop_index("idx_strategies_user_underlying", table_name="strategies")
    if _index_exists("strategies", "idx_strategies_user_mode_status"):
        op.drop_index("idx_strategies_user_mode_status", table_name="strategies")
    if _table_exists("strategies"):
        op.drop_table("strategies")
