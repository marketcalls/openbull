"""
Redis-backed master contract cache.

The ``symtoken`` table is global and wiped on each master-contract download.
We mirror it into Redis so that:

- **App restart** does NOT re-read 122k rows from PostgreSQL — the in-memory
  lookup dicts are hydrated from Redis in seconds.
- **Redis restart** (cold) is recovered automatically: the loader falls back
  to PostgreSQL and repopulates Redis.
- **Master-contract download** rewrites Redis after the DB bulk-insert so
  the caches stay in sync.

Five hashes mirror the five in-memory lookup dicts used by ``order_data``:

===========================  ==========================================
Redis hash key               Field -> value
===========================  ==========================================
symtoken:tok2sym             token -> symbol
symtoken:tok2symex           token -> "symbol||exchange"
symtoken:symex2tok           "symbol||exchange" -> token
symtoken:symex2brsym         "symbol||exchange" -> brsymbol
symtoken:brsymex2sym         "brsymbol||exchange" -> symbol
symtoken:ready               JSON sentinel {count, ts}
===========================  ==========================================
"""

from __future__ import annotations

import json
import logging
import time
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from backend.config import get_settings
from backend.utils.redis_client import (
    cache_delete,
    cache_exists,
    cache_set_json,
    get_redis,
    hash_hgetall,
    hash_hmset_pipelined,
)

logger = logging.getLogger(__name__)

SEP = "||"

KEY_READY = "symtoken:ready"
KEY_TOK2SYM = "symtoken:tok2sym"
KEY_TOK2SYMEX = "symtoken:tok2symex"
KEY_SYMEX2TOK = "symtoken:symex2tok"
KEY_SYMEX2BRSYM = "symtoken:symex2brsym"
KEY_BRSYMEX2SYM = "symtoken:brsymex2sym"

ALL_HASH_KEYS = (
    KEY_TOK2SYM,
    KEY_TOK2SYMEX,
    KEY_SYMEX2TOK,
    KEY_SYMEX2BRSYM,
    KEY_BRSYMEX2SYM,
)


def _encode_pair(a: str, b: str) -> str:
    return f"{a}{SEP}{b}"


def _decode_pair(s: str) -> tuple[str, str]:
    a, _, b = s.partition(SEP)
    return a, b


def _session_factory():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def is_ready() -> bool:
    """Return True if Redis holds a populated master-contract cache."""
    return await cache_exists(KEY_READY)


async def clear() -> None:
    """Remove all master-contract entries from Redis."""
    await cache_delete(KEY_READY, *ALL_HASH_KEYS)


async def _fetch_rows_from_db() -> list[tuple[str, str, str, str]]:
    """Fetch (token, symbol, exchange, brsymbol) rows from PostgreSQL."""
    factory, engine = _session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                text("SELECT token, symbol, exchange, brsymbol FROM symtoken")
            )
            return [(r[0], r[1], r[2], r[3]) for r in result.fetchall()]
    finally:
        await engine.dispose()


def _build_hash_dicts(
    rows: Iterable[tuple[str, str, str, str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    tok2sym: dict[str, str] = {}
    tok2symex: dict[str, str] = {}
    symex2tok: dict[str, str] = {}
    symex2brsym: dict[str, str] = {}
    brsymex2sym: dict[str, str] = {}

    for token, symbol, exchange, brsymbol in rows:
        if token is None or symbol is None or exchange is None:
            continue
        symex = _encode_pair(symbol, exchange)
        tok2sym[token] = symbol
        tok2symex[token] = symex
        symex2tok[symex] = token
        if brsymbol:
            symex2brsym[symex] = brsymbol
            brsymex2sym[_encode_pair(brsymbol, exchange)] = symbol
        # Numeric token reverse mapping (for Zerodha "12345::::67890" -> "12345")
        if "::::" in token:
            numeric_part = token.split("::::")[0]
            tok2symex[numeric_part] = symex

    return tok2sym, tok2symex, symex2tok, symex2brsym, brsymex2sym


async def warm_from_db() -> int:
    """Read the full symtoken table and populate Redis.

    Called when the cache is cold (first startup, Redis flushed) or right
    after a master-contract download. Returns the number of DB rows written.
    """
    rows = await _fetch_rows_from_db()
    if not rows:
        logger.warning("symtoken table is empty — nothing to warm into Redis")
        await clear()
        return 0

    tok2sym, tok2symex, symex2tok, symex2brsym, brsymex2sym = _build_hash_dicts(rows)

    await clear()
    total = 0
    total += await hash_hmset_pipelined(KEY_TOK2SYM, tok2sym)
    total += await hash_hmset_pipelined(KEY_TOK2SYMEX, tok2symex)
    total += await hash_hmset_pipelined(KEY_SYMEX2TOK, symex2tok)
    total += await hash_hmset_pipelined(KEY_SYMEX2BRSYM, symex2brsym)
    total += await hash_hmset_pipelined(KEY_BRSYMEX2SYM, brsymex2sym)

    await cache_set_json(
        KEY_READY,
        {"rows": len(rows), "ts": int(time.time())},
        ttl_seconds=0,  # sentinel is persistent (see note below)
    )

    logger.info(
        "symtoken cache warmed into Redis: %d rows -> %d hash fields", len(rows), total
    )
    return len(rows)


async def load_into_memory_dicts() -> tuple[
    dict[str, str],
    dict[str, tuple[str, str]],
    dict[tuple[str, str], str],
    dict[tuple[str, str], str],
    dict[tuple[str, str], str],
]:
    """Hydrate the five in-memory lookup dicts used by ``order_data`` from Redis.

    Returns (tok2sym, tok2symex, symex2tok, symex2brsym, brsymex2sym) where
    the second dict's values are ``(symbol, exchange)`` tuples.
    Returns all-empty dicts if Redis is empty / unreachable.
    """
    tok2sym_raw = await hash_hgetall(KEY_TOK2SYM)
    tok2symex_raw = await hash_hgetall(KEY_TOK2SYMEX)
    symex2tok_raw = await hash_hgetall(KEY_SYMEX2TOK)
    symex2brsym_raw = await hash_hgetall(KEY_SYMEX2BRSYM)
    brsymex2sym_raw = await hash_hgetall(KEY_BRSYMEX2SYM)

    tok2sym = dict(tok2sym_raw)
    tok2symex = {tok: _decode_pair(v) for tok, v in tok2symex_raw.items()}
    symex2tok = {_decode_pair(k): v for k, v in symex2tok_raw.items()}
    symex2brsym = {_decode_pair(k): v for k, v in symex2brsym_raw.items()}
    brsymex2sym = {_decode_pair(k): v for k, v in brsymex2sym_raw.items()}

    return tok2sym, tok2symex, symex2tok, symex2brsym, brsymex2sym
