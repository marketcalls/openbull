"""Centralized logging for OpenBull.

One-call setup via :func:`setup_logging` that installs:

* a console handler (stdout) with optional ANSI colours when a TTY is
  detected and the ``colorama`` package is available,
* a size-rotated all-levels file handler at ``<log_dir>/openbull.log``,
* a size-rotated WARNING+ file handler at ``<log_dir>/openbull-error.log``,
* a :class:`SensitiveDataFilter` that redacts api keys / tokens / passwords,
* a :class:`RequestIdFilter` that stamps the current request id onto every
  record (sourced from :mod:`backend.utils.request_context`),
* a :class:`DBErrorLogHandler` that persists every WARNING+ record into
  the ``error_logs`` table (best-effort, never raises into the caller),
  with periodic row-count trimming to keep the table bounded.

All modules SHOULD obtain loggers via :func:`get_logger` and MUST NOT
call ``logging.basicConfig`` themselves — that would detach handlers
installed here.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from backend.utils.request_context import get_request_id

if TYPE_CHECKING:
    from backend.config import Settings

try:
    from colorama import Fore, Style, init as _colorama_init

    _colorama_init(autoreset=True)
    _COLORAMA_AVAILABLE = True
    _RESET = Style.RESET_ALL
except ImportError:  # pragma: no cover — optional dependency
    _COLORAMA_AVAILABLE = False
    _RESET = ""


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
# Each entry matches "<key>=<value>" or "<key>: <value>" (case-insensitive)
# and "Bearer <token>" / "token <token>" schemes. Only the value is
# replaced; the key stays readable in logs so developers still see WHAT
# was redacted.
SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Auth schemes first (word + whitespace + value). Must run before
    # the key=value rules below, otherwise the key=value rule consumes
    # just the scheme word ("Bearer") and leaves the secret suffix in
    # the clear.
    (re.compile(r"(Bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(token\s+)[\w\-.:]+", re.IGNORECASE), r"\1[REDACTED]"),
    # Header/kv form: key=value or key: value. Value stops at a comma,
    # semicolon, or whitespace — we never want to swallow the rest of
    # a log line. The `access[_-]?token` / `refresh[_-]?token` rules
    # run before the bare `token` rule so that compound keys don't get
    # the bare rule's narrower replacement.
    (re.compile(r"(api[_-]?key\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(access[_-]?token\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(refresh[_-]?token\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(authorization\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(\btoken\s*[=:]\s*)[^\s,;]+", re.IGNORECASE), r"\1[REDACTED]"),
)


def _redact(text: str) -> str:
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Redact credentials from the message template and its args.

    Attached to every configured handler. (Python's logging library
    does **not** run a logger's own filters when a record propagates
    up from a child logger — see ``Logger.callHandlers`` — so filters
    that need to see records from the whole tree must live on each
    handler.) The filter preserves arg types when no redaction was
    needed, so callers that use ``%d`` / ``%f`` format specifiers keep
    working.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                redacted_msg = _redact(record.msg)
                if redacted_msg is not record.msg:
                    record.msg = redacted_msg

            if record.args:
                args_tuple = record.args if isinstance(record.args, tuple) else (record.args,)
                new_args: list[object] = []
                changed = False
                for arg in args_tuple:
                    if isinstance(arg, (str, bytes, bytearray)):
                        as_str = arg if isinstance(arg, str) else arg.decode("utf-8", "replace")
                        redacted = _redact(as_str)
                        if redacted != as_str:
                            new_args.append(redacted)
                            changed = True
                            continue
                    new_args.append(arg)
                if changed:
                    record.args = tuple(new_args)
        except Exception:
            # Never let the filter swallow a real log line.
            pass
        return True


class RequestIdFilter(logging.Filter):
    """Stamp the current contextvar-stored request id onto every record.

    Attached to every configured handler so the ``%(request_id)s``
    format specifier is always resolvable regardless of which logger
    originated the record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


# ---------------------------------------------------------------------------
# Coloured console formatter
# ---------------------------------------------------------------------------
if _COLORAMA_AVAILABLE:
    _LEVEL_COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }
else:
    _LEVEL_COLORS = {}


class ColoredFormatter(logging.Formatter):
    """Plain formatter that paints the level name when colors are enabled."""

    def __init__(self, fmt: str, datefmt: str | None = None, enable_colors: bool = True):
        super().__init__(fmt, datefmt)
        self.enable_colors = enable_colors and _COLORAMA_AVAILABLE and self._supports_color()

    @staticmethod
    def _supports_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        force = os.environ.get("FORCE_COLOR", "").lower()
        if force in ("1", "true", "yes", "on"):
            return True
        if force in ("0", "false", "no", "off"):
            return False
        return bool(getattr(sys.stdout, "isatty", lambda: False)())

    def format(self, record: logging.LogRecord) -> str:
        try:
            rendered = super().format(record)
        except (TypeError, ValueError):
            # Safety net for third-party libs that pass malformed args
            # (e.g. hpack historically sent a str to a %d specifier).
            record.args = None
            rendered = super().format(record)

        if self.enable_colors:
            colour = _LEVEL_COLORS.get(record.levelname, "")
            if colour:
                rendered = rendered.replace(
                    record.levelname, f"{colour}{record.levelname}{_RESET}", 1
                )
        return rendered


