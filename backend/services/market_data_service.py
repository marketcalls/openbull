"""
Market data service - symbol info, search, expiry dates, intervals.
All functions query the symtoken table or broker config. No broker API calls.
"""

import importlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import get_settings

logger = logging.getLogger(__name__)


async def _query_db(query_str: str, params: dict) -> list:
    """Run an async DB query and return all rows."""
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(text(query_str), params)
            return result.fetchall()
    finally:
        await engine.dispose()


def _run_query(query_str: str, params: dict) -> list:
    """Run a DB query from sync context."""
    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _query_db(query_str, params)).result()


def get_symbol_info(
    symbol: str, exchange: str
) -> tuple[bool, dict[str, Any], int]:
    """Get full symbol info from symtoken table."""
    try:
        rows = _run_query(
            "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
            "expiry, strike, lotsize, instrumenttype, tick_size "
            "FROM symtoken WHERE symbol = :symbol AND exchange = :exchange LIMIT 1",
            {"symbol": symbol, "exchange": exchange},
        )

        if not rows:
            return False, {"status": "error", "message": "Symbol not found"}, 404

        row = rows[0]
        data = {
            "symbol": row[0],
            "brsymbol": row[1],
            "name": row[2],
            "exchange": row[3],
            "brexchange": row[4],
            "token": row[5],
            "expiry": row[6],
            "strike": row[7],
            "lotsize": row[8],
            "instrumenttype": row[9],
            "tick_size": row[10],
        }
        return True, {"status": "success", "data": data}, 200

    except Exception as e:
        logger.error("Error fetching symbol info: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def search_symbols_api(
    query: str, exchange: str | None = None
) -> tuple[bool, dict[str, Any], int]:
    """Search symbols by name or symbol code."""
    try:
        like_pattern = f"%{query}%"

        if exchange:
            rows = _run_query(
                "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
                "expiry, strike, lotsize, instrumenttype, tick_size "
                "FROM symtoken WHERE (symbol ILIKE :q OR name ILIKE :q) "
                "AND exchange = :exchange LIMIT 50",
                {"q": like_pattern, "exchange": exchange},
            )
        else:
            rows = _run_query(
                "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
                "expiry, strike, lotsize, instrumenttype, tick_size "
                "FROM symtoken WHERE (symbol ILIKE :q OR name ILIKE :q) LIMIT 50",
                {"q": like_pattern},
            )

        results = []
        for row in rows:
            results.append({
                "symbol": row[0],
                "brsymbol": row[1],
                "name": row[2],
                "exchange": row[3],
                "brexchange": row[4],
                "token": row[5],
                "expiry": row[6],
                "strike": row[7],
                "lotsize": row[8],
                "instrumenttype": row[9],
                "tick_size": row[10],
            })

        return True, {"status": "success", "data": results}, 200

    except Exception as e:
        logger.error("Error searching symbols: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def get_expiry_dates(
    symbol: str, exchange: str
) -> tuple[bool, dict[str, Any], int]:
    """Get sorted expiry dates for a symbol in an F&O exchange."""
    try:
        rows = _run_query(
            "SELECT DISTINCT expiry FROM symtoken "
            "WHERE name = :symbol AND exchange = :exchange "
            "AND expiry IS NOT NULL AND expiry != ''",
            {"symbol": symbol, "exchange": exchange},
        )

        if not rows:
            rows = _run_query(
                "SELECT DISTINCT expiry FROM symtoken "
                "WHERE symbol LIKE :pattern AND exchange = :exchange "
                "AND expiry IS NOT NULL AND expiry != ''",
                {"pattern": f"{symbol}%", "exchange": exchange},
            )

        if not rows:
            return False, {"status": "error", "message": "No expiry dates found"}, 404

        expiry_strings = [row[0] for row in rows if row[0]]

        def parse_expiry(e):
            try:
                return datetime.strptime(e, "%d-%b-%y")
            except ValueError:
                return datetime.max

        expiry_strings.sort(key=parse_expiry)

        return True, {"status": "success", "data": expiry_strings}, 200

    except Exception as e:
        logger.error("Error fetching expiry dates: %s", e)
        return False, {"status": "error", "message": str(e)}, 500


def get_supported_intervals(
    broker: str,
) -> tuple[bool, dict[str, Any], int]:
    """Get supported candle intervals for the broker."""
    try:
        data_module = importlib.import_module(f"backend.broker.{broker}.api.data")
        intervals = getattr(data_module, "SUPPORTED_INTERVALS", None)

        if intervals is None:
            return False, {"status": "error", "message": "Intervals not defined for this broker"}, 404

        return True, {"status": "success", "data": intervals}, 200

    except ImportError:
        return False, {"status": "error", "message": "Broker module not found"}, 404
    except Exception as e:
        logger.error("Error fetching intervals: %s", e)
        return False, {"status": "error", "message": str(e)}, 500
