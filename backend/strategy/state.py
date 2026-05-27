"""Redis-backed runtime state for active strategy runs.

The DB (``sm_strategy``, ``sm_strategy_run``, ``sm_strategy_order``,
``sm_strategy_checkpoint``) is the canonical store. Redis is the **hot**
cache that the engine reads/writes during a run — fast enough for tick-loop
updates (Phase 6) without hitting Postgres on every leg state change.

Per the plan (Section 4.2, Section 5.4), Redis state is **derivable** —
recovery.py rebuilds it from DB + (later) broker reconciliation on every
boot, so the keys here are intentionally TTL-less but disposable.

Keys
----
``strategy:run:{run_id}:state``  — full run state JSON (no TTL; cleared on stop)
``strategy:run:{run_id}:lock``   — ownership lock (worker_id, 30s TTL)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from backend.utils.redis_client import (
    cache_delete,
    cache_get_json,
    cache_set_json,
    get_redis,
)

logger = logging.getLogger(__name__)


# Per-run asyncio lock for serializing state read-modify-write sequences
# inside this process. The tick processor and engine.{start,stop,close_leg,
# _exit_legs} both read state, mutate, then write it back; without this
# lock, an in-flight tick processor can stomp the write that close_leg
# just made (silently losing leg.realized_pnl, for example). Single-process
# only — a future multi-worker deployment would need to fall back to the
# Redis ownership lock for cross-process serialization.
_state_locks: dict[int, asyncio.Lock] = {}


def get_state_lock(run_id: int) -> asyncio.Lock:
    """Return (creating if needed) the per-run state-mutation lock.

    Callers should ``async with`` this around any get_run_state + mutate +
    hydrate_run_state sequence. Cheap — one Lock object per active run,
    cleared by :func:`clear_run_state`.
    """
    lock = _state_locks.get(run_id)
    if lock is None:
        lock = asyncio.Lock()
        _state_locks[run_id] = lock
    return lock


def _state_key(run_id: int) -> str:
    return f"strategy:run:{run_id}:state"


def _lock_key(run_id: int) -> str:
    return f"strategy:run:{run_id}:lock"


# ---------------------------------------------------------------------------
# State CRUD
# ---------------------------------------------------------------------------


def _build_initial_state(
    *, run_id: int, strategy_id: int, strategy_legs: list[dict[str, Any]],
    entry_orders_by_leg: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Construct the initial state dict from engine inputs.

    Phase 5 seeds the state with leg statuses derived from entry orders.
    P&L fields are placeholders until Phase 6 wires the tick loop.
    """
    legs: dict[str, dict[str, Any]] = {}
    for leg in strategy_legs:
        leg_id = int(leg["id"])
        entry = entry_orders_by_leg.get(leg_id)
        legs[str(leg_id)] = {
            "leg_id": leg_id,
            "position": leg.get("position"),
            "lots": leg.get("lots"),
            "symbol": entry.get("symbol") if entry else None,
            "exchange": entry.get("exchange") if entry else None,
            "qty": entry.get("qty") if entry else None,
            "entry_order_id": entry.get("order_id") if entry else None,
            "entry_status": entry.get("status") if entry else "configured",
            "entry_avg": None,
            "ltp": None,
            "mtm": 0.0,
            "status": "rejected" if entry and entry["status"] == "rejected" else (
                "open" if entry else "configured"
            ),
            "exit_order_id": None,
            "exit_kind": None,
            "effective_sl": None,
            "effective_target": None,
            "trail_active": False,
            "favorable_peak": 0.0,
        }
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "pnl_realized": 0.0,
        "pnl_unrealized": 0.0,
        "pnl_total": 0.0,
        "pnl_peak": 0.0,
        "pnl_trough": 0.0,
        "lock_armed": False,
        "lock_floor": None,
        "trail_to_entry_active": False,
        "legs": legs,
    }


async def init_run_state(
    *, run_id: int, strategy_id: int, strategy_legs: list[dict[str, Any]],
    entry_orders_by_leg: dict[int, dict[str, Any]],
) -> None:
    """Write the initial state on engine.start_run."""
    state = _build_initial_state(
        run_id=run_id,
        strategy_id=strategy_id,
        strategy_legs=strategy_legs,
        entry_orders_by_leg=entry_orders_by_leg,
    )
    await cache_set_json(_state_key(run_id), state, ttl_seconds=0)


