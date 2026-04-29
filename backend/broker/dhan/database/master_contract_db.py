"""
Dhan master contract download and symbol table population.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(separate from the main app's engine) to avoid event loop conflicts.

Source CSV: https://images.dhan.co/api-data/api-scrip-master.csv
"""

import asyncio
import logging
import math
import os
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

    Background thread spins up its own event loop via asyncio.run(); asyncpg
    connections can't cross loops, so we cannot reuse the shared app engine.
    Caller MUST dispose the engine after use to release pooled connections.
    """
    from backend.config import get_settings
    engine = create_async_engine(get_settings().database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _download_csv(output_path: str) -> None:
    """Download the Dhan master scrip CSV."""
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    logger.info("Downloading Dhan master contract from %s", url)
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)


def _assign_values(row):
    """Map (broker exchange, instrument) -> (openalgo exchange, brexchange, instrumenttype).

    Ported verbatim from openalgo's dhan database/master_contract_db.py.
    """
    if row["SEM_EXM_EXCH_ID"] == "NSE" and row["SEM_INSTRUMENT_NAME"] == "EQUITY":
        return "NSE", "NSE_EQ", "EQ"
    if row["SEM_EXM_EXCH_ID"] == "BSE" and row["SEM_INSTRUMENT_NAME"] == "EQUITY":
        return "BSE", "BSE_EQ", "EQ"
    if row["SEM_EXM_EXCH_ID"] == "NSE" and row["SEM_INSTRUMENT_NAME"] == "INDEX":
        return "NSE_INDEX", "IDX_I", "INDEX"
    if row["SEM_EXM_EXCH_ID"] == "BSE" and row["SEM_INSTRUMENT_NAME"] == "INDEX":
        return "BSE_INDEX", "IDX_I", "INDEX"
    if row["SEM_EXM_EXCH_ID"] == "MCX" and row["SEM_INSTRUMENT_NAME"] in (
        "FUTIDX", "FUTCOM", "OPTFUT",
    ):
        return (
            "MCX",
            "MCX_COMM",
            row["SEM_OPTION_TYPE"] if "OPT" in row["SEM_INSTRUMENT_NAME"] else "FUT",
        )
    if row["SEM_EXM_EXCH_ID"] == "NSE" and row["SEM_INSTRUMENT_NAME"] in (
        "FUTIDX", "FUTSTK", "OPTIDX", "OPTSTK", "OPTFUT",
    ):
        return (
            "NFO",
            "NSE_FNO",
            row["SEM_OPTION_TYPE"] if "OPT" in row["SEM_INSTRUMENT_NAME"] else "FUT",
        )
    if row["SEM_EXM_EXCH_ID"] == "NSE" and row["SEM_INSTRUMENT_NAME"] in ("FUTCUR", "OPTCUR"):
        return (
            "CDS",
            "NSE_CURRENCY",
            row["SEM_OPTION_TYPE"] if "OPT" in row["SEM_INSTRUMENT_NAME"] else "FUT",
        )
    if row["SEM_EXM_EXCH_ID"] == "BSE" and row["SEM_INSTRUMENT_NAME"] in (
        "FUTIDX", "FUTSTK", "OPTIDX", "OPTSTK",
    ):
        return (
            "BFO",
            "BSE_FNO",
            row["SEM_OPTION_TYPE"] if "OPT" in row["SEM_INSTRUMENT_NAME"] else "FUT",
        )
    if row["SEM_EXM_EXCH_ID"] == "BSE" and row["SEM_INSTRUMENT_NAME"] in ("FUTCUR", "OPTCUR"):
        return (
            "BCD",
            "BSE_CURRENCY",
            row["SEM_OPTION_TYPE"] if "OPT" in row["SEM_INSTRUMENT_NAME"] else "FUT",
        )
    return "Unknown", "Unknown", "Unknown"


def _reformat_symbol(row):
    """Build OpenBull-format symbol from Dhan custom/trading symbol.

    Ported verbatim from openalgo.
    """
    symbol = row["SEM_CUSTOM_SYMBOL"]
    instrument_type = row["instrumenttype"]
    equity = row["SEM_INSTRUMENT_NAME"]
    expiry = str(row["expiry"]).replace("-", "")

    if equity == "EQUITY":
        symbol = row["SEM_TRADING_SYMBOL"]
    elif equity == "INDEX":
        symbol = row["SEM_TRADING_SYMBOL"]
    elif instrument_type == "FUT":
        parts = str(symbol).split(" ")
        if len(parts) == 3:
            symbol = f"{parts[0]}{expiry}{instrument_type}"
        elif len(parts) == 4:
            symbol = f"{parts[0]}{expiry}{instrument_type}"
    elif instrument_type in ("CE", "PE"):
        parts = str(symbol).split(" ")
        if len(parts) == 4:
            symbol = f"{parts[0]}{expiry}{parts[2]}{instrument_type}"
        elif len(parts) == 5:
            symbol = f"{parts[0]}{expiry}{parts[3]}{instrument_type}"
    return symbol


# NSE_INDEX whitelist for normalized symbols (matches openalgo)
_VALID_NSE_INDEX_SYMBOLS = {
    "NIFTY", "NIFTYNXT50", "FINNIFTY", "BANKNIFTY", "MIDCPNIFTY", "INDIAVIX",
    "HANGSENGBEESNAV", "NIFTY100", "NIFTY200", "NIFTY500", "NIFTYALPHA50",
    "NIFTYAUTO", "NIFTYCOMMODITIES", "NIFTYCONSUMPTION", "NIFTYCPSE",
    "NIFTYDIVOPPS50", "NIFTYENERGY", "NIFTYFMCG", "NIFTYGROWSECT15",
    "NIFTYGS10YR", "NIFTYGS10YRCLN", "NIFTYGS1115YR", "NIFTYGS15YRPLUS",
    "NIFTYGS48YR", "NIFTYGS813YR", "NIFTYGSCOMPSITE", "NIFTYINFRA", "NIFTYIT",
    "NIFTYMEDIA", "NIFTYMETAL", "NIFTYMIDLIQ15", "NIFTYMIDCAP100",
    "NIFTYMIDCAP150", "NIFTYMIDCAP50", "NIFTYMIDSML400", "NIFTYMNC",
    "NIFTYPHARMA", "NIFTYPSE", "NIFTYPSUBANK", "NIFTYPVTBANK", "NIFTYREALTY",
    "NIFTYSERVSECTOR", "NIFTYSMLCAP100", "NIFTYSMLCAP250", "NIFTYSMLCAP50",
    "NIFTY100EQLWGT", "NIFTY100LIQ15", "NIFTY100LOWVOL30", "NIFTY100QUALTY30",
    "NIFTY200QUALTY30", "NIFTY50DIVPOINT", "NIFTY50EQLWGT", "NIFTY50PR1XINV",
    "NIFTY50PR2XLEV", "NIFTY50TR1XINV", "NIFTY50TR2XLEV", "NIFTY50VALUE20",
}

_NSE_INDEX_RENAMES = {
    "NIFTYNEXT50": "NIFTYNXT50",
    "NIFTYMCAP50": "NIFTYMIDCAP50",
    "NIFTYMIDSMALLCAP400": "NIFTYMIDSML400",
    "NIFTYSMALLCAP100": "NIFTYSMLCAP100",
    "NIFTYSMALLCAP250": "NIFTYSMLCAP250",
    "NIFTYSMALLCAP50": "NIFTYSMLCAP50",
    "NIFTY100EQUALWEIGHT": "NIFTY100EQLWGT",
    "NIFTY100LOWVOLATILITY30": "NIFTY100LOWVOL30",
    "NIFTYMID100FREE": "NIFTYMIDCAP100",
}

_BSE_INDEX_MAP = {
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
    "SNSX50": "SENSEX50",
    "SNXT50": "BSESENSEXNEXT50",
    "BSE100": "BSE100",
    "BSE200": "BSE200",
    "BSE500": "BSE500",
    "MID150": "BSE150MIDCAPINDEX",
    "LMI250": "BSE250LARGEMIDCAPINDEX",
    "MSL400": "BSE400MIDSMALLCAPINDEX",
    "AUTO": "BSEAUTO",
    "BSE CG": "BSECAPITALGOODS",
    "BSE CD": "BSECONSUMERDURABLES",
    "BSE HC": "BSEHEALTHCARE",
    "BSE IT": "BSEINFORMATIONTECHNOLOGY",
    "CARBON": "BSECARBONEX",
    "CPSE": "BSECPSE",
    "DOL100": "BSEDOLLEX100",
    "DOL200": "BSEDOLLEX200",
    "DOL30": "BSEDOLLEX30",
    "ENERGY": "BSEENERGY",
    "BSEFMC": "BSEFASTMOVINGCONSUMERGOODS",
    "FINSER": "BSEFINANCIALSERVICES",
    "GREENX": "BSEGREENEX",
    "INFRA": "BSEINDIAINFRASTRUCTUREINDEX",
    "INDSTR": "BSEINDUSTRIALS",
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
}


def _process_csv(path: str) -> pd.DataFrame:
    """Process Dhan CSV into the symtoken schema."""
    logger.info("Processing Dhan scrip master CSV")
    df = pd.read_csv(path, low_memory=False)
    df.columns = df.columns.str.strip()

    df["SEM_EXPIRY_DATE"] = pd.to_datetime(df["SEM_EXPIRY_DATE"], errors="coerce")
    df["SEM_EXPIRY_DATE"] = df["SEM_EXPIRY_DATE"].dt.strftime("%d-%b-%y")
    df["SEM_EXPIRY_DATE"] = df["SEM_EXPIRY_DATE"].fillna("-1")

    df["token"] = df["SEM_SMST_SECURITY_ID"].astype(str)
    df["name"] = df.get("SM_SYMBOL_NAME", "")
    df["expiry"] = df["SEM_EXPIRY_DATE"].str.upper()
    df["strike"] = pd.to_numeric(df.get("SEM_STRIKE_PRICE"), errors="coerce")
    df["lotsize"] = pd.to_numeric(df.get("SEM_LOT_UNITS"), errors="coerce").fillna(0).astype(int)
    df["tick_size"] = pd.to_numeric(df.get("SEM_TICK_SIZE"), errors="coerce") / 100
    df["brsymbol"] = df["SEM_TRADING_SYMBOL"]

    df[["exchange", "brexchange", "instrumenttype"]] = df.apply(
        _assign_values, axis=1, result_type="expand",
    )

    # Drop rows that didn't match any known segment
    df = df[df["exchange"] != "Unknown"].copy()

    df["symbol"] = df.apply(_reformat_symbol, axis=1)

    # Normalize NSE_INDEX symbols
    nse_idx_mask = df["exchange"] == "NSE_INDEX"
    original_nse = df.loc[nse_idx_mask, "symbol"].copy()
    df.loc[nse_idx_mask, "symbol"] = (
        df.loc[nse_idx_mask, "symbol"]
        .str.upper()
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )
    df.loc[nse_idx_mask, "symbol"] = df.loc[nse_idx_mask, "symbol"].replace(_NSE_INDEX_RENAMES)
    not_valid = ~df.loc[nse_idx_mask, "symbol"].isin(_VALID_NSE_INDEX_SYMBOLS)
    revert_idx = not_valid[not_valid].index
    df.loc[revert_idx, "symbol"] = original_nse.loc[revert_idx]

    # Normalize BSE_INDEX symbols
    bse_idx_mask = df["exchange"] == "BSE_INDEX"
    df.loc[bse_idx_mask, "symbol"] = df.loc[bse_idx_mask, "symbol"].replace(_BSE_INDEX_MAP)

    token_df = df[
        [
            "symbol", "brsymbol", "name", "exchange", "brexchange",
            "token", "expiry", "strike", "lotsize", "instrumenttype", "tick_size",
        ]
    ].copy()

    return token_df


def _cleanup_temp(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Deleted temporary file %s", path)
    except Exception as e:
        logger.error("Error deleting temp file %s: %s", path, e)


def master_contract_download(auth_token: str | None = None) -> dict:
    """Download and process Dhan master contracts into the symtoken table.

    Note: Dhan's scrip master CSV is publicly accessible and does NOT require
    auth_token. The argument is kept for openbull contract parity.
    """
    output_path = str(TMP_DIR / "dhan_master.csv")

    try:
        _download_csv(output_path)
        token_df = _process_csv(output_path)
        _cleanup_temp(output_path)

        async def _db_ops():
            engine, session_factory = _build_isolated_engine_and_session()
            try:
                async with session_factory() as session:
                    async with session.begin():
                        logger.info("Clearing symtoken table")
                        await session.execute(text("DELETE FROM symtoken"))
                        data_dict = token_df.to_dict(orient="records")
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

        async def _refresh_caches():
            from backend.utils import symtoken_cache
            from backend.broker.upstox.mapping.order_data import _load_symbol_cache
            await symtoken_cache.warm_from_db()
            await _load_symbol_cache()

        asyncio.run(_refresh_caches())

        logger.info("Dhan master contract download completed successfully")
        return {
            "status": "success",
            "message": "Dhan master contracts downloaded",
            "count": len(token_df),
        }

    except Exception as e:
        logger.error("Dhan master contract download failed: %s", e)
        _cleanup_temp(output_path)
        return {"status": "error", "message": str(e)}


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for symbols matching the query on the given exchange.

    Same multi-token ILIKE search used by upstox/zerodha. Splits the query
    on whitespace; every token must appear (case-insensitive) somewhere in
    symbol/brsymbol/name. Up to 50 rows returned, exact-prefix first.
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
