"""
Zerodha master contract download and symbol table population.

Runs in a background thread. Uses asyncio.run() with a dedicated async engine
(separate from the main app's engine) to avoid event loop conflicts.
"""

import asyncio
import io
import logging
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
TMP_DIR.mkdir(exist_ok=True)


def _create_async_session():
    """Create a fresh async engine+session for use in background threads."""
    from backend.config import get_settings
    engine = create_async_engine(get_settings().database_url, echo=False)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _download_csv(auth_token: str, output_path: str) -> pd.DataFrame:
    """Download instruments CSV from Zerodha using the auth token."""
    from backend.utils.httpx_client import get_httpx_client

    logger.info("Downloading Zerodha instruments CSV")
    client = get_httpx_client()
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth_token}",
    }
    response = client.get("https://api.kite.trade/instruments", headers=headers)
    response.raise_for_status()

    csv_string = response.text
    df = pd.read_csv(io.StringIO(csv_string))
    if output_path:
        df.to_csv(output_path, index=False)
    return df


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


def _process_csv(path: str) -> pd.DataFrame:
    """Process Zerodha CSV into DataFrame matching symtoken schema."""
    logger.info("Processing Zerodha CSV data")
    df = pd.read_csv(path)

    exchange_map = {
        "NSE": "NSE",
        "NFO": "NFO",
        "CDS": "CDS",
        "BSE": "BSE",
        "BFO": "BFO",
        "BCD": "BCD",
        "MCX": "MCX",
    }
    df["exchange"] = df["exchange"].map(exchange_map)

    # Drop rows with unmapped exchanges (e.g. GIFT NIFTY)
    df = df.dropna(subset=["exchange"])

    # Index segment handling
    df.loc[(df["segment"] == "INDICES") & (df["exchange"] == "NSE"), "exchange"] = "NSE_INDEX"
    df.loc[(df["segment"] == "INDICES") & (df["exchange"] == "BSE"), "exchange"] = "BSE_INDEX"
    df.loc[(df["segment"] == "INDICES") & (df["exchange"] == "MCX"), "exchange"] = "MCX_INDEX"
    df.loc[(df["segment"] == "INDICES") & (df["exchange"] == "CDS"), "exchange"] = "CDS_INDEX"

    # Format expiry date
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.strftime("%d-%b-%y").str.upper()

    # Combine tokens
    df["token"] = df["instrument_token"].astype(str) + "::::" + df["exchange_token"].astype(str)

    # Select and rename columns
    df = df[
        [
            "token",
            "tradingsymbol",
            "name",
            "expiry",
            "strike",
            "lot_size",
            "instrument_type",
            "exchange",
            "tick_size",
        ]
    ].rename(
        columns={
            "tradingsymbol": "symbol",
            "lot_size": "lotsize",
            "instrument_type": "instrumenttype",
        }
    )

    df["brsymbol"] = df["symbol"]
    df["symbol"] = df.apply(_reformat_symbol, axis=1)
    df["brexchange"] = df["exchange"]

    # Fill NaN expiry
    df["expiry"] = df["expiry"].fillna("")

    def format_strike(strike):
        val = float(strike)
        return str(int(val)) if val == int(val) else str(val)

    # Futures symbol update
    df.loc[df["instrumenttype"] == "FUT", "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )

    # Options symbol update
    for opt_type in ("CE", "PE"):
        mask = df["instrumenttype"] == opt_type
        df.loc[mask, "symbol"] = (
            df["name"]
            + df["expiry"].str.replace("-", "", regex=False)
            + df["strike"].apply(format_strike)
            + df["instrumenttype"]
        )

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


def _cleanup_temp(output_path: str):
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
            logger.info("Deleted temporary file %s", output_path)
    except Exception as e:
        logger.error("Error deleting temp file %s: %s", output_path, e)


def master_contract_download(auth_token: str | None = None) -> dict:
    """Download and process Zerodha master contracts into the symtoken table."""
    output_path = str(TMP_DIR / "zerodha.csv")

    try:
        if not auth_token:
            return {"status": "error", "message": "Zerodha requires auth_token for instrument download"}

        _download_csv(auth_token, output_path)
        token_df = _process_csv(output_path)
        _cleanup_temp(output_path)

        async def _db_ops():
            session_factory = _create_async_session()
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

        asyncio.run(_db_ops())

        logger.info("Zerodha master contract download completed successfully")
        return {"status": "success", "message": "Zerodha master contracts downloaded", "count": len(token_df)}

    except Exception as e:
        logger.error("Zerodha master contract download failed: %s", e)
        _cleanup_temp(output_path)
        return {"status": "error", "message": str(e)}


def search_symbols(symbol: str, exchange: str) -> list[dict]:
    """Search symtoken table for symbols matching the query."""
    async def _search():
        session_factory = _create_async_session()
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT symbol, brsymbol, name, exchange, brexchange, token, "
                    "expiry, strike, lotsize, instrumenttype, tick_size "
                    "FROM symtoken WHERE symbol ILIKE :pattern AND exchange = :exchange LIMIT 50"
                ),
                {"pattern": f"%{symbol}%", "exchange": exchange},
            )
            return [
                {
                    "symbol": r[0], "brsymbol": r[1], "name": r[2], "exchange": r[3],
                    "brexchange": r[4], "token": r[5], "expiry": r[6], "strike": r[7],
                    "lotsize": r[8], "instrumenttype": r[9], "tick_size": r[10],
                }
                for r in result.fetchall()
            ]
    return asyncio.run(_search())
