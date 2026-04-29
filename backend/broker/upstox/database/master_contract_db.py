"""
Upstox master contract download and symbol table population.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(separate from the main app's engine) to avoid event loop conflicts.
"""

import asyncio
import gzip
import logging
import os
import shutil
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
TMP_DIR.mkdir(exist_ok=True)


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


def _download_and_unzip(url: str, gz_path: str, json_path: str):
    """Download gzipped JSON from Upstox and decompress."""
    logger.info("Downloading Upstox master contract from %s", url)
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    with open(gz_path, "wb") as f:
        f.write(response.content)
    logger.info("Decompressing JSON file")
    with gzip.open(gz_path, "rb") as f_in:
        with open(json_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def _reformat_symbol(row):
    symbol = row["symbol"]
    instrument_type = row["instrumenttype"]

    if instrument_type == "FUT":
        parts = symbol.split(" ")
        if len(parts) == 5:
            symbol = parts[0] + parts[2] + parts[3] + parts[4] + parts[1]
    elif instrument_type in ["CE", "PE"]:
        parts = symbol.split(" ")
        if len(parts) == 6:
            symbol = parts[0] + parts[3] + parts[4] + parts[5] + parts[1] + parts[2]

    return symbol


def _process_json(path: str) -> pd.DataFrame:
    """Process Upstox JSON file into a DataFrame matching symtoken schema."""
    logger.info("Processing Upstox master contract data")
    df = pd.read_json(path)

    # Filter out commodity segment
    df = df[df["segment"] != "NSE_COM"]

    exchange_map = {
        "NSE_EQ": "NSE",
        "NSE_FO": "NFO",
        "NCD_FO": "CDS",
        "NSE_INDEX": "NSE_INDEX",
        "BSE_INDEX": "BSE_INDEX",
        "BSE_EQ": "BSE",
        "BSE_FO": "BFO",
        "BCD_FO": "BCD",
        "MCX_FO": "MCX",
    }
    segment_copy = df["segment"].copy()
    df["segment"] = df["segment"].map(exchange_map)
    df["expiry"] = pd.to_datetime(df["expiry"], unit="ms").dt.strftime("%d-%b-%y").str.upper()

    df = df[
        [
            "instrument_key",
            "trading_symbol",
            "name",
            "expiry",
            "strike_price",
            "lot_size",
            "instrument_type",
            "segment",
            "tick_size",
        ]
    ].rename(
        columns={
            "instrument_key": "token",
            "trading_symbol": "symbol",
            "name": "name",
            "expiry": "expiry",
            "strike_price": "strike",
            "lot_size": "lotsize",
            "instrument_type": "instrumenttype",
            "segment": "exchange",
            "tick_size": "tick_size",
        }
    )

    # Upstox returns tick_size in paisa, convert to rupees
    df["tick_size"] = df["tick_size"] / 100

    df["brsymbol"] = df["symbol"]
    df["symbol"] = df.apply(_reformat_symbol, axis=1)
    df["brexchange"] = segment_copy

    # NSE Index symbol mapping
    df["symbol"] = df["symbol"].replace({
        "NIFTY 50": "NIFTY",
        "NIFTY NEXT 50": "NIFTYNXT50",
        "NIFTY FIN SERVICE": "FINNIFTY",
        "NIFTY BANK": "BANKNIFTY",
        "NIFTY MID SELECT": "MIDCPNIFTY",
        "INDIA VIX": "INDIAVIX",
        "NIFTY 100": "NIFTY100",
        "NIFTY 200": "NIFTY200",
        "NIFTY 500": "NIFTY500",
        "NIFTY AUTO": "NIFTYAUTO",
        "NIFTY ENERGY": "NIFTYENERGY",
        "NIFTY FMCG": "NIFTYFMCG",
        "NIFTY IT": "NIFTYIT",
        "NIFTY MEDIA": "NIFTYMEDIA",
        "NIFTY METAL": "NIFTYMETAL",
        "NIFTY PHARMA": "NIFTYPHARMA",
        "NIFTY PSU BANK": "NIFTYPSUBANK",
        "NIFTY PVT BANK": "NIFTYPVTBANK",
        "NIFTY REALTY": "NIFTYREALTY",
        "NIFTY INFRA": "NIFTYINFRA",
        "NIFTY COMMODITIES": "NIFTYCOMMODITIES",
        "NIFTY CONSUMPTION": "NIFTYCONSUMPTION",
        "NIFTY CPSE": "NIFTYCPSE",
        "NIFTY MIDCAP 50": "NIFTYMIDCAP50",
        "NIFTY MIDCAP 100": "NIFTYMIDCAP100",
        "NIFTY MIDCAP 150": "NIFTYMIDCAP150",
        "NIFTY SMLCAP 50": "NIFTYSMLCAP50",
        "NIFTY SMLCAP 100": "NIFTYSMLCAP100",
        "NIFTY SMLCAP 250": "NIFTYSMLCAP250",
        "NIFTY SERV SECTOR": "NIFTYSERVSECTOR",
        "NIFTY MNC": "NIFTYMNC",
        "NIFTY PSE": "NIFTYPSE",
        "NIFTY ALPHA 50": "NIFTYALPHA50",
        "NIFTY DIV OPPS 50": "NIFTYDIVOPPS50",
        "NIFTY GROWSECT 15": "NIFTYGROWSECT15",
        "NIFTY MID LIQ 15": "NIFTYMIDLIQ15",
        "NIFTY MIDSML 400": "NIFTYMIDSML400",
        "NIFTY100 EQL WGT": "NIFTY100EQLWGT",
        "NIFTY100 LIQ 15": "NIFTY100LIQ15",
        "NIFTY100 LOWVOL30": "NIFTY100LOWVOL30",
        "NIFTY100 QUALTY30": "NIFTY100QUALTY30",
        "NIFTY200 QUALTY30": "NIFTY200QUALTY30",
        "NIFTY50 EQL WGT": "NIFTY50EQLWGT",
        "NIFTY50 VALUE 20": "NIFTY50VALUE20",
        "NIFTY GS 10YR": "NIFTYGS10YR",
        "NIFTY GS 10YR CLN": "NIFTYGS10YRCLN",
        "NIFTY GS 11 15YR": "NIFTYGS1115YR",
        "NIFTY GS 15YRPLUS": "NIFTYGS15YRPLUS",
        "NIFTY GS 4 8YR": "NIFTYGS48YR",
        "NIFTY GS 8 13YR": "NIFTYGS813YR",
        "NIFTY GS COMPSITE": "NIFTYGSCOMPSITE",
        "NIFTY50 DIV POINT": "NIFTY50DIVPOINT",
        "NIFTY50 PR 1X INV": "NIFTY50PR1XINV",
        "NIFTY50 PR 2X LEV": "NIFTY50PR2XLEV",
        "NIFTY50 TR 1X INV": "NIFTY50TR1XINV",
        "NIFTY50 TR 2X LEV": "NIFTY50TR2XLEV",
        "HANGSENG BEES NAV": "HANGSENGBEESNAV",
    })

    # BSE Index symbol mapping (only BSE_INDEX rows)
    bse_idx_mask = df["exchange"] == "BSE_INDEX"
    df.loc[bse_idx_mask, "symbol"] = df.loc[bse_idx_mask, "symbol"].replace({
        "SNSX50": "SENSEX50",
        "SNXT50": "BSESENSEXNEXT50",
        "MID150": "BSE150MIDCAPINDEX",
        "LMI250": "BSE250LARGEMIDCAPINDEX",
        "MSL400": "BSE400MIDSMALLCAPINDEX",
        "AUTO": "BSEAUTO",
        "BSE CG": "BSECAPITALGOODS",
        "CARBON": "BSECARBONEX",
        "BSE CD": "BSECONSUMERDURABLES",
        "CPSE": "BSECPSE",
        "DOL100": "BSEDOLLEX100",
        "DOL200": "BSEDOLLEX200",
        "DOL30": "BSEDOLLEX30",
        "ENERGY": "BSEENERGY",
        "BSEFMC": "BSEFASTMOVINGCONSUMERGOODS",
        "FINSER": "BSEFINANCIALSERVICES",
        "GREENX": "BSEGREENEX",
        "BSE HC": "BSEHEALTHCARE",
        "INFRA": "BSEINDIAINFRASTRUCTUREINDEX",
        "INDSTR": "BSEINDUSTRIALS",
        "BSE IT": "BSEINFORMATIONTECHNOLOGY",
        "BSEIPO": "BSEIPO",
        "LRGCAP": "BSELARGECAP",
        "METAL": "BSEMETAL",
        "MIDCAP": "BSEMIDCAP",
        "MIDSEL": "BSEMIDCAPSELECTINDEX",
        "OILGAS": "BSEOIL&GAS",
        "POWER": "BSEPOWER",
        "BSEPSU": "BSEPSU",
        "REALTY": "BSEREALTY",
        "SMLCAP": "BSESMALLCAP",
        "SMLSEL": "BSESMALLCAPSELECTINDEX",
        "SMEIPO": "BSESMEIPO",
        "TECK": "BSETECK",
        "TELCOM": "BSETELECOM",
    })

    return df


def _cleanup_temp(gz_path: str, json_path: str):
    for path in (gz_path, json_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.info("Deleted temporary file %s", path)
        except Exception as e:
            logger.error("Error deleting temp file %s: %s", path, e)


def master_contract_download() -> dict:
    """Download and process Upstox master contracts into the symtoken table."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    gz_path = str(TMP_DIR / "temp_upstox.json.gz")
    json_path = str(TMP_DIR / "upstox.json")

    try:
        _download_and_unzip(url, gz_path, json_path)
        token_df = _process_json(json_path)
        _cleanup_temp(gz_path, json_path)

        async def _db_ops():
            engine, session_factory = _build_isolated_engine_and_session()
            try:
                async with session_factory() as session:
                    async with session.begin():
                        logger.info("Clearing symtoken table")
                        await session.execute(text("DELETE FROM symtoken"))
                        data_dict = token_df.to_dict(orient="records")
                        # Replace NaN with None for asyncpg compatibility
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

        # Mirror the freshly-inserted rows into Redis, then reload in-memory dicts
        async def _refresh_caches():
            from backend.utils import symtoken_cache
            from backend.broker.upstox.mapping.order_data import _load_symbol_cache
            await symtoken_cache.warm_from_db()
            await _load_symbol_cache()

        asyncio.run(_refresh_caches())

        logger.info("Upstox master contract download completed successfully")
        return {"status": "success", "message": "Upstox master contracts downloaded", "count": len(token_df)}

    except Exception as e:
        logger.error("Upstox master contract download failed: %s", e)
        _cleanup_temp(gz_path, json_path)
        return {"status": "error", "message": str(e)}


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for symbols matching the query on the given exchange.

    The query is split on whitespace into tokens; every token must appear
    (case-insensitive, anywhere) in one of ``symbol``, ``brsymbol``, or
    ``name``. This lets users type loose forms like ``NIFTY 28APR26`` and
    still match ``NIFTY28APR2625000CE`` — because "NIFTY" and "28APR26"
    both hit the ``symbol`` column. Tokens are capped to guard against
    pathological queries; the entire query is already capped at 50 chars
    by the route's Pydantic validator.

    Ordering: rows whose ``symbol`` starts with the first token come
    first, then by length, then alphabetical — so exact prefix matches
    like plain "NIFTY" float to the top. Returns up to 50 rows.
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
