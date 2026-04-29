"""
Fyers market data API - quotes, multi-quotes, depth, historical candles.

The auth_token is the combined ``"api_key:access_token"`` string used as
the value of the Fyers ``Authorization`` header.
"""

import logging
import time
import urllib.parse
from datetime import datetime, timedelta

import httpx
import pandas as pd

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


# Public TIMEFRAME_MAP — maps openbull intervals to Fyers resolutions.
TIMEFRAME_MAP: dict = {
    # Seconds
    "5s": "5S",
    "10s": "10S",
    "15s": "15S",
    "30s": "30S",
    "45s": "45S",
    # Minutes
    "1m": "1",
    "2m": "2",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "20m": "20",
    "30m": "30",
    # Hours
    "1h": "60",
    "2h": "120",
    "4h": "240",
    # Daily
    "D": "1D",
}

SUPPORTED_INTERVALS = {
    "seconds": ["5s", "10s", "15s", "30s", "45s"],
    "minutes": ["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m"],
    "hours": ["1h", "2h", "4h"],
    "days": ["D"],
}

# Exchanges where Fyers exposes Open Interest in /data/depth.
_FNO_EXCHANGES = {"NFO", "BFO", "MCX", "CDS"}

# /data/depth pacing — Fyers caps depth at 10 req/sec.
_DEPTH_MIN_GAP_SECONDS = 0.1

# /data/quotes batching — bulk endpoint accepts up to 50 symbols per call.
_QUOTE_BATCH_SIZE = 50
_QUOTE_BATCH_DELAY = 0.1


def _headers(auth_token: str) -> dict:
    return {
        "Authorization": auth_token,
        "Content-Type": "application/json",
    }


def _br_symbol(symbol: str, exchange: str) -> str:
    """Resolve OpenBull symbol -> Fyers broker symbol; fall back to raw."""
    return get_brsymbol_from_cache(symbol, exchange) or symbol


def _api_get(endpoint: str, auth_token: str) -> dict:
    """GET ``endpoint`` on Fyers and return parsed JSON."""
    client = get_httpx_client()
    url = f"https://api-t1.fyers.in{endpoint}"
    try:
        response = client.get(url, headers=_headers(auth_token))
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error("Fyers HTTP error on %s: %s", endpoint, e.response.text)
        try:
            return e.response.json()
        except Exception:
            return {"s": "error", "message": str(e)}
    except Exception as e:
        logger.exception("Error during Fyers GET %s", endpoint)
        return {"s": "error", "message": str(e)}


def get_quotes(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None,
) -> dict:
    """Get LTP/OHLC quotes (with OI for derivatives) for a single symbol via /data/depth."""
    br_symbol = _br_symbol(symbol, exchange)
    if not br_symbol:
        raise ValueError(f"Symbol not found for {symbol}/{exchange}")

    encoded = urllib.parse.quote(br_symbol)
    response = _api_get(f"/data/depth?symbol={encoded}&ohlcv_flag=1", auth_token)

    if response.get("s") != "ok":
        msg = response.get("message", "Unknown error")
        raise ValueError(f"Fyers API error: {msg}")

    depth_data = response.get("d", {}).get(br_symbol, {})
    if not depth_data:
        raise ValueError(f"No quote data available for {exchange}:{symbol}")

    bids = depth_data.get("bids", [])
    asks = depth_data.get("ask", [])  # Fyers uses singular 'ask'
    bid_price = bids[0].get("price", 0) if bids else 0
    ask_price = asks[0].get("price", 0) if asks else 0

    return {
        "bid": bid_price,
        "ask": ask_price,
        "open": depth_data.get("o", 0),
        "high": depth_data.get("h", 0),
        "low": depth_data.get("l", 0),
        "ltp": depth_data.get("ltp", 0),
        "prev_close": depth_data.get("c", 0),
        "volume": depth_data.get("v", 0),
        "oi": int(depth_data.get("oi", 0) or 0),
    }


def _fetch_oi_for_symbol(br_symbol: str, auth_token: str, last_call_at: list[float]) -> int:
    """Fetch OI for a single derivative symbol via /data/depth, with rate pacing.

    ``last_call_at`` is a 1-element mutable list used so multiple calls inside
    a multi-quote batch share the same pacing clock.
    """
    elapsed = time.monotonic() - last_call_at[0]
    if elapsed < _DEPTH_MIN_GAP_SECONDS:
        time.sleep(_DEPTH_MIN_GAP_SECONDS - elapsed)

    try:
        encoded = urllib.parse.quote(br_symbol)
        response = _api_get(f"/data/depth?symbol={encoded}&ohlcv_flag=1", auth_token)
    finally:
        last_call_at[0] = time.monotonic()

    if response.get("s") != "ok":
        return 0
    depth_data = response.get("d", {}).get(br_symbol, {})
    return int(depth_data.get("oi", 0) or 0)


