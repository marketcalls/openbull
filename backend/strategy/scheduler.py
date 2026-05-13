"""APScheduler-backed cron triggers for strategy runs.

Plan Section 10. Per-strategy cron job that calls ``engine.start_run`` at
the configured weekday + ``HH:MM`` IST. Optional auto-stop cron at the
configured ``auto_stop_time``. ``strategy.scheduler`` jsonb is the source
of truth; APScheduler's in-memory store is rebuilt from the DB on every
boot (no extra job-store dependency).

Idempotency: scheduler-triggered ``start_run`` is a no-op when the
strategy is already running (the engine itself enforces that via
``status != 'stopped'`` check). Cron fires Mon-Fri by default; the
user can pick any weekday subset in the wizard.

Live mode safety: if ``scheduler.default_mode='live'`` but the strategy
isn't ``live_enabled``, the trigger logs a critical event and refuses
to start — matches the same gate as the manual /start endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from backend.database import async_session
from backend.events.strategy_events import RunStartedEvent, StrategyConfigEvent
from backend.models.strategy_module import SmStrategy
from backend.utils.event_bus import bus

logger = logging.getLogger(__name__)

IST_TZ = "Asia/Kolkata"

# Map config weekday strings to APScheduler's day_of_week tokens
_WEEKDAY_TOKENS = {
    "MON": "mon", "TUE": "tue", "WED": "wed", "THU": "thu",
    "FRI": "fri", "SAT": "sat", "SUN": "sun",
}

_scheduler: Optional[AsyncIOScheduler] = None


def _job_id(strategy_id: int, kind: str) -> str:
    return f"strategy:{strategy_id}:{kind}"


# ---------------------------------------------------------------------------
# Public lifecycle
# ---------------------------------------------------------------------------


async def start() -> None:
    """Initialize APScheduler and load every enabled strategy's job."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone=IST_TZ)
    _scheduler.start()
    await sync_all_jobs()
    logger.info("Strategy scheduler started (tz=%s)", IST_TZ)


def stop() -> None:
    """Shut down the scheduler. Pending jobs are dropped — they'll rebuild
    from DB on next boot."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("Scheduler shutdown raised")
    _scheduler = None
    logger.info("Strategy scheduler stopped")


def get_scheduler() -> Optional[AsyncIOScheduler]:
    """Return the singleton; None if start() hasn't run yet."""
    return _scheduler


# ---------------------------------------------------------------------------
# Per-strategy job sync (called from repo on create/update/delete)
# ---------------------------------------------------------------------------


async def sync_jobs_for_strategy(strategy_id: int) -> None:
    """Re-create this strategy's jobs from the current DB row.

    Called from repository CRUD after any change to ``strategy.scheduler``
    so the running scheduler converges to the persisted config without a
    process restart.
    """
    if _scheduler is None:
        return
    async with async_session() as db:
        strategy = await db.get(SmStrategy, strategy_id)
        _remove_jobs(strategy_id)
        if strategy is None:
            return
        _install_jobs(strategy)


async def sync_all_jobs() -> None:
    """Rebuild every strategy's jobs from scratch. Called on startup."""
    if _scheduler is None:
        return
    async with async_session() as db:
        rows = (await db.execute(select(SmStrategy))).scalars().all()
        for s in rows:
            _remove_jobs(s.id)
            _install_jobs(s)
        logger.info("Strategy scheduler: synced jobs for %d strategies", len(rows))


def remove_jobs_for_strategy(strategy_id: int) -> None:
    """Drop both jobs for a strategy. Used on delete; safe if jobs absent."""
    if _scheduler is None:
        return
    _remove_jobs(strategy_id)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _remove_jobs(strategy_id: int) -> None:
    """Remove the start + auto-stop jobs for a strategy. Idempotent."""
    if _scheduler is None:
        return
    for kind in ("start", "stop"):
        jid = _job_id(strategy_id, kind)
        try:
            _scheduler.remove_job(jid)
        except Exception:
            # JobLookupError when the job doesn't exist — fine.
            pass


