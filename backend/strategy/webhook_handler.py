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
from backend.utils.redis_client import cache_delete, cache_get_json, cache_set_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Knobs (env overrides — keep defaults conservative)
# ---------------------------------------------------------------------------

MAX_BODY_BYTES = 8 * 1024            # 8 KB — webhook payloads are tiny
DEDUPE_TTL_SEC = 60                  # plan 14.4
COOLING_OFF_SEC = 30                 # post-stop quiet window
RATE_LIMIT_PER_MIN = 60              # per-strategy

# Per-kind action allowlists. The webhook router validates the incoming
# action against the strategy's kind so a batch strategy can't accidentally
# accept a long_entry alert and a signal strategy can't be start/stop'd
# via a copy-pasted TV alert from a batch setup. See
# docs/plan/strategy-signal-mode.md section 4.2.
BATCH_ACTIONS = frozenset({"start", "stop"})
SIGNAL_ACTIONS = frozenset({"long_entry", "long_exit", "short_entry", "short_exit"})
ENTRY_SIGNALS = frozenset({"long_entry", "short_entry"})
EXIT_SIGNALS = frozenset({"long_exit", "short_exit"})
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


def _dedupe_key(strategy_id: int, action: str, mode: Optional[str]) -> str:
    return f"webhook:dedupe:{strategy_id}:{action}:{mode or '_'}"


