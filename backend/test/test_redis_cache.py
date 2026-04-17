"""
Verify Redis caching is correctly wired into OpenBull.

Run from the openbull project root:
    uv run python backend/test/test_redis_cache.py

The script does not start the FastAPI server. It exercises the cache helpers
directly and inspects Redis so you can see:
  1. Values are stored under the ``openbull:`` namespace
  2. TTLs are set and countdown
  3. Entries survive a simulated "app restart" (client recreated)
  4. Invalidation clears the right keys
"""

import asyncio
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.utils.redis_client import (
    KEY_PREFIX,
    cache_delete_pattern,
    cache_get_json,
    cache_set_json,
    cache_ttl,
    close_redis,
    get_redis,
)
from backend.dependencies import (
    API_CTX_TTL,
    API_KEY_TTL,
    BROKER_CTX_TTL,
    INVALID_KEY_TTL,
    _key_api_ctx,
    _key_api_invalid,
    _key_api_valid,
    _key_broker_ctx,
    invalidate_all_caches,
    invalidate_user_cache,
)


def header(text: str) -> None:
    print(f"\n{'=' * 60}\n{text}\n{'=' * 60}")


async def test_connection() -> None:
    header("1. Redis connection check")
    client = get_redis()
    pong = await client.ping()
    print(f"PING -> {pong}")
    info = await client.info("server")
    print(f"Redis version: {info.get('redis_version')}")
    print(f"Uptime: {info.get('uptime_in_seconds')}s")


async def test_api_key_cache() -> None:
    header("2. API key positive cache (SET + GET + TTL)")
    fake_key = "test-api-key-abc123"
    key_hash = hashlib.sha256(fake_key.encode()).hexdigest()
    cache_key = _key_api_valid(key_hash)

    await cache_set_json(cache_key, 42, API_KEY_TTL)

    got = await cache_get_json(cache_key)
    ttl = await cache_ttl(cache_key)
    full_key = f"{KEY_PREFIX}{cache_key}"
    print(f"Redis key:       {full_key}")
    print(f"Stored value:    {got}  (expected 42)")
    print(f"TTL remaining:   {ttl}s (set to {API_KEY_TTL}s)")

    assert got == 42, "Positive cache roundtrip failed"
    assert 0 < ttl <= API_KEY_TTL, f"TTL outside expected range: {ttl}"
    print("OK")


async def test_invalid_key_cache() -> None:
    header("3. API key negative cache (rejects bad keys fast)")
    fake_key = "definitely-not-a-real-key"
    key_hash = hashlib.sha256(fake_key.encode()).hexdigest()
    cache_key = _key_api_invalid(key_hash)

    await cache_set_json(cache_key, 1, INVALID_KEY_TTL)
    ttl = await cache_ttl(cache_key)
    got = await cache_get_json(cache_key)
    print(f"Negative marker: {got}")
    print(f"TTL:             {ttl}s (set to {INVALID_KEY_TTL}s)")
    assert got == 1 and 0 < ttl <= INVALID_KEY_TTL
    print("OK")


async def test_broker_context_cache() -> None:
    header("4. Broker context cache (session-scoped)")
    user_id = 9999
    payload = {
        "broker_name": "upstox",
        "auth_token": "demo-token-value",
        "broker_config": {"api_key": "x", "api_secret": "y", "redirect_url": "z"},
    }
    cache_key = _key_broker_ctx(user_id)
    await cache_set_json(cache_key, payload, BROKER_CTX_TTL)

    got = await cache_get_json(cache_key)
    ttl = await cache_ttl(cache_key)
    print(f"Stored value:  {got}")
    print(f"TTL remaining: {ttl}s (set to {BROKER_CTX_TTL}s)")
    assert got == payload and 0 < ttl <= BROKER_CTX_TTL
    print("OK")


async def test_api_context_cache() -> None:
    header("5. External API context cache (shared HTTP + WS)")
    user_id = 9999
    payload = {
        "broker_name": "zerodha",
        "auth_token": "another-token",
        "broker_config": {},
    }
    cache_key = _key_api_ctx(user_id)
    await cache_set_json(cache_key, payload, API_CTX_TTL)

    got = await cache_get_json(cache_key)
    ttl = await cache_ttl(cache_key)
    print(f"Stored value:  {got}")
    print(f"TTL remaining: {ttl}s (set to {API_CTX_TTL}s)")
    assert got == payload and 0 < ttl <= API_CTX_TTL
    print("OK")


async def test_ttl_countdown() -> None:
    header("6. TTL countdown (wait 2s)")
    cache_key = "test:countdown"
    await cache_set_json(cache_key, "x", 10)
    t1 = await cache_ttl(cache_key)
    await asyncio.sleep(2)
    t2 = await cache_ttl(cache_key)
    print(f"TTL at t=0:  {t1}s")
    print(f"TTL at t=2:  {t2}s")
    assert t1 > t2, "TTL did not decrease"
    print("OK — TTL ticks down in real time")


async def test_restart_survival() -> None:
    header("7. Restart survival (simulate process restart)")
    user_id = 9999
    cache_key = _key_broker_ctx(user_id)

    # Pretend we are about to stop the app
    await close_redis()
    print("Closed Redis client (simulating app shutdown)")
    time.sleep(0.2)

    # New client = new process
    got = await cache_get_json(cache_key)
    ttl = await cache_ttl(cache_key)
    print(f"After 'restart': value={got}, ttl={ttl}s")
    assert got is not None, "Cache entry was lost across client restart!"
    print("OK — Redis retains data across app restarts (TTLs continue to tick)")


async def test_inspect_namespace() -> None:
    header("8. All OpenBull keys currently in Redis")
    client = get_redis()
    keys = []
    async for k in client.scan_iter(match=f"{KEY_PREFIX}*", count=500):
        keys.append(k)
    for k in sorted(keys):
        ttl = await client.ttl(k)
        typ = await client.type(k)
        print(f"  {k}  (type={typ}, ttl={ttl}s)")
    print(f"Total: {len(keys)} key(s)")


async def test_invalidation() -> None:
    header("9. Invalidation clears caches")
    await invalidate_user_cache(9999)
    for k in (_key_broker_ctx(9999), _key_api_ctx(9999)):
        v = await cache_get_json(k)
        assert v is None
    print("invalidate_user_cache(9999) -> both keys removed  OK")

    await invalidate_all_caches()
    client = get_redis()
    remaining = []
    async for k in client.scan_iter(match=f"{KEY_PREFIX}api_key:*", count=500):
        remaining.append(k)
    async for k in client.scan_iter(match=f"{KEY_PREFIX}broker_ctx:*", count=500):
        remaining.append(k)
    async for k in client.scan_iter(match=f"{KEY_PREFIX}api_ctx:*", count=500):
        remaining.append(k)
    print(f"After invalidate_all_caches(): {len(remaining)} auth-related key(s) left")
    assert len(remaining) == 0
    print("OK")


async def cleanup() -> None:
    # Remove anything this test created
    await cache_delete_pattern("test:*")
    await cache_delete_pattern("api_key:*")
    await cache_delete_pattern("broker_ctx:*")
    await cache_delete_pattern("api_ctx:*")
    await close_redis()


async def main() -> None:
    try:
        await test_connection()
        await test_api_key_cache()
        await test_invalid_key_cache()
        await test_broker_context_cache()
        await test_api_context_cache()
        await test_ttl_countdown()
        await test_restart_survival()
        await test_inspect_namespace()
        await test_invalidation()
        print("\nAll Redis cache tests passed.")
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