def _install_jobs(strategy: SmStrategy) -> None:
    """Install start + optional auto-stop jobs for this strategy."""
    if _scheduler is None:
        return
    cfg = strategy.scheduler or {}
    if not cfg.get("enabled"):
        return

    days_raw = cfg.get("days") or []
    days_tokens = [_WEEKDAY_TOKENS[d] for d in days_raw if d in _WEEKDAY_TOKENS]
    if not days_tokens:
        logger.warning(
            "Scheduler: strategy %d has enabled=true but no valid weekdays",
            strategy.id,
        )
        return
    day_of_week = ",".join(days_tokens)

    # ---- Start job ----
    start_time = cfg.get("start_time") or "09:15"
    try:
        h, m = _parse_hhmm(start_time)
    except ValueError:
        logger.warning("Scheduler: strategy %d has invalid start_time=%r",
                       strategy.id, start_time)
        return
    default_mode = cfg.get("default_mode") or "sandbox"
    _scheduler.add_job(
        _fire_start,
        CronTrigger(day_of_week=day_of_week, hour=h, minute=m, timezone=IST_TZ),
        args=[strategy.id, default_mode],
        id=_job_id(strategy.id, "start"),
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
        max_instances=1,
    )

    # ---- Optional auto-stop job ----
    auto_stop = cfg.get("auto_stop_time")
    if auto_stop:
        try:
            sh, sm = _parse_hhmm(auto_stop)
        except ValueError:
            logger.warning(
                "Scheduler: strategy %d has invalid auto_stop_time=%r",
                strategy.id, auto_stop,
            )
            return
        _scheduler.add_job(
            _fire_auto_stop,
            CronTrigger(day_of_week=day_of_week, hour=sh, minute=sm, timezone=IST_TZ),
            args=[strategy.id],
            id=_job_id(strategy.id, "stop"),
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )

    logger.info(
        "Scheduler: strategy %d enabled (days=%s, start=%s, auto_stop=%s, mode=%s)",
        strategy.id, day_of_week, start_time, auto_stop, default_mode,
    )


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(s)
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(s)
    return h, m


# ---------------------------------------------------------------------------
# Cron callbacks (async — APScheduler awaits coroutines)
# ---------------------------------------------------------------------------


async def _resolve_user_broker(db, user_id: int) -> Optional[str]:
    """Find the user's active broker — required for sm_strategy_run.broker.

    Scheduler-fired runs don't have a JWT-cached broker (no session
    context), so we look it up from BrokerConfig.is_active. Mirrors the
    fallback in :func:`backend.dependencies.get_broker_context`.
    """
    from backend.models.broker_config import BrokerConfig

    row = (await db.execute(
        select(BrokerConfig).where(
            BrokerConfig.user_id == user_id,
            BrokerConfig.is_active == True,  # noqa: E712
        )
    )).scalar_one_or_none()
    return row.broker_name if row else None