# ---------------------------------------------------------------------------
# DB-backed error sink (writes WARNING+ to `error_logs` table)
# ---------------------------------------------------------------------------
class DBErrorLogHandler(logging.Handler):
    """Persist WARNING+ records to the ``error_logs`` table.

    Records are pushed onto a bounded ``queue.Queue`` by ``emit`` (which
    runs on whatever thread the log call happened on — including the
    asyncio event-loop thread, so we must not do blocking I/O there).
    A daemon worker thread drains the queue and inserts rows using a
    *sync* SQLAlchemy engine independent from the app's async engine.

    Contract:

    * ``emit`` never raises and never blocks.
    * The worker loop swallows all exceptions (logging must never cause
      cascading failures when the DB itself is what's broken).
    * The ``sqlalchemy`` / ``asyncpg`` / ``psycopg`` / ``alembic``
      loggers are suppressed here, because writing their errors back to
      the DB would loop forever if the DB is down.
    * After every ``TRIM_EVERY_N_INSERTS`` successful inserts, the
      worker trims the table down to ``max_rows`` rows, keeping storage
      bounded.
    """

    MAX_QUEUE = 1000
    TRIM_EVERY_N_INSERTS = 100
    _SUPPRESSED_PREFIXES = ("sqlalchemy", "asyncpg", "psycopg", "alembic")

    def __init__(
        self,
        sync_database_url: str,
        level: int = logging.WARNING,
        max_rows: int = 50_000,
    ):
        super().__init__(level=level)
        self._q: queue.Queue = queue.Queue(maxsize=self.MAX_QUEUE)
        self._sync_database_url = sync_database_url
        self._max_rows = max_rows
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._worker, name="openbull-errorlog", daemon=True
        )
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            if any(record.name.startswith(p) for p in self._SUPPRESSED_PREFIXES):
                return
            # Separate the rendered message from any exception traceback
            # so the UI can render them distinctly and the `message`
            # column doesn't duplicate the `exc_text` column.
            try:
                rendered_message = record.getMessage()
            except Exception:
                rendered_message = str(record.msg)
            exc_text: str | None = None
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                try:
                    exc_text = formatter.formatException(record.exc_info)
                except Exception:
                    exc_text = None

            payload = {
                "level": record.levelname,
                "logger": record.name[:200],
                "message": rendered_message[:4000],
                "module": (record.module or "")[:200],
                "func_name": (record.funcName or "")[:200],
                "lineno": record.lineno,
                "request_id": getattr(record, "request_id", None),
                "exc_text": exc_text,
            }
            try:
                self._q.put_nowait(payload)
            except queue.Full:
                # Drop silently — bounded memory matters more than
                # completeness here.
                pass
        except Exception:
            pass

    def _worker(self) -> None:
        # Imports local to the worker so import failure of any of these
        # doesn't break normal console logging during ``setup_logging``.
        try:
            from sqlalchemy import create_engine, delete, func, select

            from backend.models.audit import ErrorLog
        except Exception:
            return

        try:
            engine = create_engine(
                self._sync_database_url, pool_pre_ping=True, future=True
            )
        except Exception:
            return

        inserts_since_trim = 0

        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(ErrorLog.__table__.insert().values(**item))
                inserts_since_trim += 1
            except Exception:
                continue

            if inserts_since_trim >= self.TRIM_EVERY_N_INSERTS:
                inserts_since_trim = 0
                try:
                    with engine.begin() as conn:
                        max_id = conn.execute(select(func.max(ErrorLog.id))).scalar()
                        if max_id is not None:
                            cutoff = max_id - self._max_rows
                            if cutoff > 0:
                                conn.execute(
                                    delete(ErrorLog).where(ErrorLog.id <= cutoff)
                                )
                except Exception:
                    pass

    def close(self) -> None:
        """Signal the worker to stop and wait briefly for a flush."""
        self._stop.set()
        try:
            self._thread.join(timeout=2.0)
        finally:
            super().close()


# ---------------------------------------------------------------------------
# Request-path access log filter: skip /health spam
# ---------------------------------------------------------------------------
class _AccessLogPathFilter(logging.Filter):
    """Drop access-log lines for noisy health-probe endpoints."""

    SKIP = ("GET /health ", "HEAD /health ")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if any(s in msg for s in self.SKIP):
                return False
        except Exception:
            pass
        return True


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
_DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s:%(lineno)d - %(message)s"
)

