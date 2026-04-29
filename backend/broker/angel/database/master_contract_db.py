"""
Angel One master contract download and symbol table population.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(separate from the main app's engine) to avoid event loop conflicts.

Port of openalgo's angel master_contract_db.py — JSON processing logic is
preserved verbatim; download/DB plumbing follows the upstox/zerodha template.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
TMP_DIR.mkdir(exist_ok=True)

ANGEL_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


def _build_isolated_engine_and_session():
    """Create a fresh engine + sessionmaker for use under asyncio.run().

    The download path runs in a background thread and spins up a temporary
    event loop via asyncio.run(); asyncpg connections can't cross loops, so
    the shared app engine can't be reused here. Caller MUST dispose the
    engine after use to release pooled connections.
    """
    from backend.config import get_settings
    engine = create_async_engine(get_settings().database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _download_json(url: str, output_path: str) -> None:
    logger.info("Downloading Angel master contract from %s", url)
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)
    logger.info("Angel master contract downloaded (%d bytes)", len(response.content))


def _convert_date(date_str):
    """Convert from '19MAR2024' to '19-MAR-24'."""
    try:
        return datetime.strptime(date_str, "%d%b%Y").strftime("%d-%b-%y")
    except (ValueError, TypeError):
        return date_str


def _process_angel_json(path: str) -> pd.DataFrame:
    """Process Angel JSON file into a DataFrame matching the symtoken schema.

    Logic ported verbatim from openalgo's angel master_contract_db.py.
    """
    df = pd.read_json(path)

    df = df.rename(
        columns={
            "exch_seg": "exchange",
            "instrumenttype": "instrumenttype",
            "lotsize": "lotsize",
            "strike": "strike",
            "symbol": "symbol",
            "token": "token",
            "name": "name",
            "tick_size": "tick_size",
        }
    )

    df["brsymbol"] = df["symbol"]
    df["brexchange"] = df["exchange"]

    # Update exchange names based on instrument type for indices.
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "NSE"), "exchange"] = "NSE_INDEX"
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "BSE"), "exchange"] = "BSE_INDEX"
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "MCX"), "exchange"] = "MCX_INDEX"

    # Strip equity suffixes from the symbol.
    df["symbol"] = df["symbol"].str.replace("-EQ|-BE|-MF|-SG", "", regex=True)

    # Expiry normalization.
    df["expiry"] = df["expiry"].apply(lambda x: _convert_date(x) if pd.notnull(x) else x)
    df["expiry"] = df["expiry"].str.upper()

    # strike / lotsize / tick_size scaling.
    df["strike"] = df["strike"].astype(float) / 100
    df.loc[(df["instrumenttype"] == "OPTCUR") & (df["exchange"] == "CDS"), "strike"] = (
        df["strike"].astype(float) / 100000
    )
    df.loc[(df["instrumenttype"] == "OPTIRC") & (df["exchange"] == "CDS"), "strike"] = (
        df["strike"].astype(float) / 100000
    )
    df["lotsize"] = df["lotsize"].astype(int)
    df["tick_size"] = df["tick_size"].astype(float) / 100

    # CDS / MCX futures symbol reformat.
    df.loc[(df["instrumenttype"] == "FUTCUR") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    df.loc[(df["instrumenttype"] == "FUTIRC") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    df.loc[(df["instrumenttype"] == "FUTCOM") & (df["exchange"] == "MCX"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )

    # CDS / MCX options symbol reformat.
    for inst_exch in [("OPTCUR", "CDS"), ("OPTIRC", "CDS"), ("OPTFUT", "MCX")]:
        inst_type, exch = inst_exch
        df.loc[(df["instrumenttype"] == inst_type) & (df["exchange"] == exch), "symbol"] = (
            df["name"]
            + df["expiry"].str.replace("-", "", regex=False)
            + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
            + df["symbol"].str[-2:]
        )

    # BFO Index/Stock futures + options.
    df.loc[(df["instrumenttype"] == "FUTIDX") & (df["exchange"] == "BFO"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    df.loc[(df["instrumenttype"] == "FUTSTK") & (df["exchange"] == "BFO"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    for inst_type in ("OPTIDX", "OPTSTK"):
        df.loc[
            (df["instrumenttype"] == inst_type)
            & (df["exchange"] == "BFO")
            & (df["symbol"].str.endswith("CE", na=False)),
            "symbol",
        ] = (
            df["name"]
            + df["expiry"].str.replace("-", "", regex=False)
            + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
            + "CE"
        )
        df.loc[
            (df["instrumenttype"] == inst_type)
            & (df["exchange"] == "BFO")
            & (df["symbol"].str.endswith("PE", na=False)),
            "symbol",
        ] = (
            df["name"]
            + df["expiry"].str.replace("-", "", regex=False)
            + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
            + "PE"
        )

    # NSE_INDEX symbol normalization (uppercase, no spaces/hyphens).
    nse_idx_mask = df["exchange"] == "NSE_INDEX"
    df.loc[nse_idx_mask, "symbol"] = (
        df.loc[nse_idx_mask, "name"]
        .str.upper()
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )

    # BSE_INDEX symbol normalization (also strip 'S&P ' prefix).
    bse_idx_mask = df["exchange"] == "BSE_INDEX"
    df.loc[bse_idx_mask, "symbol"] = (
        df.loc[bse_idx_mask, "name"]
        .str.upper()
        .str.replace("S&P ", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )

    # Major-index aliases used by OpenBull.
    df["symbol"] = df["symbol"].replace({
        "NIFTY50": "NIFTY",
        "NIFTYBANK": "BANKNIFTY",
        "NIFTYFINSERVICE": "FINNIFTY",
        "NIFTYNEXT50": "NIFTYNXT50",
        "NIFTYMIDSELECT": "MIDCPNIFTY",
        "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
        "SNSX50": "SENSEX50",
    })

    # Convert OPTIDX/OPTSTK/OPTFUT/OPTCUR/OPTIRC -> CE/PE based on suffix.
    for src in ("OPTIDX", "OPTSTK", "OPTFUT", "OPTCUR", "OPTIRC"):
        df.loc[
            (df["instrumenttype"] == src) & (df["symbol"].str.endswith("CE", na=False)),
            "instrumenttype",
        ] = "CE"
        df.loc[
            (df["instrumenttype"] == src) & (df["symbol"].str.endswith("PE", na=False)),
            "instrumenttype",
        ] = "PE"

    # Collapse all FUT* instrument types to plain FUT.
    for src in ("FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR", "FUTIRC", "FUTIRT"):
        df.loc[df["instrumenttype"] == src, "instrumenttype"] = "FUT"

    # Reduce to the columns the symtoken table expects.
    df = df[
        [
            "symbol",
            "brsymbol",
            "name",
            "exchange",
            "brexchange",
            "token",
            "expiry",
            "strike",
            "lotsize",
            "instrumenttype",
            "tick_size",
        ]
    ]

    # Cast token to string (matches upstox/zerodha schema).
    df["token"] = df["token"].astype(str)

    return df


def _cleanup_temp(json_path: str) -> None:
    try:
        if os.path.exists(json_path):
            os.remove(json_path)
            logger.info("Deleted temporary file %s", json_path)
    except Exception as e:
        logger.error("Error deleting temp file %s: %s", json_path, e)


def master_contract_download(auth_token: str | None = None) -> dict:
    """Download Angel master contract and populate symtoken table.

    ``auth_token`` is accepted for signature parity but Angel's master file
    is publicly hosted; no auth needed.
    """
    json_path = str(TMP_DIR / "angel.json")

    try:
        _download_json(ANGEL_MASTER_URL, json_path)
        token_df = _process_angel_json(json_path)
        _cleanup_temp(json_path)

        async def _db_ops():
            engine, session_factory = _build_isolated_engine_and_session()
            try:
                async with session_factory() as session:
                    async with session.begin():
                        logger.info("Clearing symtoken table")
                        await session.execute(text("DELETE FROM symtoken"))
                        data_dict = token_df.to_dict(orient="records")
                        # asyncpg can't accept NaN floats — replace with None.
                        import math
                        for row in data_dict:
                            for k, v in row.items():
                                if isinstance(v, float) and math.isnan(v):
                                    row[k] = None
                        logger.info("Performing bulk insert of %d records", len(data_dict))
                        await session.execute(
                            text(
                                "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, "
                                "token, expiry, strike, lotsize, instrumenttype, tick_size) "
                                "VALUES (:symbol, :brsymbol, :name, :exchange, :brexchange, "
                                ":token, :expiry, :strike, :lotsize, :instrumenttype, :tick_size)"
                            ),
                            data_dict,
                        )
                logger.info("Bulk insert completed with %d records", len(data_dict))
            finally:
                await engine.dispose()

        asyncio.run(_db_ops())

        # Mirror the freshly inserted rows into Redis, then reload the in-memory dicts.
        async def _refresh_caches():
            from backend.utils import symtoken_cache
            from backend.broker.upstox.mapping.order_data import _load_symbol_cache
            await symtoken_cache.warm_from_db()
            await _load_symbol_cache()

        asyncio.run(_refresh_caches())

        logger.info("Angel master contract download completed successfully")
        return {
            "status": "success",
            "message": "Angel master contracts downloaded",
            "count": len(token_df),
        }

    except Exception as e:
        logger.error("Angel master contract download failed: %s", e)
        _cleanup_temp(json_path)
        return {"status": "error", "message": str(e)}


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for symbols on the given exchange.

    Reuses the same multi-token contains-match algorithm as upstox/zerodha:
    splits the query on whitespace and requires every token to appear in
    one of (symbol, brsymbol, name). Returns up to 50 rows ordered by
    prefix-match-first, then length, then alphabetical.
    """
    tokens = [t for t in symbol.split() if t][:6]
    if not tokens:
        return []

    where_parts = ["exchange = :exchange"]
    params: dict = {"exchange": exchange, "prefix": f"{tokens[0]}%"}
    for i, tok in enumerate(tokens):
        key = f"t{i}"
        where_parts.append(
            f"(symbol ILIKE :{key} OR brsymbol ILIKE :{key} OR name ILIKE :{key})"
        )
        params[key] = f"%{tok}%"

    sql = (
        "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
        "expiry, strike, lotsize, instrumenttype, tick_size "
        "FROM symtoken WHERE " + " AND ".join(where_parts) + " "
        "ORDER BY "
        "  CASE WHEN symbol ILIKE :prefix THEN 0 ELSE 1 END, "
        "  length(symbol), symbol "
        "LIMIT 50"
    )

    from backend.database import async_session
    async with async_session() as session:
        result = await session.execute(text(sql), params)
        return [
            {
                "symbol": r[0], "brsymbol": r[1], "name": r[2], "exchange": r[3],
                "brexchange": r[4], "token": r[5], "expiry": r[6], "strike": r[7],
                "lotsize": r[8], "instrumenttype": r[9], "tick_size": r[10],
            }
            for r in result.fetchall()
        ]
