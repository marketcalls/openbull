"""
Idempotent startup micro-migrations.

OpenBull primarily manages schema via ``Base.metadata.create_all(...)`` on
startup. That handles *new* tables but **does not alter** existing ones, so
when a column is added to a model the live DB drifts away from the code.

This module closes that gap with minimal machinery:

* Each migration is a pure ``(inspector, engine) -> None`` function.
* All migrations are idempotent — they check whether they've already been
  applied and no-op if so.
* They run after ``create_all`` on every startup, so adding a new column to
  an existing table only needs two things: update the model, and append one
  ``_add_column_if_missing`` call here.

For anything more complex than a nullable column add, use Alembic.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _add_column_if_missing(
    engine: Engine, table: str, column: str, column_ddl: str
) -> None:
    """Execute ``ALTER TABLE <table> ADD COLUMN <column> <column_ddl>`` if the
    table exists and the column is missing. Safe to call on every startup."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return  # create_all will create it fresh with every column from the model
    existing_cols = {c["name"] for c in insp.get_columns(table)}
    if column in existing_cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {column_ddl}'))
    logger.info("Schema migration: added column %s.%s", table, column)


def _add_index_if_missing(
    engine: Engine, table: str, index_name: str, columns: list[str]
) -> None:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {ix["name"] for ix in insp.get_indexes(table)}
    if index_name in existing:
        return
    col_list = ", ".join(f'"{c}"' for c in columns)
    with engine.begin() as conn:
        conn.execute(
            text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ({col_list})')
        )
    logger.info("Schema migration: created index %s on %s(%s)", index_name, table, col_list)


def run_startup_migrations() -> None:
    """Apply every pending in-place migration. Called from the app lifespan."""
    engine = create_engine(get_settings().sync_database_url, future=True)
    try:
        # Phase 3: api_logs gained a `mode` column + companion index.
        _add_column_if_missing(engine, "api_logs", "mode", "VARCHAR(10)")
        _add_index_if_missing(
            engine, "api_logs", "idx_api_logs_mode_created", ["mode", "created_at"]
        )
    except Exception:
        logger.exception("Startup schema migration failed")
    finally:
        engine.dispose()