def _process_quote_batch(
    batch: list[dict], auth_token: str, fetch_oi: bool, last_call_at: list[float],
) -> list[dict]:
    """Fetch a single batch of <= _QUOTE_BATCH_SIZE symbols from /data/quotes."""
    br_symbols: list[str] = []
    symbol_map: dict = {}
    skipped: list[dict] = []

    for item in batch:
        sym, exch = item["symbol"], item["exchange"]
        br = _br_symbol(sym, exch)
        if not br:
            skipped.append({"symbol": sym, "exchange": exch, "error": "Could not resolve broker symbol"})
            continue
        br_symbols.append(br)
        symbol_map[br] = {"symbol": sym, "exchange": exch}

    if not br_symbols:
        return skipped

    symbols_param = ",".join(br_symbols)
    encoded = urllib.parse.quote(symbols_param)
    response = _api_get(f"/data/quotes?symbols={encoded}", auth_token)

    quotes_map: dict = {}
    if response.get("s") == "ok":
        for quote_item in response.get("d", []):
            if quote_item.get("s") == "ok":
                quotes_map[quote_item.get("n", "")] = quote_item.get("v", {})
    else:
        logger.warning("Fyers quotes batch error: %s", response.get("message"))

    results: list[dict] = []
    for br in br_symbols:
        quote = quotes_map.get(br, {})
        if not quote:
            continue

        original = symbol_map.get(br, {"symbol": br, "exchange": "UNKNOWN"})

        oi_value = 0
        if fetch_oi and original["exchange"] in _FNO_EXCHANGES:
            oi_value = _fetch_oi_for_symbol(br, auth_token, last_call_at)

        results.append({
            "symbol": original["symbol"],
            "exchange": original["exchange"],
            "ltp": quote.get("lp", 0),
            "open": quote.get("open_price", 0),
            "high": quote.get("high_price", 0),
            "low": quote.get("low_price", 0),
            "close": quote.get("prev_close_price", 0),
            "prev_close": quote.get("prev_close_price", 0),
            "volume": quote.get("volume", 0),
            "oi": oi_value,
            "bid": quote.get("bid", 0),
            "ask": quote.get("ask", 0),
        })

    return skipped + results


