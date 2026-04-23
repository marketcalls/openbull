"""Typed accessors over the ``sandbox_config`` key/value table."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.sandbox import SandboxConfig
from backend.sandbox._db import session_scope
from backend.sandbox.defaults import DEFAULT_CONFIGS, LEVERAGE_KEYS

logger = logging.getLogger(__name__)


def seed_defaults() -> None:
    """Insert any missing default keys. Safe to call at every app startup."""
    with session_scope() as db:
        existing = {
            row.key for row in db.execute(select(SandboxConfig.key)).scalars().all()
        }
        added = 0
        for key, value, desc in DEFAULT_CONFIGS:
            if key not in existing:
                db.add(SandboxConfig(key=key, value=value, description=desc))
                added += 1
        if added:
            logger.info("Seeded %d sandbox_config defaults", added)


def _read_str(db: Session, key: str, fallback: str) -> str:
    row = db.execute(
        select(SandboxConfig).where(SandboxConfig.key == key)
    ).scalar_one_or_none()
    return row.value if row else fallback


def _read_float(db: Session, key: str, fallback: float) -> float:
    try:
        return float(_read_str(db, key, str(fallback)))
    except ValueError:
        return fallback


def _read_int(db: Session, key: str, fallback: int) -> int:
    try:
        return int(_read_str(db, key, str(fallback)))
    except ValueError:
        return fallback


def get_starting_capital(db: Session | None = None) -> float:
    if db is None:
        with session_scope() as s:
            return _read_float(s, "starting_capital", 10_000_000.0)
    return _read_float(db, "starting_capital", 10_000_000.0)


def get_leverage(product: str, db: Session | None = None) -> float:
    key = LEVERAGE_KEYS.get(product.upper(), "leverage_nrml")
    if db is None:
        with session_scope() as s:
            return _read_float(s, key, 1.0)
    return _read_float(db, key, 1.0)


def get_order_check_interval(db: Session | None = None) -> int:
    if db is None:
        with session_scope() as s:
            return max(1, _read_int(s, "order_check_interval_seconds", 5))
    return max(1, _read_int(db, "order_check_interval_seconds", 5))


def get_all_configs() -> dict[str, dict[str, str | bool]]:
    """Return the full config map for the /sandbox UI (phase 2b)."""
    with session_scope() as db:
        rows = db.execute(select(SandboxConfig)).scalars().all()
        return {
            r.key: {
                "value": r.value,
                "description": r.description or "",
                "is_editable": r.is_editable,
            }
            for r in rows
        }


def set_config(key: str, value: str) -> bool:
    """Update a single config row. Returns True if changed, False if not found."""
    with session_scope() as db:
        row = db.execute(
            select(SandboxConfig).where(SandboxConfig.key == key)
        ).scalar_one_or_none()
        if row is None:
            return False
        if not row.is_editable:
            return False
        row.value = value
    return True