async def _fire_start(strategy_id: int, mode: str) -> None:
    """Cron-triggered start. Idempotent: no-op when already running.

    Routes through engine.start_run with trigger_source='scheduler' so the
    audit trail differentiates cron fires from manual /start.
    """
    from backend.strategy import engine

    async with async_session() as db:
        strategy = await db.get(SmStrategy, strategy_id)
        if strategy is None:
            return
        if strategy.status == "running":
            logger.info(
                "Scheduler: strategy %d already running — skip start", strategy_id,
            )
            return
        # Live-mode gate (same as manual /start endpoint)
        if mode == "live" and not strategy.live_enabled:
            logger.warning(
                "Scheduler: strategy %d requested live but live_enabled=False — skip",
                strategy_id,
            )
            bus.publish(StrategyConfigEvent(
                topic="strategy.run_started",
                user_id=strategy.user_id,
                strategy_id=strategy.id,
                severity="warn",
                message=(
                    f"Scheduler refused live start: live mode not enabled. "
                    f"Re-auth on the detail page to enable."
                ),
                payload={"mode": "live", "reason": "live_not_enabled"},
            ))
            return

        broker = await _resolve_user_broker(db, strategy.user_id)
        if not broker:
            if mode == "live":
                logger.warning(
                    "Scheduler: strategy %d cannot start live — no active broker",
                    strategy_id,
                )
                return
            broker = "scheduler-sandbox"

        # Resolve broker auth from DB. Live runs require it (no token =
        # no order placement). Sandbox runs use it best-effort - the token
        # is consumed by ATM strike resolution (the underlying's LTP is
        # fetched via the broker's quote API even when the resulting
        # order routes to sandbox). Direct-strike legs work without auth.
        # Tokens are never cached in strategy state (plan section 14.8).
        from backend.strategy import live_auth
        auth_token = None
        config = None
        if mode == "live":
            ctx = await live_auth.resolve_live_auth(
                db, user_id=strategy.user_id, broker=broker,
            )
            if ctx is None:
                logger.warning(
                    "Scheduler: strategy %d cannot start live — broker session "
                    "expired or revoked", strategy_id,
                )
                return
            auth_token = ctx.auth_token
            config = ctx.config
        elif broker != "scheduler-sandbox":
            # Sandbox with a real broker known - resolve so ATM legs can
            # fetch underlying LTP. Failure is non-fatal here.
            ctx = await live_auth.resolve_live_auth(
                db, user_id=strategy.user_id, broker=broker,
            )
            if ctx is not None:
                auth_token = ctx.auth_token
                config = ctx.config

        try:
            await engine.start_run(
                db,
                strategy=strategy,
                mode=mode,
                broker=broker,
                auth_token=auth_token,
                config=config,
                trigger_source="scheduler",
            )
        except engine.EngineError as e:
            logger.warning(
                "Scheduler: start_run failed for strategy %d: %s", strategy_id, e,
            )
        except Exception:
            logger.exception(
                "Scheduler: start_run raised for strategy %d", strategy_id,
            )


async def _fire_auto_stop(strategy_id: int) -> None:
    """Cron-triggered stop. Idempotent: no-op when already stopped."""
    from backend.models.strategy_module import SmStrategyRun
    from backend.strategy import engine, live_auth

    async with async_session() as db:
        strategy = await db.get(SmStrategy, strategy_id)
        if strategy is None:
            return
        if strategy.status != "running":
            logger.info(
                "Scheduler: strategy %d not running — skip auto-stop", strategy_id,
            )
            return

        auth_token = None
        broker = None
        config = None
        run = None
        if strategy.current_run_id:
            run = await db.get(SmStrategyRun, strategy.current_run_id)
            if run and run.mode == "live":
                ctx = await live_auth.resolve_live_auth(
                    db, user_id=strategy.user_id, broker=run.broker,
                )
                if ctx is None:
                    logger.warning(
                        "Scheduler: strategy %d live auto-stop blocked — no "
                        "broker auth. Leaving run live; operator must square "
                        "off manually.", strategy_id,
                    )
                    return
                auth_token = ctx.auth_token
                broker = ctx.broker
                config = ctx.config

        try:
            strategy_kind = getattr(strategy, "strategy_kind", "batch") or "batch"
            if strategy_kind == "signal":
                # Signal-mode auto-stop walks the open legs and exits each
                # via the same exit_leg_by_signal path a manual *_exit
                # signal would take. Sequential per leg; finalizes the run
                # at the end. See engine.signal_auto_square docstring.
                await engine.signal_auto_square(
                    db,
                    strategy=strategy,
                    mode=run.mode if run else "sandbox",
                    broker=broker or (run.broker if run else "scheduler-sandbox"),
                    auth_token=auth_token,
                    config=config,
                )
            else:
                await engine.stop_run(
                    db,
                    strategy=strategy,
                    stop_reason="scheduler",
                    auth_token=auth_token,
                    broker=broker,
                    config=config,
                )
        except engine.EngineError as e:
            logger.warning(
                "Scheduler: stop_run failed for strategy %d: %s", strategy_id, e,
            )
        except Exception:
            logger.exception(
                "Scheduler: stop_run raised for strategy %d", strategy_id,
            )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def list_jobs() -> list[dict[str, Any]]:
    """Return all currently scheduled strategy jobs for /admin or testing."""
    if _scheduler is None:
        return []
    out: list[dict[str, Any]] = []
    for job in _scheduler.get_jobs():
        out.append({
            "id": job.id,
            "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
            "args": list(job.args),
        })
    return out
