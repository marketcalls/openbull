"""
Shared Redis client for cache-aside patterns.

All cache keys in OpenBull are prefixed with ``openbull:`` so they coexist safely
with other apps on the same Redis instance. TTLs are applied at write time; we
never rely on maxmemory eviction for correctness.
"""

import json
import logging
from typing import Any

import redis.asyncio as redis

from backend.config import get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "openbull:"

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return the shared async Redis client (lazy-initialized)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        logger.info("Redis client initialized at %s", settings.redis_url)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


def _k(key: str) -> str:
    return f"{KEY_PREFIX}{key}"


async def cache_get_json(key: str) -> Any | None:
    """GET and JSON-decode a cache entry. Returns None on miss or Redis failure."""
    try:
        raw = await get_redis().get(_k(key))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("Redis GET failed for %s: %s", key, e)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> bool:
    """SET a JSON-encoded cache entry. Pass ttl_seconds<=0 for a persistent key."""
    try:
        payload = json.dumps(value)
        if ttl_seconds and ttl_seconds > 0:
            await get_redis().set(_k(key), payload, ex=ttl_seconds)
        else:
            await get_redis().set(_k(key), payload)
        return True
    except Exception as e:
        logger.warning("Redis SET failed for %s: %s", key, e)
        return False


async def cache_delete(*keys: str) -> int:
    """Delete one or more cache keys. Returns the number of keys removed."""
    if not keys:
        return 0
    try:
        return await get_redis().delete(*(_k(k) for k in keys))
    except Exception as e:
        logger.warning("Redis DEL failed: %s", e)
        return 0


async def cache_delete_pattern(pattern: str) -> int:
    """Delete keys matching a pattern (used for mass invalidation)."""
    try:
        client = get_redis()
        deleted = 0
        async for key in client.scan_iter(match=_k(pattern), count=500):
            deleted += await client.delete(key)
        return deleted
    except Exception as e:
        logger.warning("Redis SCAN/DEL failed for %s: %s", pattern, e)
        return 0


async def cache_ttl(key: str) -> int:
    """Return remaining TTL in seconds for a key. -2 if missing, -1 if no TTL."""
    try:
        return await get_redis().ttl(_k(key))
    except Exception as e:
        logger.warning("Redis TTL failed for %s: %s", key, e)
        return -2


async def cache_exists(key: str) -> bool:
    """Return True if a key exists in Redis."""
    try:
        return bool(await get_redis().exists(_k(key)))
    except Exception as e:
        logger.warning("Redis EXISTS failed for %s: %s", key, e)
        return False


async def hash_hgetall(key: str) -> dict[str, str]:
    """HGETALL on a namespaced hash. Returns empty dict on miss or failure."""
    try:
        return await get_redis().hgetall(_k(key))
    except Exception as e:
        logger.warning("Redis HGETALL failed for %s: %s", key, e)
        return {}


async def hash_hmset_pipelined(key: str, mapping: dict[str, str], chunk: int = 5000) -> int:
    """Bulk-populate a hash in chunks using a pipeline. Returns number of fields written."""
    if not mapping:
        return 0
    client = get_redis()
    full_key = _k(key)
    written = 0
    try:
        items = list(mapping.items())
        for i in range(0, len(items), chunk):
            slice_ = dict(items[i : i + chunk])
            async with client.pipeline(transaction=False) as pipe:
                pipe.hset(full_key, mapping=slice_)
                await pipe.execute()
            written += len(slice_)
        return written
    except Exception as e:
        logger.warning("Redis bulk HSET failed for %s: %s", key, e)
        return written
