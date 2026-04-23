"""
Single daemon thread that drives every time-based sandbox job.

Wakes every 60 seconds, reads IST wall clock, and for each job checks:

1. Is it due (current HH:MM >= scheduled HH:MM, or scheduled day == today)?
2. Has it already run today / this week?

Re-entrant on a single job name: a job's "last run date" lives in a
reserved ``sandbox_config`` row (keys prefixed ``_sched_last_`` so the UI
hides them). That means if the app is down at the scheduled time, the job
runs late on the next wake rather than being silently skipped.

Jobs (all times IST):

* **Auto square-off** — one per exchange bucket, runs the moment the
  configured cut-off is reached.
* **T+1 settlement + EOD P&L snapshot** — fires at 23:55 IST each day so
  tomorrow's opening bell already has yesterday's P&L stored.
* **Weekly reset** — if ``reset_day`` is a weekday name, triggers on that
  day at ``reset_time``.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select

from backend.models.sandbox import SandboxConfig
from backend.sandbox._db import session_scope
from backend.sandbox import pnl_snapshot, squareoff, t1_settle, weekly_reset

logger = logging.getLogger(__name__)

# IST is fixed UTC+05:30 year-round — no DST — so we don't need pytz.
IST = timezone(timedelta(hours=5, minutes=30))

CHECK_INTERVAL_SECONDS = 60

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# -- state helpers ---------------------------------------------------------

def _read_cfg(key: str, fallback: str) -> str:
    with session_scope() as db:
        row = db.execute(
            select(SandboxConfig).where(SandboxConfig.key == key)
        ).scalar_one_or_none()
        return row.value if row else fallback


def _read_last_run(job_key: str) -> str:
    """Return last-run ISO date for the job, or empty string if never."""
    return _read_cfg(f"_sched_last_{job_key}", "")


def _mark_ran(job_key: str, today_iso: str) -> None:
    with session_scope() as db:
        row = db.execute(
            select(SandboxConfig).where(SandboxConfig.key == f"_sched_last_{job_key}")
        ).scalar_one_or_none()
        if row is None:
            db.add(
                SandboxConfig(
                    key=f"_sched_last_{job_key}",
                    value=today_iso,
                    description="Scheduler bookkeeping — last run date",
                    is_editable=False,
                )
            )
        else:
            row.value = today_iso


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    try:
        hh, mm = s.strip().split(":", 1)
        return int(hh), int(mm)
    except Exception:
        return None


# -- due-checks -----------------------------------------------------------

def _ist_now() -> datetime:
    return datetime.now(tz=IST)


def _due_today(hhmm: str, now: datetime, already_ran_today: bool) -> bool:
    """True if the configured HH:MM has been reached today and we haven't run."""
    if already_ran_today:
        return False
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        return False
    target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    return now >= target


def _due_weekly(day_name: str, hhmm: str, now: datetime, already_ran_week: bool) -> bool:
    if already_ran_week:
        return False
    if day_name not in DAY_NAMES:
        return False  # "Never" or invalid
    if now.weekday() != DAY_NAMES.index(day_name):
        return False
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        return False
    target = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    return now >= target


# -- main loop -----------------------------------------------------------

_running = False
_stop = threading.Event()
_thread: threading.Thread | None = None


def _run_once_safely(name: str, fn: Callable[[], int | None]) -> None:
    try:
        result = fn()
        logger.info("sandbox scheduler: %s ran (%s)", name, result)
    except Exception:
        logger.exception("sandbox scheduler: %s raised", name)


def _tick() -> None:
    """One evaluation pass. Runs everything due and marks them done."""
    now = _ist_now()
    today_iso = now.date().isoformat()
    # Week key — Monday-anchored ISO week is fine, we just need a bucket.
    week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    # --- Square-off per bucket ---
    for bucket_key, cfg_key in (
        ("nse_nfo_bse_bfo", "squareoff_nse_nfo_bse_bfo"),
        ("cds", "squareoff_cds"),
        ("mcx", "squareoff_mcx"),
    ):
        job = f"squareoff_{bucket_key}"
        already = _read_last_run(job) == today_iso
        if _due_today(_read_cfg(cfg_key, "15:15"), now, already):
            _run_once_safely(job, lambda bk=bucket_key: squareoff.squareoff_bucket(bk))
            _mark_ran(job, today_iso)

    # --- EOD settlement + P&L snapshot (23:55 IST) ---
    eod_job = "eod_settle"
    if _due_today("23:55", now, _read_last_run(eod_job) == today_iso):
        _run_once_safely("t1_settle", t1_settle.settle_cnc_to_holdings)
        _run_once_safely(
            "pnl_snapshot",
            lambda: pnl_snapshot.snapshot_for_date(date.fromisoformat(today_iso)),
        )
        _mark_ran(eod_job, today_iso)

    # --- Weekly reset ---
    reset_job = "weekly_reset"
    reset_day = _read_cfg("reset_day", "Sunday")
    if reset_day != "Never":
        reset_time = _read_cfg("reset_time", "00:00")
        if _due_weekly(reset_day, reset_time, now, _read_last_run(reset_job) == week_key):
            _run_once_safely("weekly_reset", weekly_reset.wipe_all_users)
            _mark_ran(reset_job, week_key)


def _loop() -> None:
    while not _stop.is_set():
        try:
            _tick()
        except Exception:
            logger.exception("sandbox scheduler tick raised")
        _stop.wait(CHECK_INTERVAL_SECONDS)


def start() -> None:
    """Launch the scheduler daemon. Idempotent."""
    global _running, _thread
    if _running:
        return
    _running = True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sandbox-scheduler", daemon=True)
    _thread.start()
    logger.info("sandbox scheduler started")


def stop(timeout: float = 2.0) -> None:
    global _running, _thread
    if not _running:
        return
    _running = False
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=timeout)
    _thread = None