async def hydrate_run_state(run_id: int, state: dict[str, Any]) -> None:
    """Recovery path — write a fully-built state dict directly."""
    state["run_id"] = run_id
    await cache_set_json(_state_key(run_id), state, ttl_seconds=0)


async def get_run_state(run_id: int) -> Optional[dict[str, Any]]:
    return await cache_get_json(_state_key(run_id))


async def update_run_state(run_id: int, **updates: Any) -> Optional[dict[str, Any]]:
    """Read-modify-write of run-scoped fields (pnl_*, lock_*, etc.)."""
    state = await cache_get_json(_state_key(run_id))
    if state is None:
        return None
    state.update(updates)
    await cache_set_json(_state_key(run_id), state, ttl_seconds=0)
    return state


async def update_leg_state(
    run_id: int, leg_id: int, updates: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Partial update of one leg inside the run state."""
    state = await cache_get_json(_state_key(run_id))
    if state is None:
        return None
    key = str(leg_id)
    leg = state.get("legs", {}).get(key)
    if leg is None:
        # New leg appearing post-init shouldn't happen, but be defensive.
        leg = {"leg_id": leg_id}
        state.setdefault("legs", {})[key] = leg
    leg.update(updates)
    await cache_set_json(_state_key(run_id), state, ttl_seconds=0)
    return state


async def mark_leg_closed(
    run_id: int, leg_id: int, *,
    exit_order_id: int,
    exit_kind: str,
    exit_status: str,
) -> Optional[dict[str, Any]]:
    """Convenience: flip a leg to 'closed' on exit placement."""
    return await update_leg_state(run_id, leg_id, {
        "status": "closed" if exit_status != "rejected" else "open",
        "exit_order_id": exit_order_id,
        "exit_kind": exit_kind,
    })


async def clear_run_state(run_id: int) -> int:
    """Drop the state key (engine.stop_run final cleanup)."""
    result = await cache_delete(_state_key(run_id))
    _state_locks.pop(run_id, None)
    return result


# ---------------------------------------------------------------------------
# Ownership lock (Phase 6 tick loop renews; Phase 5 just acquires once)
# ---------------------------------------------------------------------------


async def acquire_run_lock(run_id: int, worker_id: str, ttl_seconds: int = 30) -> bool:
    """SET NX EX — returns True if this worker is now the owner."""
    from backend.utils.redis_client import KEY_PREFIX
    try:
        client = get_redis()
        result = await client.set(
            f"{KEY_PREFIX}{_lock_key(run_id)}", worker_id, nx=True, ex=ttl_seconds,
        )
        return bool(result)
    except Exception as e:
        logger.warning("Redis SETNX lock failed for run %d: %s", run_id, e)
        return False


async def renew_run_lock(run_id: int, worker_id: str, ttl_seconds: int = 30) -> bool:
    """Refresh the lock TTL if we still own it (compare-and-extend via Lua)."""
    from backend.utils.redis_client import KEY_PREFIX
    lua = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "  return redis.call('expire', KEYS[1], ARGV[2]) "
        "else return 0 end"
    )
    try:
        client = get_redis()
        result = await client.eval(
            lua, 1, f"{KEY_PREFIX}{_lock_key(run_id)}", worker_id, str(ttl_seconds),
        )
        return bool(result)
    except Exception as e:
        logger.warning("Redis lock renew failed for run %d: %s", run_id, e)
        return False


async def release_run_lock(run_id: int, worker_id: str) -> bool:
    """Release only if still owned by us (CAS DEL via Lua)."""
    from backend.utils.redis_client import KEY_PREFIX
    lua = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "  return redis.call('del', KEYS[1]) "
        "else return 0 end"
    )
    try:
        client = get_redis()
        result = await client.eval(
            lua, 1, f"{KEY_PREFIX}{_lock_key(run_id)}", worker_id,
        )
        return bool(result)
    except Exception as e:
        logger.warning("Redis lock release failed for run %d: %s", run_id, e)
        return False
