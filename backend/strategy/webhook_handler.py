"""TradingView webhook handler — validation pipeline + dispatch.

URL-embedded token model (plan Section 11):
    POST /webhook/strategy/{webhook_token}
    Body: {"action": "start"|"stop", "mode": "sandbox"|"live"}

The token in the URL is the credential — the body never carries a secret.
Server SHA-256s the token and indexed-looks-up in ``strategy.webhook_token_hash``.

Validation order (matches plan 11.4 — fail fast, audit everything):
  1. Token unknown        → 401 ``rejected_token``       (no strategy bound)
  2. Body too large       → 413
  3. Body invalid JSON    → 400 ``rejected_invalid_body``
  4. IP not in allowlist  → 401 ``rejected_ip``
  5. Rate limit hit       → 429 ``rate_limited``
  6. action ∉ {start,stop}→ 400 ``rejected_invalid_action``
  7. start without mode   → 400 ``rejected_invalid_mode``
  8. live + not enabled   → 403 ``rejected_live_disabled``
  9. Dedupe (60s window)  → 200 OK + ``rejected_dedupe`` event
 10. Cooling-off (<30s)   → 200 OK + ``rejected_cooling_off`` event
 11. Engine error         → 200 OK + ``rejected_engine_error`` event

Every code path — accepted or rejected — writes one row to
``sm_webhook_event``. The token plaintext is never persisted (the URL
path is logged by the reverse proxy with redaction; the request body
doesn't include it).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session
from backend.models.strategy_module import (
    SmStrategy,
    SmStrategyRun,
    SmWebhookEvent,
)
from backend.strategy.security import hash_webhook_token, redact_payload
from backend.strategy.time_utils import now_utc
from backend.utils.redis_client import cache_get_json, cache_set_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Knobs (env overrides — keep defaults conservative)
# ---------------------------------------------------------------------------

MAX_BODY_BYTES = 8 * 1024            # 8 KB — webhook payloads are tiny
DEDUPE_TTL_SEC = 60                  # plan 14.4
COOLING_OFF_SEC = 30                 # post-stop quiet window
RATE_LIMIT_PER_MIN = 60              # per-strategy
ALLOWED_ACTIONS = frozenset({"start", "stop"})
ALLOWED_MODES = frozenset({"live", "sandbox"})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class WebhookOutcome:
    """Plain-data result returned to the router for HTTP response shaping."""

    __slots__ = ("status_code", "body", "result_label", "error")

    def __init__(
        self,
        *,
        status_code: int,
        body: dict[str, Any],
        result_label: str,
        error: Optional[str] = None,
    ):
        self.status_code = status_code
        self.body = body
        self.result_label = result_label
        self.error = error


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _ip_in_allowlist(ip_str: Optional[str], allowlist: Any) -> bool:
    """Empty/None allowlist → allow any. Otherwise the IP must be in one of
    the configured CIDRs."""
    if not allowlist:
        return True
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for entry in allowlist:
        cidr = entry.get("cidr") if isinstance(entry, dict) else entry
        if not cidr:
            continue
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


async def _rate_limited(strategy_id: int) -> bool:
    """Sliding-window-ish per-strategy rate limit via Redis counters.

    A small bucket: increment a per-minute key; if > limit, reject. The
    minute key auto-expires so we don't need cleanup.
    """
    bucket = f"webhook:ratelimit:{strategy_id}:{int(time.time() // 60)}"
    try:
        from backend.utils.redis_client import KEY_PREFIX, get_redis
        client = get_redis()
        # Increment, set TTL on first hit. INCR + EXPIRE in a pipeline.
        async with client.pipeline(transaction=False) as pipe:
            pipe.incr(f"{KEY_PREFIX}{bucket}")
            pipe.expire(f"{KEY_PREFIX}{bucket}", 120)  # 2 minutes — comfortable margin
            count, _ = await pipe.execute()
        return int(count) > RATE_LIMIT_PER_MIN
    except Exception:
        # Redis hiccup — don't block legitimate webhooks
        logger.warning("Rate limit check failed for strategy %d", strategy_id)
        return False


async def _dedupe_seen(strategy_id: int, action: str, mode: Optional[str]) -> bool:
    """Returns True if the same (strategy, action, mode) was seen in
    the last DEDUPE_TTL_SEC seconds; also records this hit for next time."""
    key = f"webhook:dedupe:{strategy_id}:{action}:{mode or '_'}"
    seen = await cache_get_json(key)
    if seen is not None:
        return True
    await cache_set_json(key, 1, ttl_seconds=DEDUPE_TTL_SEC)
    return False


async def _cooling_off_active(db: AsyncSession, strategy_id: int) -> bool:
    """Returns True if the last run for this strategy stopped within the
    last COOLING_OFF_SEC seconds. Prevents oscillation when a TV alert
    fires repeatedly during a brief drawdown.

    Only applies to ``start`` actions; ``stop`` is always allowed.
    """
    cutoff = now_utc() - timedelta(seconds=COOLING_OFF_SEC)
    row = (await db.execute(
        select(SmStrategyRun.stopped_at)
        .where(
            SmStrategyRun.strategy_id == strategy_id,
            SmStrategyRun.stopped_at.is_not(None),
        )
        .order_by(SmStrategyRun.stopped_at.desc())
        .limit(1)
    )).first()
    if row is None or row[0] is None:
        return False
    return row[0] >= cutoff


async def _record_event(
    db: AsyncSession,
    *,
    strategy_id: Optional[int],
    action: Optional[str],
    mode: Optional[str],
    payload: Any,
    ip: Optional[str],
    user_agent: Optional[str],
    result_label: str,
    error: Optional[str] = None,
) -> SmWebhookEvent:
    """Write one sm_webhook_event row. Always called, regardless of outcome.

    The token plaintext is not in ``payload`` (it's in the URL); the
    redactor scrubs anything else that could leak.
    """
    redacted = redact_payload(payload) if payload is not None else None
    row = SmWebhookEvent(
        strategy_id=strategy_id,
        action=action,
        mode=mode,
        payload=redacted,
        ip=ip,
        user_agent=user_agent,
        result=result_label,
        error=error,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def handle_webhook(
    *,
    token: str,
    raw_body: bytes,
    ip: Optional[str],
    user_agent: Optional[str],
) -> WebhookOutcome:
    """Validate + dispatch one incoming TradingView webhook.

    All branches write an sm_webhook_event row. Returns a WebhookOutcome
    the router translates to an HTTP response.
    """
    # 1. Body size cap (cheap to check first)
    if len(raw_body) > MAX_BODY_BYTES:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=None, action=None, mode=None,
                payload={"_oversize_bytes": len(raw_body)},
                ip=ip, user_agent=user_agent,
                result_label="rejected_token", error="oversized",
            )
        return WebhookOutcome(
            status_code=413, body={"status": "error", "message": "Payload too large"},
            result_label="rejected_token", error="oversized",
        )

    # 2. Token lookup — opaque on miss to prevent enumeration
    token_hash = hash_webhook_token(token)
    async with async_session() as db:
        strategy = (await db.execute(
            select(SmStrategy).where(SmStrategy.webhook_token_hash == token_hash)
        )).scalar_one_or_none()
        if strategy is None:
            await _record_event(
                db, strategy_id=None, action=None, mode=None, payload=None,
                ip=ip, user_agent=user_agent,
                result_label="rejected_token",
            )
            # Same response shape as auth failure — no enumeration hint
            return WebhookOutcome(
                status_code=401,
                body={"status": "error", "message": "Authentication failed"},
                result_label="rejected_token",
            )

    # 3. Parse JSON body
    try:
        body_text = raw_body.decode("utf-8") if raw_body else ""
        parsed = json.loads(body_text) if body_text else {}
        if not isinstance(parsed, dict):
            raise ValueError("body must be a JSON object")
    except (ValueError, UnicodeDecodeError) as e:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=None, mode=None,
                payload=None, ip=ip, user_agent=user_agent,
                result_label="rejected_invalid_body", error=str(e),
            )
        return WebhookOutcome(
            status_code=400, body={"status": "error", "message": "Invalid JSON body"},
            result_label="rejected_invalid_body", error=str(e),
        )

    action = parsed.get("action")
    mode = parsed.get("mode")

    # 4. IP allow-list (if configured)
    if not _ip_in_allowlist(ip, strategy.webhook_ip_allowlist):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_ip",
            )
        return WebhookOutcome(
            status_code=401, body={"status": "error", "message": "Authentication failed"},
            result_label="rejected_ip",
        )

    # 5. Rate limit
    if await _rate_limited(strategy.id):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rate_limited",
            )
        return WebhookOutcome(
            status_code=429, body={"status": "error", "message": "Rate limit exceeded"},
            result_label="rate_limited",
        )

    # 6. action validation
    if action not in ALLOWED_ACTIONS:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=str(action) if action else None,
                mode=mode, payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_invalid_action",
            )
        return WebhookOutcome(
            status_code=400,
            body={"status": "error", "message": "action must be 'start' or 'stop'"},
            result_label="rejected_invalid_action",
        )

    # 7. mode validation (start only)
    if action == "start":
        if mode not in ALLOWED_MODES:
            async with async_session() as db:
                await _record_event(
                    db, strategy_id=strategy.id, action=action, mode=str(mode) if mode else None,
                    payload=parsed, ip=ip, user_agent=user_agent,
                    result_label="rejected_invalid_mode" if mode else "rejected_invalid_action",
                )
            return WebhookOutcome(
                status_code=400,
                body={"status": "error", "message": "mode must be 'live' or 'sandbox' for start"},
                result_label="rejected_invalid_mode",
            )

        # 8. live + not enabled
        if mode == "live" and not strategy.live_enabled:
            async with async_session() as db:
                await _record_event(
                    db, strategy_id=strategy.id, action=action, mode=mode,
                    payload=parsed, ip=ip, user_agent=user_agent,
                    result_label="rejected_live_disabled",
                )
            return WebhookOutcome(
                status_code=403,
                body={"status": "error", "message": "Live mode is not enabled on this strategy"},
                result_label="rejected_live_disabled",
            )

    # 9. Dedupe (60s window)
    if await _dedupe_seen(strategy.id, action, mode if action == "start" else None):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_dedupe",
            )
        return WebhookOutcome(
            status_code=200,
            body={"status": "ok", "note": f"duplicate within {DEDUPE_TTL_SEC}s — ignored"},
            result_label="rejected_dedupe",
        )

    # 10. Cooling-off (start only; protect against oscillation)
    if action == "start":
        async with async_session() as db:
            if await _cooling_off_active(db, strategy.id):
                await _record_event(
                    db, strategy_id=strategy.id, action=action, mode=mode,
                    payload=parsed, ip=ip, user_agent=user_agent,
                    result_label="rejected_cooling_off",
                )
                return WebhookOutcome(
                    status_code=200,
                    body={
                        "status": "ok",
                        "note": f"cooling-off active ({COOLING_OFF_SEC}s post-stop) — ignored",
                    },
                    result_label="rejected_cooling_off",
                )

    # 11. Dispatch to the engine.
    async with async_session() as db:
        # Re-load strategy on this session (mutations and start_run need it)
        strategy_fresh = await db.get(SmStrategy, strategy.id)
        if strategy_fresh is None:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_engine_error",
                error="strategy_disappeared",
            )
            return WebhookOutcome(
                status_code=500,
                body={"status": "error", "message": "Strategy not found"},
                result_label="rejected_engine_error",
            )

        # Record the event FIRST so the engine.start_run can stamp it on the run.
        event_row = await _record_event(
            db, strategy_id=strategy.id, action=action, mode=mode,
            payload=parsed, ip=ip, user_agent=user_agent,
            result_label="ok",
        )

        from backend.strategy import engine, live_auth, scheduler as sm_scheduler

        try:
            if action == "start":
                if strategy_fresh.status == "running":
                    # Idempotent: webhook fired but already running.
                    return WebhookOutcome(
                        status_code=200,
                        body={
                            "status": "ok",
                            "note": "already running",
                            "run_id": strategy_fresh.current_run_id,
                        },
                        result_label="ok",
                    )
                broker = await sm_scheduler._resolve_user_broker(db, strategy_fresh.user_id)
                if not broker:
                    if mode == "live":
                        await _record_event(
                            db, strategy_id=strategy.id, action=action, mode=mode,
                            payload=parsed, ip=ip, user_agent=user_agent,
                            result_label="rejected_engine_error",
                            error="no_active_broker",
                        )
                        return WebhookOutcome(
                            status_code=403,
                            body={"status": "error", "message": "No active broker for live mode"},
                            result_label="rejected_engine_error",
                            error="no_active_broker",
                        )
                    broker = "webhook-sandbox"

                # Live: fetch broker auth token fresh from DB so the engine
                # can place real orders. Sandbox leaves auth_token=None.
                auth_token = None
                cfg_dict = None
                if mode == "live":
                    ctx = await live_auth.resolve_live_auth(
                        db, user_id=strategy_fresh.user_id, broker=broker,
                    )
                    if ctx is None:
                        await _record_event(
                            db, strategy_id=strategy.id, action=action, mode=mode,
                            payload=parsed, ip=ip, user_agent=user_agent,
                            result_label="rejected_engine_error",
                            error="broker_session_expired",
                        )
                        return WebhookOutcome(
                            status_code=403,
                            body={"status": "error", "message": "Broker session expired or revoked"},
                            result_label="rejected_engine_error",
                            error="broker_session_expired",
                        )
                    auth_token = ctx.auth_token
                    cfg_dict = ctx.config

                run, _ = await engine.start_run(
                    db,
                    strategy=strategy_fresh,
                    mode=mode,
                    broker=broker,
                    auth_token=auth_token,
                    config=cfg_dict,
                    trigger_source="webhook",
                )
                # Stamp the webhook_event onto the run for forensic linking.
                run.webhook_event_id = event_row.id
                await db.commit()
                return WebhookOutcome(
                    status_code=200,
                    body={"status": "ok", "action": "start", "run_id": run.id, "mode": mode},
                    result_label="ok",
                )

            else:  # action == "stop"
                if strategy_fresh.status != "running":
                    return WebhookOutcome(
                        status_code=200,
                        body={"status": "ok", "note": "not running — nothing to stop"},
                        result_label="ok",
                    )
                # If the running run is live, resolve broker auth for the
                # exit orders. Sandbox stop needs nothing.
                stop_auth = None
                stop_broker = None
                stop_cfg = None
                if strategy_fresh.current_run_id:
                    from backend.models.strategy_module import SmStrategyRun
                    run = await db.get(SmStrategyRun, strategy_fresh.current_run_id)
                    if run and run.mode == "live":
                        ctx = await live_auth.resolve_live_auth(
                            db, user_id=strategy_fresh.user_id, broker=run.broker,
                        )
                        if ctx is None:
                            return WebhookOutcome(
                                status_code=403,
                                body={"status": "error", "message": "Broker session expired"},
                                result_label="rejected_engine_error",
                                error="broker_session_expired",
                            )
                        stop_auth = ctx.auth_token
                        stop_broker = ctx.broker
                        stop_cfg = ctx.config
                result = await engine.stop_run(
                    db,
                    strategy=strategy_fresh,
                    stop_reason="webhook",
                    auth_token=stop_auth,
                    broker=stop_broker,
                    config=stop_cfg,
                )
                return WebhookOutcome(
                    status_code=200,
                    body={"status": "ok", "action": "stop", "run_id": result["run_id"]},
                    result_label="ok",
                )

        except engine.EngineError as e:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_engine_error", error=str(e),
            )
            return WebhookOutcome(
                status_code=200,
                body={"status": "error", "message": str(e)},
                result_label="rejected_engine_error", error=str(e),
            )
        except Exception as e:
            logger.exception("Webhook dispatch raised for strategy %d", strategy.id)
            try:
                async with async_session() as db2:
                    await _record_event(
                        db2, strategy_id=strategy.id, action=action, mode=mode,
                        payload=parsed, ip=ip, user_agent=user_agent,
                        result_label="rejected_engine_error", error=repr(e),
                    )
            except Exception:
                pass
            return WebhookOutcome(
                status_code=500,
                body={"status": "error", "message": "Internal error"},
                result_label="rejected_engine_error", error=repr(e),
            )