def get_multi_quotes(
    symbols_list: list[dict], auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get quotes for multiple symbols. Auto-batches at 50 symbols/call."""
    if not symbols_list:
        return []

    OI_THRESHOLD = 100
    fetch_oi = len(symbols_list) <= OI_THRESHOLD
    if not fetch_oi:
        logger.info(
            "Fyers multiquote size %d > %d: skipping OI fetch",
            len(symbols_list), OI_THRESHOLD,
        )

    last_call_at = [0.0]
    all_results: list[dict] = []

    for i in range(0, len(symbols_list), _QUOTE_BATCH_SIZE):
        batch = symbols_list[i:i + _QUOTE_BATCH_SIZE]
        all_results.extend(_process_quote_batch(batch, auth_token, fetch_oi, last_call_at))
        if i + _QUOTE_BATCH_SIZE < len(symbols_list):
            time.sleep(_QUOTE_BATCH_DELAY)

    return all_results


def get_market_depth(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None,
) -> dict:
    """Get 5-level market depth for a symbol via /data/depth."""
    br_symbol = _br_symbol(symbol, exchange)
    if not br_symbol:
        raise ValueError(f"Symbol not found for {symbol}/{exchange}")

    encoded = urllib.parse.quote(br_symbol)
    response = _api_get(f"/data/depth?symbol={encoded}&ohlcv_flag=1", auth_token)

    if response.get("s") != "ok":
        raise ValueError(f"Fyers API error: {response.get('message', 'Unknown error')}")

    depth_data = response.get("d", {}).get(br_symbol)
    if not depth_data:
        return {}

    bids = depth_data.get("bids", [])
    asks = depth_data.get("ask", [])

    empty_entry = {"price": 0, "quantity": 0, "orders": 0}
    bids_formatted = [
        {
            "price": b.get("price", 0),
            "quantity": b.get("volume", 0),
            "orders": b.get("ord", 0),
        }
        for b in bids[:5]
    ]
    asks_formatted = [
        {
            "price": a.get("price", 0),
            "quantity": a.get("volume", 0),
            "orders": a.get("ord", 0),
        }
        for a in asks[:5]
    ]

    while len(bids_formatted) < 5:
        bids_formatted.append(empty_entry)
    while len(asks_formatted) < 5:
        asks_formatted.append(empty_entry)

    return {
        "bids": bids_formatted,
        "asks": asks_formatted,
        "totalbuyqty": depth_data.get("totalbuyqty", 0),
        "totalsellqty": depth_data.get("totalsellqty", 0),
        "high": depth_data.get("h", 0),
        "low": depth_data.get("l", 0),
        "ltp": depth_data.get("ltp", 0),
        "ltq": depth_data.get("ltq", 0),
        "open": depth_data.get("o", 0),
        "close": depth_data.get("c", 0),
        "prev_close": depth_data.get("c", 0),
        "volume": depth_data.get("v", 0),
        "oi": int(depth_data.get("oi", 0) or 0),
    }


def _chunk_days_for(resolution: str) -> int:
    """Pick a chunk size for the historical-candle range given a Fyers resolution."""
    if resolution == "1D":
        return 300
    if resolution.endswith("S"):
        return 25  # Seconds data limited to last ~30 trading days
    return 60


def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get historical OHLCV candles with automatic chunking."""
    br_symbol = _br_symbol(symbol, exchange)
    if not br_symbol:
        raise ValueError(f"Symbol not found for {symbol}/{exchange}")

    if interval in ("W", "M"):
        raise ValueError(
            f"Timeframe '{interval}' is not supported by Fyers. "
            "Supported: seconds (5s/10s/15s/30s/45s), minutes (1m-30m), "
            "hours (1h/2h/4h), daily (D)."
        )

    resolution = TIMEFRAME_MAP.get(interval)
    if not resolution:
        raise ValueError(f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP)}")

    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    current_dt = pd.Timestamp.now()

    if end_dt > current_dt:
        end_dt = current_dt
    if start_dt > end_dt:
        raise ValueError(f"Start date {start_dt.date()} cannot be after end date {end_dt.date()}")

    if resolution.endswith("S"):
        max_days_ago = current_dt - pd.Timedelta(days=30)
        if start_dt < max_days_ago:
            logger.warning(
                "Fyers seconds data limited to ~30 trading days. Adjusting start %s -> %s",
                start_dt.date(), max_days_ago.date(),
            )
            start_dt = max_days_ago

    chunk_days = _chunk_days_for(resolution)
    enable_oi = exchange in _FNO_EXCHANGES
    encoded_symbol = urllib.parse.quote(br_symbol)

    dfs: list[pd.DataFrame] = []
    current_start = start_dt
    while current_start <= end_dt:
        current_end = min(current_start + pd.Timedelta(days=chunk_days - 1), end_dt)
        chunk_start = current_start.strftime("%Y-%m-%d")
        chunk_end = current_end.strftime("%Y-%m-%d")

        endpoint = (
            f"/data/history?"
            f"symbol={encoded_symbol}&"
            f"resolution={resolution}&"
            f"date_format=1&"
            f"range_from={chunk_start}&"
            f"range_to={chunk_end}&"
            f"cont_flag=1"
        )
        if enable_oi:
            endpoint += "&oi_flag=1"

        try:
            response = _api_get(endpoint, auth_token)
            if response.get("s") != "ok":
                logger.error(
                    "Fyers history error for %s..%s: %s",
                    chunk_start, chunk_end, response.get("message"),
                )
                current_start = current_end + pd.Timedelta(days=1)
                continue

            candles = response.get("candles", []) or []
            if candles:
                if enable_oi and len(candles[0]) == 7:
                    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
                else:
                    cols = ["timestamp", "open", "high", "low", "close", "volume"]
                df = pd.DataFrame(candles, columns=cols)
                if "oi" not in df.columns:
                    df["oi"] = 0
                dfs.append(df)
        except Exception as e:
            logger.error("Error fetching Fyers history chunk %s..%s: %s", chunk_start, chunk_end, e)

        # Small pause between chunks to be polite to the Fyers backend.
        time.sleep(0.2)
        current_start = current_end + pd.Timedelta(days=1)

    if not dfs:
        return []

    final_df = pd.concat(dfs, ignore_index=True)
    final_df = (
        final_df.drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    out: list[dict] = []
    for _, row in final_df.iterrows():
        out.append({
            "timestamp": int(row["timestamp"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]) if row["volume"] is not None else 0,
            "oi": int(row.get("oi", 0)) if row.get("oi") is not None else 0,
        })
    return out