_setup_lock = threading.Lock()
_setup_done = False
_setup_logger = logging.getLogger("openbull.logging")


def setup_logging(settings: Settings) -> None:
    """Install handlers on the root logger. Idempotent.

    Must be called once, early, before any user code logs anything it
    cares about seeing on disk. Subsequent calls are no-ops and emit a
    warning on the ``openbull.logging`` logger.
    """
    global _setup_done
    with _setup_lock:
        if _setup_done:
            _setup_logger.warning(
                "setup_logging called more than once — ignoring subsequent call"
            )
            return

        log_level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
        log_dir = Path(settings.log_dir)
        log_to_file = bool(settings.log_to_file)
        max_bytes = int(settings.log_file_max_mb) * 1024 * 1024
        backup_count = int(settings.log_file_backup_count)
        enable_colors = bool(settings.log_colors)

        root = logging.getLogger()
        root.setLevel(log_level)
        # Drop any handlers/filters installed by earlier basicConfig calls
        # so we own the formatting stack exclusively.
        for h in list(root.handlers):
            root.removeHandler(h)
        for f in list(root.filters):
            root.removeFilter(f)

        console_formatter = ColoredFormatter(_DEFAULT_FORMAT, enable_colors=enable_colors)
        file_formatter = logging.Formatter(_DEFAULT_FORMAT)

        # Console handler
        console = logging.StreamHandler(stream=sys.stdout)
        console.setFormatter(console_formatter)
        _apply_standard_filters(console)
        root.addHandler(console)

        if log_to_file:
            _attach_file_handlers(root, log_dir, max_bytes, backup_count, file_formatter)

        # DB-backed error sink
        try:
            db_handler = DBErrorLogHandler(
                settings.sync_database_url,
                max_rows=int(settings.error_log_db_max_rows),
            )
            db_handler.setFormatter(file_formatter)
            _apply_standard_filters(db_handler)
            root.addHandler(db_handler)
        except Exception as exc:  # pragma: no cover
            sys.stderr.write(f"[logging] failed to attach DB error handler: {exc}\n")

        _suppress_third_party_noise(log_level)

        _setup_done = True


def _apply_standard_filters(handler: logging.Handler) -> None:
    """Attach the request-id and sensitive-data filters to a handler.

    Kept DRY so every handler in :func:`setup_logging` wears the same
    stack. Each call creates fresh filter instances — sharing instances
    across handlers is safe in practice but not required, and keeping
    them per-handler makes reasoning about state strictly local.
    """
    handler.addFilter(RequestIdFilter())
    handler.addFilter(SensitiveDataFilter())


def _attach_file_handlers(
    root: logging.Logger,
    log_dir: Path,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
) -> None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        all_handler = RotatingFileHandler(
            filename=str(log_dir / "openbull.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        all_handler.setFormatter(formatter)
        _apply_standard_filters(all_handler)
        root.addHandler(all_handler)

        error_handler = RotatingFileHandler(
            filename=str(log_dir / "openbull-error.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(formatter)
        _apply_standard_filters(error_handler)
        root.addHandler(error_handler)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"[logging] failed to attach file handlers: {exc}\n")


def _suppress_third_party_noise(app_level: int) -> None:
    """Quiet down chatty dependencies, but only when the app itself is
    running at INFO or higher. If the operator set ``LOG_LEVEL=DEBUG``
    they probably want to see SQL and HTTP client internals — respect
    that."""
    if app_level > logging.DEBUG:
        noisy = (
            "httpx", "httpcore", "urllib3", "asyncio",
            "uvicorn.access", "websockets", "websockets.server",
            "hpack", "hpack.hpack", "multipart", "multipart.multipart",
            "sqlalchemy.engine",
        )
        for name in noisy:
            logging.getLogger(name).setLevel(logging.WARNING)

    # Some libraries (notably SQLAlchemy with echo=True) attach their
    # own handlers directly to their logger. That bypasses our
    # formatting AND produces a second line when the record also
    # propagates up to root. Take ownership by removing those foreign
    # handlers — we keep propagation on so records still flow through
    # our handlers.
    for owned in ("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
        lg = logging.getLogger(owned)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.propagate = True

    # The health-probe filter is cheap enough to always apply to our
    # access logger.
    logging.getLogger("openbull.access").addFilter(_AccessLogPathFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger. Prefer this over ``logging.getLogger``."""
    return logging.getLogger(name)


__all__ = [
    "ColoredFormatter",
    "DBErrorLogHandler",
    "RequestIdFilter",
    "SensitiveDataFilter",
    "get_logger",
    "setup_logging",
]
