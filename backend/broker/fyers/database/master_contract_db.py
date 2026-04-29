"""
Fyers master contract download and symbol table population.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(separate from the main app's engine) to avoid event loop conflicts.

Fyers publishes per-segment master files (CSV for cash/derivatives, JSON for
CDS/MCX). We download all of them, normalize each to the common
``symtoken`` schema, then bulk-insert.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
TMP_DIR.mkdir(exist_ok=True)


# Fyers per-segment master files.
_CSV_URLS = {
    "NSE_CD": "https://public.fyers.in/sym_details/NSE_CD_sym_master.json",
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "NSE_CM": "https://public.fyers.in/sym_details/NSE_CM.csv",
    "BSE_CM": "https://public.fyers.in/sym_details/BSE_CM.csv",
    "BSE_FO": "https://public.fyers.in/sym_details/BSE_FO.csv",
    "MCX_COM": "https://public.fyers.in/sym_details/MCX_COM_sym_master.json",
}

# Headers for the CSV master files (positional, per Fyers spec).
_CSV_HEADERS = [
    "Fytoken",
    "Symbol Details",
    "Exchange Instrument type",
    "Minimum lot size",
    "Tick size",
    "ISIN",
    "Trading Session",
    "Last update date",
    "Expiry date",
    "Symbol ticker",
    "Exchange",
    "Segment",
    "Scrip code",
    "Underlying symbol",
    "Underlying scrip code",
    "Strike price",
    "Option type",
    "Underlying FyToken",
    "Reserved column1",
    "Reserved column2",
    "Reserved column3",
]

_CSV_DTYPES = {
    "Fytoken": str,
    "Symbol Details": str,
    "Exchange Instrument type": "Int64",
    "Minimum lot size": "Int64",
    "Tick size": float,
    "ISIN": str,
    "Trading Session": str,
    "Last update date": str,
    "Expiry date": str,
    "Symbol ticker": str,
    "Exchange": "Int64",
    "Segment": "Int64",
    "Scrip code": "Int64",
    "Underlying symbol": str,
    "Underlying scrip code": "Int64",
    "Strike price": float,
    "Option type": str,
    "Underlying FyToken": str,
    "Reserved column1": str,
    "Reserved column2": str,
    "Reserved column3": str,
}

# BSE_INDEX broker symbol -> OpenBull standard symbol.
_BSE_INDEX_MAP = {
    "SENSEX": "SENSEX",
    "SENSEX50": "SENSEX50",
    "BANKEX": "BANKEX",
    "SNXT50": "BSESENSEXNEXT50",
    "100": "BSE100",
    "200": "BSE200",
    "500": "BSE500",
    "150MIDCAP": "BSE150MIDCAPINDEX",
    "250LARGEMIDCAP": "BSE250LARGEMIDCAPINDEX",
    "400MIDSMALLCAP": "BSE400MIDSMALLCAPINDEX",
    "AUTO": "BSEAUTO",
    "CG": "BSECAPITALGOODS",
    "CARBONEX": "BSECARBONEX",
    "CD": "BSECONSUMERDURABLES",
    "CPSE": "BSECPSE",
    "DOL100": "BSEDOLLEX100",
    "DOL200": "BSEDOLLEX200",
    "DOL30": "BSEDOLLEX30",
    "ENERGY": "BSEENERGY",
    "FMC": "BSEFASTMOVINGCONSUMERGOODS",
    "FIN": "BSEFINANCIALSERVICES",
    "GREENEX": "BSEGREENEX",
    "HC": "BSEHEALTHCARE",
    "INFRA": "BSEINDIAINFRASTRUCTUREINDEX",
    "INDSTR": "BSEINDUSTRIALS",
    "IT": "BSEINFORMATIONTECHNOLOGY",
    "IPO": "BSEIPO",
    "LRGCAP": "BSELARGECAP",
    "METAL": "BSEMETAL",
    "MIDCAP": "BSEMIDCAP",
    "MIDSEL": "BSEMIDCAPSELECTINDEX",
    "OILGAS": "BSEOIL&GAS",
    "POWER": "BSEPOWER",
    "PSU": "BSEPSU",
    "REALTY": "BSEREALTY",
    "SMLCAP": "BSESMALLCAP",
    "SMLSEL": "BSESMALLCAPSELECTINDEX",
    "SME IPO": "BSESMEIPO",
    "TECK": "BSETECK",
    "TELCOM": "BSETELECOM",
}


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


def _download_files(output_dir: Path) -> list[Path]:
    """Download every Fyers master-contract file into output_dir."""
    logger.info("Downloading Fyers master contract files")
    downloaded: list[Path] = []
    for key, url in _CSV_URLS.items():
        try:
            response = httpx.get(url, timeout=60.0, follow_redirects=True)
            response.raise_for_status()

            ext = ".json" if url.endswith(".json") else ".csv"
            file_path = output_dir / f"{key}{ext}"
            file_path.write_bytes(response.content)
            downloaded.append(file_path)
            logger.info("Downloaded %s -> %s", key, file_path)
        except Exception as e:
            logger.error("Error downloading %s from %s: %s", key, url, e)
    return downloaded


def _reformat_symbol_detail(s: str) -> str:
    """Format Fyers ``Symbol Details`` field to OpenBull's DDMMMYY-style symbol prefix.

    Input: ``"NIFTY 02 Mar 26 30600"``
    Output: ``"NIFTY02MAR2630600"``
    """
    parts = s.split()
    if len(parts) < 5:
        return s
    return f"{parts[0]}{parts[1]}{parts[2].upper()}{parts[3]}{parts[4]}"


def _process_nse_cm(path: Path) -> pd.DataFrame:
    """NSE Cash Market (equities + indices)."""
    logger.info("Processing Fyers NSE_CM data")
    df = pd.read_csv(path, names=_CSV_HEADERS, dtype=_CSV_DTYPES)

    df["token"] = df["Fytoken"]
    df["name"] = df["Symbol Details"]
    df["expiry"] = df["Expiry date"]
    df["strike"] = df["Strike price"]
    df["lotsize"] = df["Minimum lot size"]
    df["tick_size"] = df["Tick size"]
    df["brsymbol"] = df["Symbol ticker"]

    df.loc[df["Exchange Instrument type"].isin([0, 9]), "exchange"] = "NSE"
    df.loc[df["Exchange Instrument type"].isin([0, 9]), "instrumenttype"] = "EQ"
    df.loc[
        (df["Exchange Instrument type"] == 2) & (df["Symbol ticker"].str.endswith("-GB", na=False)),
        "exchange",
    ] = "NSE"
    df.loc[
        (df["Exchange Instrument type"] == 2) & (df["Symbol ticker"].str.endswith("-GB", na=False)),
        "instrumenttype",
    ] = "GB"
    df.loc[df["Exchange Instrument type"] == 10, "exchange"] = "NSE_INDEX"
    df.loc[df["Exchange Instrument type"] == 10, "instrumenttype"] = "INDEX"

    df = df[df["exchange"].isin(["NSE", "NSE_INDEX"])].copy()
    df.loc[:, "symbol"] = df["Underlying symbol"]

    nse_idx_mask = df["exchange"] == "NSE_INDEX"
    df.loc[nse_idx_mask, "symbol"] = (
        df.loc[nse_idx_mask, "symbol"]
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )
    df.loc[nse_idx_mask, "symbol"] = df.loc[nse_idx_mask, "symbol"].replace(
        {"NIFTYMID50": "NIFTYMIDCAP50"}
    )

    df["brexchange"] = "NSE"
    return _select_schema_columns(df)


def _process_bse_cm(path: Path) -> pd.DataFrame:
    """BSE Cash Market (equities + indices)."""
    logger.info("Processing Fyers BSE_CM data")
    df = pd.read_csv(path, names=_CSV_HEADERS, dtype=_CSV_DTYPES)

    df["token"] = df["Fytoken"]
    df["name"] = df["Symbol Details"]
    df["expiry"] = df["Expiry date"]
    df["strike"] = df["Strike price"]
    df["lotsize"] = df["Minimum lot size"]
    df["tick_size"] = df["Tick size"]
    df["brsymbol"] = df["Symbol ticker"]

    df.loc[df["Exchange Instrument type"].isin([0, 4, 50]), "exchange"] = "BSE"
    df.loc[df["Exchange Instrument type"].isin([0, 4, 50]), "instrumenttype"] = "EQ"
    df.loc[df["Exchange Instrument type"] == 10, "exchange"] = "BSE_INDEX"
    df.loc[df["Exchange Instrument type"] == 10, "instrumenttype"] = "INDEX"

    df = df[df["Exchange Instrument type"].isin([0, 4, 10, 50])].copy()
    df.loc[:, "symbol"] = df["Underlying symbol"]

    bse_idx_mask = df["exchange"] == "BSE_INDEX"
    original_bse = df.loc[bse_idx_mask, "symbol"]
    mapped_bse = original_bse.map(_BSE_INDEX_MAP)
    fallback_bse = original_bse.fillna("").str.upper().str.replace(" ", "", regex=False)
    df.loc[bse_idx_mask, "symbol"] = mapped_bse.fillna(fallback_bse)

    df["brexchange"] = "BSE"
    return _select_schema_columns(df)


def _process_fo_csv(path: Path, br_exchange: str) -> pd.DataFrame:
    """NSE_FO / BSE_FO derivatives CSV."""
    logger.info("Processing Fyers %s data", br_exchange)
    df = pd.read_csv(path, names=_CSV_HEADERS, dtype=_CSV_DTYPES)

    df["token"] = df["Fytoken"]
    df["name"] = df["Symbol Details"]

    # Convert epoch-seconds expiry to DD-MMM-YY uppercase
    df["expiry"] = pd.to_datetime(
        pd.to_numeric(df["Expiry date"], errors="coerce"), unit="s",
    )
    df["expiry"] = df["expiry"].dt.strftime("%d-%b-%y").str.upper()

    df["strike"] = df["Strike price"]
    df["lotsize"] = df["Minimum lot size"]
    df["tick_size"] = df["Tick size"]
    df["brsymbol"] = df["Symbol ticker"]
    df["brexchange"] = br_exchange
    df["exchange"] = br_exchange

    # Fyers Option type:
    #   "XX" -> futures (FUT)
    #   "CE" / "PE" -> options
    #   NaN  -> default to FUT (BSE_FO sometimes leaves it blank for futures)
    df["instrumenttype"] = df["Option type"].fillna("FUT").replace({"XX": "FUT"})

    fut_mask = (df["Option type"] == "XX") | df["Option type"].isna()
    df.loc[fut_mask, "symbol"] = df.loc[fut_mask, "Symbol Details"].apply(
        lambda x: _reformat_symbol_detail(x) if pd.notnull(x) else x
    )
    for opt in ("CE", "PE"):
        mask = df["Option type"] == opt
        df.loc[mask, "symbol"] = df.loc[mask, "Symbol Details"].apply(
            lambda x: _reformat_symbol_detail(x) if pd.notnull(x) else x
        ) + opt

    return _select_schema_columns(df)


def _process_json_master(path: Path, br_exchange: str) -> pd.DataFrame:
    """CDS / MCX JSON master files. Uses ``qtyMultiplier`` for accurate lot sizes."""
    logger.info("Processing Fyers %s JSON data", br_exchange)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    df = pd.DataFrame(list(data.values()))

    df["token"] = df["fyToken"]
    df["name"] = df.get("symbolDetails", df.get("symDetails", ""))

    df["expiry"] = pd.to_datetime(
        pd.to_numeric(df["expiryDate"], errors="coerce"), unit="s",
    )
    df["expiry"] = df["expiry"].dt.strftime("%d-%b-%y").str.upper()

    df["strike"] = df["strikePrice"]
    df["lotsize"] = df["qtyMultiplier"].astype("Int64")
    df["tick_size"] = df["tickSize"]
    df["brsymbol"] = df["symTicker"]
    df["brexchange"] = br_exchange
    df["exchange"] = br_exchange

    df["instrumenttype"] = df["optType"].replace({"XX": "FUT"})

    fut_mask = df["optType"] == "XX"
    df.loc[fut_mask, "symbol"] = df.loc[fut_mask, "symDetails"].apply(
        lambda x: _reformat_symbol_detail(x) if pd.notnull(x) else x
    )
    for opt in ("CE", "PE"):
        mask = df["optType"] == opt
        df.loc[mask, "symbol"] = df.loc[mask, "symDetails"].apply(
            lambda x: _reformat_symbol_detail(x) if pd.notnull(x) else x
        ) + opt

    return _select_schema_columns(df)


_TARGET_COLUMNS = [
    "symbol", "brsymbol", "name", "exchange", "brexchange",
    "token", "expiry", "strike", "lotsize", "instrumenttype", "tick_size",
]


def _select_schema_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with only the columns required by the symtoken schema."""
    for col in _TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[_TARGET_COLUMNS].copy()