def _resolve_signal_leg(
    strategy: SmStrategy,
    payload: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Pick the leg targeted by a signal-mode webhook.

    Resolution precedence per docs/plan/strategy-signal-mode.md section 4.1:
      1. ``leg_id`` (integer) - exact match on the strategy's legs[].id
      2. fallback: ``(symbol, exchange)`` lookup against legs[]

    Returns ``(leg_dict, None)`` on success or ``(None, error_message)`` on
    miss. The legs jsonb is a list of dicts (already validated by Pydantic
    at create/update time).
    """
    legs = strategy.legs or []
    leg_id = payload.get("leg_id")
    if leg_id is not None:
        try:
            leg_id_int = int(leg_id)
        except (TypeError, ValueError):
            return None, f"leg_id must be an integer, got {leg_id!r}"
        for leg in legs:
            if int(leg.get("id", -1)) == leg_id_int:
                return leg, None
        return None, f"No leg with id={leg_id_int} on this strategy"

    sym = payload.get("symbol")
    exch = payload.get("exchange")
    if sym and exch:
        sym_u = str(sym).strip().upper()
        exch_u = str(exch).strip().upper()
        for leg in legs:
            leg_sym = (leg.get("symbol") or "").upper()
            leg_exch = (leg.get("exchange") or "").upper()
            if leg_sym == sym_u and leg_exch == exch_u:
                return leg, None
        return None, f"No leg matching symbol={sym_u} exchange={exch_u}"

    return None, "Signal webhook must carry either 'leg_id' or ('symbol','exchange')"


def _direction_allows(strategy_direction: str, action: str) -> bool:
    """True when the strategy's direction filter permits the incoming signal.

    direction=both     -> all 4 signal actions allowed
    direction=long_only  -> only long_entry / long_exit
    direction=short_only -> only short_entry / short_exit

    Exit signals are gated symmetrically so a long_only strategy can't
    accidentally process a short_exit aimed at a leg that may have
    legitimately opened on a previous direction setting.
    """
    if strategy_direction == "both":
        return True
    if strategy_direction == "long_only":
        return action in ("long_entry", "long_exit")
    if strategy_direction == "short_only":
        return action in ("short_entry", "short_exit")
    # Unknown direction value - fail closed.
    return False


def _leg_side_allows(leg_side: Optional[str], action: str) -> bool:
    """True when a leg's side declaration permits the signal action.

    side='both'   -> any of the four actions
    side='long'   -> long_entry / long_exit only
    side='short'  -> short_entry / short_exit only
    """
    if not leg_side or leg_side == "both":
        return True
    if leg_side == "long":
        return action in ("long_entry", "long_exit")
    if leg_side == "short":
        return action in ("short_entry", "short_exit")
    return False


async def _dedupe_seen(strategy_id: int, action: str, mode: Optional[str]) -> bool:
    """Returns True if the same (strategy, action, mode) was seen in
    the last DEDUPE_TTL_SEC seconds; also records this hit for next time."""
    key = _dedupe_key(strategy_id, action, mode)
    seen = await cache_get_json(key)
    if seen is not None:
        return True
    await cache_set_json(key, 1, ttl_seconds=DEDUPE_TTL_SEC)
    return False


async def _dedupe_release(strategy_id: int, action: str, mode: Optional[str]) -> None:
    """Drop the dedupe key. Used on engine-side failure so the next retry
    within the window is allowed to reach the engine instead of being
    silently swallowed as a duplicate of a request that never actually ran."""
    try:
        await cache_delete(_dedupe_key(strategy_id, action, mode))
    except Exception:
        logger.warning(
            "Failed to release webhook dedupe key for strategy %d", strategy_id,
        )


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
# Signal-mode dispatch
# ---------------------------------------------------------------------------


def _signal_dedupe_key(strategy_id: int, action: str, leg_id: int) -> str:
    """Per-leg dedupe key for signal-mode webhooks. Different from the batch
    key so the two modes' dedupe windows don't collide."""
    return f"webhook:dedupe:signal:{strategy_id}:{action}:leg:{leg_id}"


async def _signal_dedupe_seen(strategy_id: int, action: str, leg_id: int) -> bool:
    key = _signal_dedupe_key(strategy_id, action, leg_id)
    seen = await cache_get_json(key)
    if seen is not None:
        return True
    await cache_set_json(key, 1, ttl_seconds=DEDUPE_TTL_SEC)
    return False


async def _signal_dedupe_release(strategy_id: int, action: str, leg_id: int) -> None:
    try:
        await cache_delete(_signal_dedupe_key(strategy_id, action, leg_id))
    except Exception:
        logger.warning(
            "Failed to release signal dedupe key for strategy %d leg %d",
            strategy_id, leg_id,
        )


async def _handle_signal_webhook(
    *,
    strategy: SmStrategy,
    action: str,
    payload: dict[str, Any],
    ip: Optional[str],
    user_agent: Optional[str],
) -> WebhookOutcome:
    """Validate and dispatch a signal-mode webhook.

    Steps:
      1. Direction gate (strategy-level long_only / short_only / both)
      2. Leg lookup (leg_id first, then symbol+exchange fallback)
      3. Leg-side gate (the leg's own side='long'|'short'|'both')
      4. Optional mode validation (payload-supplied 'mode', defaults sandbox)
      5. Dedupe (per-leg key with 60s TTL)
      6. Engine dispatch -- slice 4 wires this; for now returns 501 so
         operators see an explicit "not implemented yet" instead of
         half-running signal flow.
    """
    strategy_id = strategy.id
    mode = payload.get("mode") or "sandbox"

    # Step 1 - direction gate.
    strategy_direction = getattr(strategy, "direction", "both") or "both"
    if not _direction_allows(strategy_direction, action):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_direction_blocked",
                error=f"direction={strategy_direction}",
            )
        return WebhookOutcome(
            status_code=403,
            body={
                "status": "error",
                "message": (
                    f"Signal {action!r} blocked by strategy direction "
                    f"{strategy_direction!r}"
                ),
            },
            result_label="rejected_direction_blocked",
            error=f"direction={strategy_direction}",
        )

    # Step 2 - leg lookup.
    leg, err = _resolve_signal_leg(strategy, payload)
    if leg is None:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_no_leg",
                error=err,
            )
        return WebhookOutcome(
            status_code=400,
            body={"status": "error", "message": err},
            result_label="rejected_no_leg",
            error=err,
        )
    leg_id = int(leg["id"])

    # Step 3 - leg-side gate.
    if not _leg_side_allows(leg.get("side"), action):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_side_mismatch",
                error=f"leg_side={leg.get('side')!r}",
            )
        return WebhookOutcome(
            status_code=400,
            body={
                "status": "error",
                "message": (
                    f"Leg {leg_id}: side={leg.get('side')!r} does not accept "
                    f"action={action!r}"
                ),
            },
            result_label="rejected_side_mismatch",
            error=f"leg_side={leg.get('side')!r}",
        )

    # Step 4 - mode validation. Signal mode treats `mode` as optional in the
    # payload and defaults to sandbox. Live mode still requires the explicit
    # per-strategy live_enabled opt-in.
    if mode not in ALLOWED_MODES:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=str(mode),
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_invalid_mode",
            )
        return WebhookOutcome(
            status_code=400,
            body={"status": "error", "message": "mode must be 'live' or 'sandbox'"},
            result_label="rejected_invalid_mode",
        )
    if mode == "live" and not strategy.live_enabled:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_live_disabled",
            )
        return WebhookOutcome(
            status_code=403,
            body={"status": "error", "message": "Live mode is not enabled on this strategy"},
            result_label="rejected_live_disabled",
        )

    # Step 5 - per-leg dedupe.
    if await _signal_dedupe_seen(strategy_id, action, leg_id):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_dedupe",
                error=f"leg_id={leg_id}",
            )
        return WebhookOutcome(
            status_code=200,
            body={
                "status": "ok",
                "note": (
                    f"duplicate {action} on leg {leg_id} within "
                    f"{DEDUPE_TTL_SEC}s - ignored"
                ),
            },
            result_label="rejected_dedupe",
        )

    # Step 6 - engine dispatch.
    async with async_session() as db:
        # Re-load on this session - the engine entry points lock the
        # strategy row via SELECT FOR UPDATE.
        strategy_fresh = await db.get(SmStrategy, strategy_id)
        if strategy_fresh is None:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_engine_error",
                error="strategy_disappeared",
            )
            await _signal_dedupe_release(strategy_id, action, leg_id)
            return WebhookOutcome(
                status_code=500,
                body={"status": "error", "message": "Strategy not found"},
                result_label="rejected_engine_error",
            )

        # Resolve broker auth - mandatory for live, best-effort for
        # sandbox (same asymmetry-fix pattern as commit 5e976ac on the
        # batch-mode webhook).
        from backend.strategy import engine, live_auth, scheduler as sm_scheduler

        broker = await sm_scheduler._resolve_user_broker(db, strategy_fresh.user_id)
        if not broker:
            if mode == "live":
                await _record_event(
                    db, strategy_id=strategy_id, action=action, mode=mode,
                    payload=payload, ip=ip, user_agent=user_agent,
                    result_label="rejected_engine_error",
                    error="no_active_broker",
                )
                await _signal_dedupe_release(strategy_id, action, leg_id)
                return WebhookOutcome(
                    status_code=403,
                    body={"status": "error", "message": "No active broker for live mode"},
                    result_label="rejected_engine_error",
                    error="no_active_broker",
                )
            broker = "webhook-sandbox"

        auth_token = None
        cfg_dict = None
        if mode == "live":
            ctx = await live_auth.resolve_live_auth(
                db, user_id=strategy_fresh.user_id, broker=broker,
            )
            if ctx is None:
                await _record_event(
                    db, strategy_id=strategy_id, action=action, mode=mode,
                    payload=payload, ip=ip, user_agent=user_agent,
                    result_label="rejected_engine_error",
                    error="broker_session_expired",
                )
                await _signal_dedupe_release(strategy_id, action, leg_id)
                return WebhookOutcome(
                    status_code=403,
                    body={"status": "error", "message": "Broker session expired or revoked"},
                    result_label="rejected_engine_error",
                    error="broker_session_expired",
                )
            auth_token = ctx.auth_token
            cfg_dict = ctx.config
        elif broker != "webhook-sandbox":
            ctx = await live_auth.resolve_live_auth(
                db, user_id=strategy_fresh.user_id, broker=broker,
            )
            if ctx is not None:
                auth_token = ctx.auth_token
                cfg_dict = ctx.config

        try:
            if action in ENTRY_SIGNALS:
                result = await engine.enter_leg(
                    db, strategy=strategy_fresh, leg_config=leg,
                    action=action, mode=mode, broker=broker,
                    auth_token=auth_token, config=cfg_dict,
                )
            else:
                result = await engine.exit_leg_by_signal(
                    db, strategy=strategy_fresh, leg_config=leg,
                    action=action, mode=mode, broker=broker,
                    auth_token=auth_token, config=cfg_dict,
                )
        except engine.EngineError as e:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_engine_error", error=str(e),
            )
            await _signal_dedupe_release(strategy_id, action, leg_id)
            return WebhookOutcome(
                status_code=200,
                body={"status": "error", "message": str(e)},
                result_label="rejected_engine_error", error=str(e),
            )
        except Exception as e:
            logger.exception(
                "Signal-mode dispatch raised for strategy %d", strategy_id,
            )
            try:
                async with async_session() as db2:
                    await _record_event(
                        db2, strategy_id=strategy_id, action=action, mode=mode,
                        payload=payload, ip=ip, user_agent=user_agent,
                        result_label="rejected_engine_error", error=repr(e),
                    )
            except Exception:
                pass
            await _signal_dedupe_release(strategy_id, action, leg_id)
            return WebhookOutcome(
                status_code=500,
                body={"status": "error", "message": "Internal error"},
                result_label="rejected_engine_error", error=repr(e),
            )

    # Translate engine outcome to webhook response shape.
    outcome = result.get("outcome")
    if outcome == "placed" or outcome == "exited":
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="ok",
            )
        return WebhookOutcome(
            status_code=200,
            body={
                "status": "ok",
                "action": action,
                "leg_id": leg_id,
                "run_id": result.get("run_id"),
                "broker_order_id": result.get("broker_order_id"),
            },
            result_label="ok",
        )
    if outcome == "already_in_position" or outcome == "no_matching_position":
        # Silent no-op contract from design 4.4. Audit row uses
        # rejected_no_match per the user's spec on the original ask.
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_no_match",
                error=result.get("note"),
            )
        await _signal_dedupe_release(strategy_id, action, leg_id)
        return WebhookOutcome(
            status_code=200,
            body={
                "status": "ok",
                "note": result.get("note") or "no_matching_position",
                "leg_id": leg_id,
            },
            result_label="rejected_no_match",
        )
    if outcome == "position_conflict":
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_position_conflict",
                error=result.get("note"),
            )
        await _signal_dedupe_release(strategy_id, action, leg_id)
        return WebhookOutcome(
            status_code=409,
            body={
                "status": "error",
                "message": result.get("note") or "position conflict",
                "leg_id": leg_id,
            },
            result_label="rejected_position_conflict",
        )
    if outcome == "direction_blocked":
        # The webhook handler's own direction gate (step 1) should have
        # caught this before we ever called the engine. Reaching here
        # means either the gate logic drifted or the strategy.direction
        # changed between gate and dispatch (race window). Audit and
        # surface 403 either way.
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_direction_blocked",
                error=result.get("note"),
            )
        await _signal_dedupe_release(strategy_id, action, leg_id)
        return WebhookOutcome(
            status_code=403,
            body={
                "status": "error",
                "message": result.get("note") or "direction blocked",
                "leg_id": leg_id,
            },
            result_label="rejected_direction_blocked",
        )
    if outcome in ("outside_entry_window", "outside_trading_window"):
        # Intraday-window rejections. Per design 5.3 these return 200
        # OK with a clear note so TV alerts firing on the wrong side
        # of the window are silently absorbed - not surfaced as errors
        # the user has to react to. Release dedupe so a legitimate
        # signal arriving moments later (at exactly entry_time) is
        # processed.
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy_id, action=action, mode=mode,
                payload=payload, ip=ip, user_agent=user_agent,
                result_label="rejected_outside_window",
                error=outcome,
            )
        await _signal_dedupe_release(strategy_id, action, leg_id)
        return WebhookOutcome(
            status_code=200,
            body={
                "status": "ok",
                "note": result.get("note") or outcome,
                "leg_id": leg_id,
            },
            result_label="rejected_outside_window",
        )
    # outcome == "rejected" - broker/sandbox refused the order.
    async with async_session() as db:
        await _record_event(
            db, strategy_id=strategy_id, action=action, mode=mode,
            payload=payload, ip=ip, user_agent=user_agent,
            result_label="rejected_engine_error",
            error=result.get("reject_reason") or "broker_rejected",
        )
    await _signal_dedupe_release(strategy_id, action, leg_id)
    return WebhookOutcome(
        status_code=200,
        body={
            "status": "error",
            "message": result.get("reject_reason") or "broker_rejected",
            "leg_id": leg_id,
        },
        result_label="rejected_engine_error",
    )


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

    # 5b. Kill-switch / webhook lock. Applies to both batch and signal
    # strategies. The lock is set by the /kill_switch endpoint (which
    # also cancels pending orders and flattens positions) and cleared
    # only by an explicit /unlock_webhook call from the operator. While
    # locked, every incoming signal is refused with HTTP 403 and a
    # rejected_locked audit row.
    if getattr(strategy, "webhook_locked", False):
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=action, mode=mode,
                payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_locked",
            )
        return WebhookOutcome(
            status_code=403,
            body={
                "status": "error",
                "message": (
                    "Strategy is kill-switched - signals are blocked. Unlock "
                    "webhooks from the detail page before sending new signals."
                ),
            },
            result_label="rejected_locked",
        )

    # 6. action validation - per-kind allowlist
    strategy_kind = getattr(strategy, "strategy_kind", "batch") or "batch"
    allowed_for_kind = SIGNAL_ACTIONS if strategy_kind == "signal" else BATCH_ACTIONS
    if action not in allowed_for_kind:
        async with async_session() as db:
            await _record_event(
                db, strategy_id=strategy.id, action=str(action) if action else None,
                mode=mode, payload=parsed, ip=ip, user_agent=user_agent,
                result_label="rejected_invalid_action",
            )
        return WebhookOutcome(
            status_code=400,
            body={
                "status": "error",
                "message": (
                    f"action {action!r} not valid for {strategy_kind}-mode "
                    f"strategy. Allowed: {sorted(allowed_for_kind)}"
                ),
            },
            result_label="rejected_invalid_action",
        )

    # ------------------------------------------------------------------
    # Signal-mode branch: leg-targeted entry/exit signals.
    # Validation order: direction gate -> leg lookup -> leg-side gate ->
    # dedupe (per-leg key) -> dispatch (slice-4 engine entry point).
    # ------------------------------------------------------------------
    if strategy_kind == "signal":
        return await _handle_signal_webhook(
            strategy=strategy,
            action=action,
            payload=parsed,
            ip=ip,
            user_agent=user_agent,
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
                        await _dedupe_release(strategy.id, action, mode)
                        return WebhookOutcome(
                            status_code=403,
                            body={"status": "error", "message": "No active broker for live mode"},
                            result_label="rejected_engine_error",
                            error="no_active_broker",
                        )
                    broker = "webhook-sandbox"

                # Resolve broker auth from DB. For live runs this is mandatory.
                # For sandbox runs the token is still used by ATM strike
                # resolution (option_symbol_service fetches the underlying's
                # LTP via the broker's quote API even when the resulting
                # order routes to sandbox), so we resolve best-effort when a
                # real broker is known. Direct-strike legs work either way.
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
                        await _dedupe_release(strategy.id, action, mode)
                        return WebhookOutcome(
                            status_code=403,
                            body={"status": "error", "message": "Broker session expired or revoked"},
                            result_label="rejected_engine_error",
                            error="broker_session_expired",
                        )
                    auth_token = ctx.auth_token
                    cfg_dict = ctx.config
                elif broker != "webhook-sandbox":
                    # Sandbox with a real broker known - resolve so ATM
                    # legs can fetch underlying LTP. Failure is non-fatal:
                    # direct-strike legs still resolve without auth.
                    ctx = await live_auth.resolve_live_auth(
                        db, user_id=strategy_fresh.user_id, broker=broker,
                    )
                    if ctx is not None:
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
                            # Stop's dedupe key uses mode=None (only start
                            # carries mode in the key); release it so the
                            # operator can re-fire stop once auth recovers.
                            await _dedupe_release(strategy.id, action, None)
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
            # Engine-side failure means the request never produced a real
            # action - drop the dedupe reservation so the user can retry
            # within the 60s window without seeing a stale "duplicate" reply.
            await _dedupe_release(
                strategy.id, action, mode if action == "start" else None,
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
            await _dedupe_release(
                strategy.id, action, mode if action == "start" else None,
            )
            return WebhookOutcome(
                status_code=500,
                body={"status": "error", "message": "Internal error"},
                result_label="rejected_engine_error", error=repr(e),
            )
