"""
Shared sync SQLAlchemy engine for the sandbox layer.

The sandbox engine (order placement, execution, position book, fund book) uses
a sync psycopg driver instead of the app's async engine. Rationale:

* The existing service layer (``order_service``, ``orderbook_service`` …) is
  already sync. Mixing async DB sessions into sync callers would require
  wrapping every call in ``asyncio.run_coroutine_threadsafe`` or similar —
  ugly and error-prone.
* The tick-driven :mod:`backend.sandbox.execution_engine` is a MarketDataCache
  subscriber running on a synchronous thread. It needs sync DB access.
* Errorlog + APILog writers already use this pattern (daemon thread + sync
  engine via ``settings.sync_database_url``). Keeping things consistent.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker | None = None
_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                url = get_settings().sync_database_url
                _engine = create_engine(url, pool_pre_ping=True, future=True)
                _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
                logger.debug("Sandbox sync engine initialized")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction-scoped session. Commits on success, rolls back on exception."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    sess = _session_factory()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
