#!/usr/bin/env python3
"""
Centralized DB migration runner for OpenBull.

Usage:
    uv run migrate_all.py            # apply everything
    uv run migrate_all.py --check    # report drift, no writes
    uv run migrate_all.py --drop     # DESTRUCTIVE: drop+recreate the schema (dev only)

What it does, in order:

1. Imports every SQLAlchemy model so ``Base.metadata`` is fully populated.
2. ``Base.metadata.create_all`` — creates any *missing* tables (idempotent).
3. Runs the in-place column / index migrations from
   ``backend.utils.schema_migrations`` (idempotent ALTER TABLE wrappers used
   by the running app at startup).
4. ``alembic upgrade head`` — applies any formal alembic revisions on top.

The ``schema_migrations`` step covers dev databases that were created via
``Base.metadata.create_all`` and never alembic-stamped, while the alembic step
handles environments that *were* stamped. Running both is safe — each layer
no-ops when its work is already done.

Connects to whatever database ``backend.config.get_settings().sync_database_url``
points at. Honours the same ``.env`` the app honours, so this is the right
script to run in CI, in a container entrypoint, on a fresh dev machine, or
against a freshly-restored backup.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_all")


def _import_all_models() -> None:
    """Force-import every module that defines a SQLAlchemy model so the
    ``Base.metadata`` registry sees them all before ``create_all`` runs.

    ``backend/models/__init__.py`` historically only imports a subset; the
    sandbox / audit / settings models load lazily via routers and services in
    the running app. For a standalone migration we have to import them
    explicitly."""
    import backend.models  # noqa: F401 — populates Base.metadata
    import backend.models.user  # noqa: F401
    import backend.models.auth  # noqa: F401
    import backend.models.broker_config  # noqa: F401
    import backend.models.symbol  # noqa: F401
    import backend.models.settings  # noqa: F401
    import backend.models.audit  # noqa: F401  (login_attempts, active_sessions, error_logs, api_logs)
    import backend.models.sandbox  # noqa: F401  (sandbox_*)
    import backend.models.strategies  # noqa: F401  (saved option strategies)


def step_create_all(engine) -> None:
    from backend.database import Base

    log.info("Step 1/3 : Base.metadata.create_all (creates any missing tables)")
    Base.metadata.create_all(bind=engine)
    log.info("           done — %d tables in metadata", len(Base.metadata.tables))


def step_inplace_migrations() -> None:
    log.info("Step 2/3 : in-place column / index migrations (schema_migrations.py)")
    from backend.utils.schema_migrations import run_startup_migrations

    run_startup_migrations()
    log.info("           done")


def step_alembic_upgrade(database_url: str) -> None:
    log.info("Step 3/3 : alembic upgrade head")
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        log.warning("           alembic not installed — skipping")
        return

    cfg_path = REPO_ROOT / "alembic.ini"
    if not cfg_path.exists():
        log.warning("           %s not found — skipping", cfg_path)
        return

    cfg = Config(str(cfg_path))
    # Override the URL so this script honours .env-driven settings rather than
    # the hard-coded value in alembic.ini.
    cfg.set_main_option("sqlalchemy.url", database_url)

    versions_dir = REPO_ROOT / "alembic" / "versions"
    has_revisions = versions_dir.exists() and any(versions_dir.glob("*.py"))
    if not has_revisions:
        log.info("           no alembic revisions present — skipping")
        return

    command.upgrade(cfg, "head")
    log.info("           done")


def step_check(engine) -> int:
    """Report what *would* be created or altered. Returns exit code (0 = clean)."""
    from sqlalchemy import inspect
    from backend.database import Base

    log.info("Drift check (read-only)")
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    declared = set(Base.metadata.tables.keys())

    missing_tables = declared - existing
    extra_tables = existing - declared

    if missing_tables:
        log.warning("  Missing tables (would be created): %s", sorted(missing_tables))
    if extra_tables:
        log.info("  Tables present but not in models: %s", sorted(extra_tables))

    drift = bool(missing_tables)
    if not drift:
        log.info("  No missing tables.")
    return 0 if not drift else 1


def step_drop_all(engine) -> None:
    """Dev-only: drop every table the models know about. Asks for confirmation."""
    from backend.database import Base

    log.warning("DESTRUCTIVE: about to DROP every modelled table.")
    log.warning("  Database: %s", engine.url)
    confirm = input("  Type the database name to confirm: ").strip()
    db_name = engine.url.database or ""
    if confirm != db_name:
        log.error("  Confirmation did not match (%r != %r). Aborting.", confirm, db_name)
        sys.exit(2)
    Base.metadata.drop_all(bind=engine)
    log.info("  Dropped %d tables.", len(Base.metadata.tables))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all OpenBull DB migrations.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift, do not write.",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="DESTRUCTIVE: drop every modelled table before migrating (dev only).",
    )
    args = parser.parse_args()

    _import_all_models()

    from sqlalchemy import create_engine

    from backend.config import get_settings

    settings = get_settings()
    url = settings.sync_database_url
    log.info("Database: %s", url.split("@")[-1])  # don't log password

    engine = create_engine(url, future=True)
    try:
        if args.check:
            return step_check(engine)

        if args.drop:
            step_drop_all(engine)

        step_create_all(engine)
        step_inplace_migrations()
        step_alembic_upgrade(url)

        log.info("All migrations applied.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