def _cleanup_temp(paths: list[Path]):
    for p in paths:
        try:
            if p.exists():
                p.unlink()
                logger.info("Deleted temporary file %s", p)
        except Exception as e:
            logger.error("Error deleting temp file %s: %s", p, e)


def master_contract_download(auth_token: str | None = None) -> dict:
    """Download and process Fyers master contracts into the symtoken table.

    The Fyers master files are publicly hosted; ``auth_token`` is accepted for
    contract parity but is not used here.
    """
    downloaded: list[Path] = []
    try:
        downloaded = _download_files(TMP_DIR)
        if not downloaded:
            return {"status": "error", "message": "No Fyers master files were downloaded"}

        frames: list[pd.DataFrame] = []
        for path in downloaded:
            try:
                if path.name == "NSE_CM.csv":
                    frames.append(_process_nse_cm(path))
                elif path.name == "BSE_CM.csv":
                    frames.append(_process_bse_cm(path))
                elif path.name == "NSE_FO.csv":
                    frames.append(_process_fo_csv(path, "NFO"))
                elif path.name == "BSE_FO.csv":
                    frames.append(_process_fo_csv(path, "BFO"))
                elif path.name == "NSE_CD.json":
                    frames.append(_process_json_master(path, "CDS"))
                elif path.name == "MCX_COM.json":
                    frames.append(_process_json_master(path, "MCX"))
                else:
                    logger.warning("Unknown Fyers master file: %s", path.name)
            except Exception as e:
                logger.error("Error processing %s: %s", path.name, e)

        _cleanup_temp(downloaded)

        if not frames:
            return {"status": "error", "message": "No Fyers master frames were processed"}

        token_df = pd.concat(frames, ignore_index=True)
        # Drop rows with no symbol or no token (incomplete records)
        token_df = token_df.dropna(subset=["symbol", "token"]).copy()

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
                                # Coerce pandas Int64 NA to None
                                elif v is pd.NA:
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

        logger.info("Fyers master contract download completed successfully")
        return {
            "status": "success",
            "message": "Fyers master contracts downloaded",
            "count": len(token_df),
        }

    except Exception as e:
        logger.error("Fyers master contract download failed: %s", e)
        if downloaded:
            _cleanup_temp(downloaded)
        return {"status": "error", "message": str(e)}


async def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken for symbols matching the query on the given exchange.

    The query is split on whitespace into tokens; every token must appear
    (case-insensitive, anywhere) in one of ``symbol``, ``brsymbol``, or
    ``name``. Returns up to 50 rows ordered by prefix-match then length.
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
