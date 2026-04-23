"""
Background writer for the ``api_logs`` table.

The middleware calls :func:`enqueue` per authenticated request. A daemon
thread drains a bounded :class:`queue.Queue` and inserts rows with a *sync*
SQLAlchemy engine (``+psycopg``), so the async event loop never waits on
the log write and a DB outage cannot backpressure request handling.

Table growth is capped: after every ``TRIM_EVERY_N_INSERTS`` successful
inserts the worker trims ``api_logs`` down to ``max_rows`` rows. Attacker
floods cannot blow the table up because the middleware skips rows whose
``request.state.user_id`` is missing (unauthenticated traffic).
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)


class ApiLogWriter:
    MAX_QUEUE = 2000
    TRIM_EVERY_N_INSERTS = 100

    def __init__(self, sync_database_url: str, max_rows: int = 100_000):
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.MAX_QUEUE)
        self._sync_database_url = sync_database_url
        self._max_rows = max_rows
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._drop_count = 0
        self._last_drop_log = 0.0

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._worker, name="openbull-apilog", daemon=True
        )
        self._thread.start()
        logger.debug("ApiLogWriter started (max_rows=%d)", self._max_rows)

    def enqueue(self, row: dict[str, Any]) -> None:
        """Non-blocking. Drops silently if the queue is full; bounded memory matters."""
        try:
            self._q.put_nowait(row)
        except queue.Full:
            self._drop_count += 1
            import time as _t

            now = _t.time()
            if now - self._last_drop_log > 30:
                logger.warning(
                    "ApiLogWriter queue full — dropped %d entries in last window",
                    self._drop_count,
                )
                self._drop_count = 0
                self._last_drop_log = now

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- worker --------------------------------------------------------

    def _worker(self) -> None:
        try:
            from sqlalchemy import create_engine, delete, func, select

            from backend.models.audit import ApiLog
        except Exception:
            logger.exception("ApiLogWriter failed to import deps; worker exiting")
            return

        try:
            engine = create_engine(
                self._sync_database_url, pool_pre_ping=True, future=True
            )
        except Exception:
            logger.exception("ApiLogWriter failed to create engine; worker exiting")
            return

        inserts_since_trim = 0

        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(ApiLog.__table__.insert().values(**item))
                inserts_since_trim += 1
            except Exception:
                # Never let a bad row / DB blip kill the worker
                continue

            if inserts_since_trim >= self.TRIM_EVERY_N_INSERTS:
                inserts_since_trim = 0
                try:
                    with engine.begin() as conn:
                        max_id = conn.execute(select(func.max(ApiLog.id))).scalar()
                        if max_id is not None:
                            cutoff = max_id - self._max_rows
                            if cutoff > 0:
                                conn.execute(
                                    delete(ApiLog).where(ApiLog.id <= cutoff)
                                )
                except Exception:
                    # Best-effort trim; try again next cycle
                    pass


_writer: ApiLogWriter | None = None
_writer_lock = threading.Lock()


def get_writer() -> ApiLogWriter | None:
    return _writer


def init_writer(sync_database_url: str, max_rows: int) -> ApiLogWriter:
    """Lazily construct the singleton writer. Call once from app startup."""
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = ApiLogWriter(sync_database_url, max_rows=max_rows)
            _writer.start()
    return _writer


def enqueue(row: dict[str, Any]) -> None:
    """Shortcut — silently no-op if the writer hasn't been initialised yet."""
    w = _writer
    if w is not None:
        w.enqueue(row)
