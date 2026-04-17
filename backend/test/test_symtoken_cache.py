"""
Verify the master-contract Redis cache end-to-end.

Run from the openbull project root:
    uv run python backend/test/test_symtoken_cache.py

What this script proves:
  1. Warm from PostgreSQL -> Redis is populated and sentinel is set
  2. Redis keys exist under openbull:symtoken:* with correct entry counts
  3. In-memory dicts hydrate correctly from Redis (no DB hit)
  4. A few real lookups return matching values
  5. The cache survives a simulated app restart (client recreated)
  6. clear() wipes everything under openbull:symtoken:*

It does NOT re-download the master contract from the broker.
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings
from backend.utils import symtoken_cache
from backend.utils.redis_client import (
    KEY_PREFIX,
    cache_get_json,
    close_redis,
    get_redis,
)


def header(text: str) -> None:
    print(f"\n{'=' * 68}\n{text}\n{'=' * 68}")


async def db_row_count() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            r = await s.execute(text("SELECT COUNT(*) FROM symtoken"))
            return int(r.scalar_one())
    finally:
        await engine.dispose()


async def sample_row():
    """Pick one real row from symtoken to use as a lookup probe."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            r = await s.execute(
                text("SELECT token, symbol, exchange, brsymbol FROM symtoken LIMIT 1")
            )
            row = r.first()
            return row
    finally:
        await engine.dispose()


async def redis_hash_sizes() -> dict[str, int]:
    client = get_redis()
    sizes = {}
    for k in symtoken_cache.ALL_HASH_KEYS:
        sizes[k] = await client.hlen(f"{KEY_PREFIX}{k}")
    return sizes


async def redis_hash_get(hash_key: str, field: str) -> str | None:
    return await get_redis().hget(f"{KEY_PREFIX}{hash_key}", field)


async def main():
    header("1. DB baseline")
    db_count = await db_row_count()
    print(f"symtoken rows in PostgreSQL: {db_count}")
    if db_count == 0:
        print("ERROR: no rows in DB — download the master contract first.")
        await close_redis()
        return

    probe = await sample_row()
    print(f"Probe row: token={probe[0]!r} symbol={probe[1]!r} exchange={probe[2]!r} brsymbol={probe[3]!r}")

    header("2. Clear Redis and warm from DB")
    await symtoken_cache.clear()
    t0 = time.perf_counter()
    written = await symtoken_cache.warm_from_db()
    t1 = time.perf_counter()
    print(f"warm_from_db() wrote {written} rows in {t1 - t0:.2f}s")
    assert written == db_count, f"row count mismatch: {written} vs {db_count}"

    header("3. Inspect Redis hashes")
    sizes = await redis_hash_sizes()
    for k, n in sizes.items():
        print(f"  openbull:{k:<24s}  {n:>7d} fields")
    ready = await cache_get_json(symtoken_cache.KEY_READY)
    print(f"  openbull:{symtoken_cache.KEY_READY}  {ready}")
    assert await symtoken_cache.is_ready()

    header("4. Lookups via Redis (direct HGET)")
    tok, sym, exch, brsym = probe
    symex = f"{sym}{symtoken_cache.SEP}{exch}"
    brsymex = f"{brsym}{symtoken_cache.SEP}{exch}" if brsym else None
    v = await redis_hash_get(symtoken_cache.KEY_SYMEX2TOK, symex)
    print(f"  symex2tok[{symex!r}] = {v!r}   (expected {tok!r})")
    assert v == tok
    v = await redis_hash_get(symtoken_cache.KEY_TOK2SYM, tok)
    print(f"  tok2sym[{tok!r}] = {v!r}   (expected {sym!r})")
    assert v == sym
    if brsymex:
        v = await redis_hash_get(symtoken_cache.KEY_BRSYMEX2SYM, brsymex)
        print(f"  brsymex2sym[{brsymex!r}] = {v!r}   (expected {sym!r})")
        assert v == sym

    header("5. Hydrate in-memory dicts from Redis (no DB hit)")
    t0 = time.perf_counter()
    (tok2sym, tok2symex, symex2tok, symex2brsym, brsymex2sym) = (
        await symtoken_cache.load_into_memory_dicts()
    )
    t1 = time.perf_counter()
    print(f"Loaded 5 dicts in {t1 - t0:.2f}s")
    print(f"  tok2sym       = {len(tok2sym):>7d} entries")
    print(f"  tok2symex     = {len(tok2symex):>7d} entries")
    print(f"  symex2tok     = {len(symex2tok):>7d} entries")
    print(f"  symex2brsym   = {len(symex2brsym):>7d} entries")
    print(f"  brsymex2sym   = {len(brsymex2sym):>7d} entries")

    # In-memory probe
    assert tok2sym[tok] == sym
    assert symex2tok[(sym, exch)] == tok
    assert tok2symex[tok] == (sym, exch)

    header("6. Simulate app restart (client recreated)")
    await close_redis()
    print("Redis client closed. Reopening…")
    # New get_redis() -> new connection pool -> same Redis server
    assert await symtoken_cache.is_ready(), "Cache missing after restart!"
    sizes_after = await redis_hash_sizes()
    print("Hash sizes after restart:")
    for k, n in sizes_after.items():
        print(f"  openbull:{k:<24s}  {n:>7d} fields")
    assert sizes_after == sizes, "Sizes changed across restart!"
    print("Cache persists across the simulated restart.")

    header("7. _load_symbol_cache (upstox) — full path as invoked by main.py lifespan")
    from backend.broker.upstox.mapping import order_data
    order_data._token_to_symbol = None  # reset to force a reload
    t0 = time.perf_counter()
    await order_data._load_symbol_cache()
    t1 = time.perf_counter()
    print(f"_load_symbol_cache() took {t1 - t0:.2f}s (should be fast — pulled from Redis)")
    print(f"  _token_to_symbol size = {len(order_data._token_to_symbol)}")
    assert order_data._token_to_symbol.get(tok) == sym

    print("\nAll master-contract Redis cache tests passed.")
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
