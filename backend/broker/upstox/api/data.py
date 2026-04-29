"""
Upstox market data API - quotes, depth, historical candles.

Historical fetch follows the openalgo Upstox v3 pattern: tries
/v3/historical-candle/intraday for current-day (minutes/hours) chunks first,
then /v3/historical-candle for older ranges, and synthesizes today's daily
candle from the quotes API when "D" interval includes today.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd

from backend.broker.upstox.mapping.order_data import (
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

# Map standard intervals to Upstox v3 (unit, interval) pairs.
TIMEFRAME_MAP_V3 = {
    "1m": ("minutes", "1"),
    "2m": ("minutes", "2"),
    "3m": ("minutes", "3"),
    "5m": ("minutes", "5"),
    "10m": ("minutes", "10"),
    "15m": ("minutes", "15"),
    "30m": ("minutes", "30"),
    "60m": ("minutes", "60"),
    "1h": ("hours", "1"),
    "2h": ("hours", "2"),
    "3h": ("hours", "3"),
    "4h": ("hours", "4"),
    "D": ("days", "1"),
    "W": ("weeks", "1"),
    "M": ("months", "1"),
}

# Public TIMEFRAME_MAP - keys are the validated set in history_service. Values
# are kept (string form) for backwards-compat with anything that introspects
# the v2-style resolution name.
TIMEFRAME_MAP = {
    "1m": "1minute",
    "2m": "2minute",
    "3m": "3minute",
    "5m": "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "60minute",
    "1h": "60minute",
    "2h": "2hour",
    "3h": "3hour",
    "4h": "4hour",
    "D": "day",
    "W": "week",
    "M": "month",
}

SUPPORTED_INTERVALS = {
    "seconds": [],
    "minutes": ["1m", "2m", "3m", "5m", "10m", "15m", "30m", "60m"],
    "hours": ["1h", "2h", "3h", "4h"],
    "days": ["D", "W", "M"],
}

# Per-(unit, interval_value) chunk size — Upstox v3 documented limits.
_CHUNK_LIMITS = {
    ("minutes", 1): 30,
    ("minutes", 2): 30,
    ("minutes", 3): 30,
    ("minutes", 5): 30,
    ("minutes", 10): 30,
    ("minutes", 15): 30,
    ("minutes", 30): 90,
    ("minutes", 60): 90,
    ("hours", 1): 90,
    ("hours", 2): 90,
    ("hours", 3): 90,
    ("hours", 4): 90,
    ("days", 1): 3650,
    ("weeks", 1): 7300,
    ("months", 1): 7300,
}


def _headers(auth_token: str) -> dict:
    return {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/json",
    }


def _encode_key(instrument_key: str) -> str:
    return quote(instrument_key, safe="")


def _fetch_v3_ohlc(encoded_keys: str, auth_token: str) -> dict:
    # v2 /market-quote/quotes returns *previous* day's OHLC under the `ohlc`
    # field, not today's — the field name is misleading. v3 OHLC supplies the
    # current session under `live_ohlc` and yesterday under `prev_ohlc`. We
    # call v3 alongside v2 to get correct today-OHLC values; v2 is still
    # needed for depth/bid/ask/oi which v3 does not return.
    client = get_httpx_client()
    url = f"https://api.upstox.com/v3/market-quote/ohlc?instrument_key={encoded_keys}&interval=1d"
    try:
        resp = client.get(url, headers=_headers(auth_token))
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return {}
        out: dict = {}
        for value in (data.get("data") or {}).values():
            if not value:
                continue
            inst_key = value.get("instrument_token")
            if inst_key:
                out[inst_key] = {
                    "live_ohlc": value.get("live_ohlc") or {},
                    "prev_ohlc": value.get("prev_ohlc") or {},
                    "last_price": value.get("last_price"),
                }
        return out
    except Exception as e:
        logger.debug("v3 OHLC fetch failed: %s", e)
        return {}


def get_quotes(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get LTP/OHLC quotes for a single symbol (v3 OHLC + v2 depth)."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    client = get_httpx_client()
    encoded = _encode_key(token)

    v3_map = _fetch_v3_ohlc(encoded, auth_token)
    v3_entry = v3_map.get(token, {})
    live_ohlc = v3_entry.get("live_ohlc", {})
    prev_ohlc = v3_entry.get("prev_ohlc", {})

    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        raise ValueError(data.get("message", "Failed to fetch quotes"))

    # Upstox sometimes returns the symbol key with a null payload (unknown /
    # expired / illiquid instrument). Skip null entries when picking the first
    # value so we don't AttributeError on `.get()` below.
    quote_data = next(
        (val for val in data["data"].values() if val is not None),
        None,
    )

    if not quote_data:
        raise ValueError("No quote data in response")

    ohlc = quote_data.get("ohlc") or {}
    return {
        "ltp": v3_entry.get("last_price") or quote_data.get("last_price", 0),
        "open": live_ohlc.get("open") or ohlc.get("open", 0),
        "high": live_ohlc.get("high") or ohlc.get("high", 0),
        "low": live_ohlc.get("low") or ohlc.get("low", 0),
        "close": ohlc.get("close", 0),
        "prev_close": (
            quote_data.get("prev_close")
            or ohlc.get("close")
            or prev_ohlc.get("close", 0)
        ),
        "volume": live_ohlc.get("volume") or quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
    }


def get_multi_quotes(symbols_list: list[dict], auth_token: str, config: dict | None = None) -> list[dict]:
    """Get quotes for multiple symbols using v2 full quote endpoint."""
    keys_map = {}  # token -> {symbol, exchange}
    suffix_map = {}  # numeric token suffix (e.g. "72277") -> {symbol, exchange}
    encoded_keys = []
    for item in symbols_list:
        sym, exch = item["symbol"], item["exchange"]
        token = get_token_from_cache(sym, exch)
        if token:
            keys_map[token] = {"symbol": sym, "exchange": exch}
            if "|" in token:
                suffix_map[token.split("|", 1)[1]] = {"symbol": sym, "exchange": exch}
            encoded_keys.append(_encode_key(token))

    if not encoded_keys:
        return []

    joined_keys = ",".join(encoded_keys)
    v3_map = _fetch_v3_ohlc(joined_keys, auth_token)

    client = get_httpx_client()
    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={joined_keys}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        return []

    results = []
    for response_key, quote_data in data["data"].items():
        # Upstox returns null for unknown/expired/illiquid instrument keys.
        # Skip those silently — without this guard the .get() calls below
        # throw "'NoneType' object has no attribute 'get'" and the whole
        # batch fails, zeroing every leg's quote.
        if not quote_data:
            continue
        info = keys_map.get(response_key)
        if not info:
            # Upstox returns keys like "NSE_FO:Nifty 50" — try matching by the
            # broker-provided instrument_token field on the quote payload.
            it = quote_data.get("instrument_token")
            if it:
                info = keys_map.get(it)
                if not info and "|" in it:
                    info = suffix_map.get(it.split("|", 1)[1])
        if not info:
            # Last resort: substring match against the response key
            for orig_token, orig_info in keys_map.items():
                if orig_token in response_key or response_key in orig_token:
                    info = orig_info
                    break
        if not info:
            continue

        # `or {}` covers both missing-key (default kicks in) and explicit-None
        # value (default is skipped — Upstox sometimes returns "ohlc": null
        # for illiquid contracts, which would otherwise AttributeError below).
        ohlc = quote_data.get("ohlc") or {}
        depth = quote_data.get("depth") or {}
        bids = depth.get("buy") or []
        asks = depth.get("sell") or []
        top_bid = bids[0] if bids else {}
        top_ask = asks[0] if asks else {}

        v3_entry = v3_map.get(quote_data.get("instrument_token") or "", {})
        live_ohlc = v3_entry.get("live_ohlc", {})
        prev_ohlc = v3_entry.get("prev_ohlc", {})

        results.append({
            "symbol": info["symbol"],
            "exchange": info["exchange"],
            "ltp": v3_entry.get("last_price") or quote_data.get("last_price", 0),
            "open": live_ohlc.get("open") or ohlc.get("open", 0),
            "high": live_ohlc.get("high") or ohlc.get("high", 0),
            "low": live_ohlc.get("low") or ohlc.get("low", 0),
            "close": ohlc.get("close", 0),
            "prev_close": (
                quote_data.get("prev_close")
                or ohlc.get("close")
                or prev_ohlc.get("close", 0)
            ),
            "volume": live_ohlc.get("volume") or quote_data.get("volume", 0),
            "oi": quote_data.get("oi", 0),
            "bid": top_bid.get("price", 0),
            "ask": top_ask.get("price", 0),
            "bid_qty": top_bid.get("quantity", 0),
            "ask_qty": top_ask.get("quantity", 0),
        })

    return results


def get_market_depth(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get 5-level market depth for a symbol (v3 OHLC + v2 depth)."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    client = get_httpx_client()
    encoded = _encode_key(token)

    v3_map = _fetch_v3_ohlc(encoded, auth_token)
    v3_entry = v3_map.get(token, {})
    live_ohlc = v3_entry.get("live_ohlc", {})
    prev_ohlc = v3_entry.get("prev_ohlc", {})

    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        raise ValueError(data.get("message", "Failed to fetch market depth"))

    quote_data = next(
        (val for val in data["data"].values() if val is not None),
        None,
    )

    if not quote_data:
        raise ValueError("No depth data in response")

    depth = quote_data.get("depth") or {}
    ohlc = quote_data.get("ohlc") or {}

    bids = [
        {"price": b.get("price", 0), "quantity": b.get("quantity", 0), "orders": b.get("orders", 0)}
        for b in (depth.get("buy") or [])
        if b is not None
    ]
    asks = [
        {"price": a.get("price", 0), "quantity": a.get("quantity", 0), "orders": a.get("orders", 0)}
        for a in (depth.get("sell") or [])
        if a is not None
    ]

    return {
        "bids": bids,
        "asks": asks,
        "ltp": v3_entry.get("last_price") or quote_data.get("last_price", 0),
        "open": live_ohlc.get("open") or ohlc.get("open", 0),
        "high": live_ohlc.get("high") or ohlc.get("high", 0),
        "low": live_ohlc.get("low") or ohlc.get("low", 0),
        "close": ohlc.get("close", 0),
        "prev_close": (
            quote_data.get("prev_close")
            or ohlc.get("close")
            or prev_ohlc.get("close", 0)
        ),
        "volume": live_ohlc.get("volume") or quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
        "totalbuyqty": quote_data.get("total_buy_quantity", 0),
        "totalsellqty": quote_data.get("total_sell_quantity", 0),
    }


def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get historical OHLCV candles with automatic date chunking (v3 endpoints).

    Mirrors the openalgo Upstox v3 pattern: tries the intraday endpoint first
    for current-day chunks (minutes/hours), then the historical endpoint, and
    synthesizes today's daily candle from the quotes API for D when today is
    in range.
    """
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    if interval not in TIMEFRAME_MAP_V3:
        raise ValueError(f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP_V3.keys())}")

    unit, interval_value = TIMEFRAME_MAP_V3[interval]
    interval_int = int(interval_value)
    encoded = _encode_key(token)

    from_dt = pd.to_datetime(start_date)
    to_dt = pd.to_datetime(end_date)

    chunk_days = _CHUNK_LIMITS.get((unit, interval_int), 30)

    dfs: list[pd.DataFrame] = []
    current_start = from_dt
    chunk_count = 0
    successful_chunks = 0
    while current_start <= to_dt:
        chunk_count += 1
        current_end = min(current_start + timedelta(days=chunk_days - 1), to_dt)
        try:
            chunk_df = _fetch_chunk_data(
                encoded, unit, interval_int,
                current_start, current_end,
                symbol, exchange, interval,
                auth_token, config,
            )
            if not chunk_df.empty:
                dfs.append(chunk_df)
                successful_chunks += 1
        except Exception as e:
            logger.error("Error fetching history chunk %s to %s: %s", current_start.date(), current_end.date(), e)
        current_start = current_end + timedelta(days=1)

    logger.info("Upstox history chunks: %d/%d successful for %s/%s/%s", successful_chunks, chunk_count, symbol, exchange, interval)

    if not dfs:
        return []

    df = pd.concat(dfs, ignore_index=True)
    df = (
        df.drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # Convert pandas DataFrame back to openbull's list[dict] surface.
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append({
            "timestamp": int(row["timestamp"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]) if row["volume"] is not None else 0,
            "oi": int(row["oi"]) if row.get("oi") is not None else 0,
        })
    return out


def _fetch_chunk_data(
    encoded: str,
    unit: str,
    interval_value: int,
    start_dt: datetime,
    end_dt: datetime,
    symbol: str,
    exchange: str,
    interval: str,
    auth_token: str,
    config: dict | None,
) -> pd.DataFrame:
    """Fetch a single chunk via v3 endpoints (intraday + historical merge)."""
    client = get_httpx_client()
    headers = _headers(auth_token)

    from_date_str = start_dt.strftime("%Y-%m-%d")
    to_date_str = end_dt.strftime("%Y-%m-%d")

    today = datetime.now().date()
    all_candles: list = []

    # Intraday endpoint first (current day, minutes/hours only).
    if unit in ("minutes", "hours") and end_dt.date() == today:
        intraday_url = (
            f"https://api.upstox.com/v3/historical-candle/intraday/"
            f"{encoded}/{unit}/{interval_value}"
        )
        try:
            resp = client.get(intraday_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                intraday_candles = (data.get("data") or {}).get("candles") or []
                filtered = _filter_candles_by_date(intraday_candles, start_dt, end_dt)
                all_candles.extend(filtered)
        except Exception as e:
            logger.debug("Intraday endpoint failed for %s: %s", encoded, e)

    # Historical endpoint for older data (and as a fallback if intraday empty).
    if not all_candles or start_dt.date() < today:
        hist_url = (
            f"https://api.upstox.com/v3/historical-candle/"
            f"{encoded}/{unit}/{interval_value}/{to_date_str}/{from_date_str}"
        )
        try:
            resp = client.get(hist_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                hist_candles = (data.get("data") or {}).get("candles") or []
                all_candles.extend(hist_candles)
        except Exception as e:
            logger.debug("Historical endpoint failed for %s %s..%s: %s", encoded, from_date_str, to_date_str, e)

    # Daily timeframe — synthesize today's candle from quotes if missing.
    if unit == "days" and interval == "D":
        if start_dt.date() <= today <= end_dt.date():
            today_present = False
            for c in all_candles:
                try:
                    ts = c[0]
                    cdate = (
                        pd.to_datetime(ts, unit="ms" if isinstance(ts, (int, float)) else None)
                    ).date()
                    if cdate == today:
                        today_present = True
                        break
                except Exception:
                    continue
            if not today_present:
                try:
                    q = get_quotes(symbol, exchange, auth_token, config)
                    if q and q.get("ltp", 0) > 0:
                        is_stale = False
                        if all_candles:
                            last = max(all_candles, key=lambda x: x[0])
                            quotes_open = q.get("open", q.get("ltp", 0))
                            quotes_high = q.get("high", q.get("ltp", 0))
                            quotes_low = q.get("low", q.get("ltp", 0))
                            quotes_close = q.get("ltp", 0)
                            quotes_volume = q.get("volume", 0)
                            if (
                                last[1] == quotes_open and last[2] == quotes_high
                                and last[3] == quotes_low and last[4] == quotes_close
                                and last[5] == quotes_volume
                            ):
                                is_stale = True
                                logger.warning(
                                    "Quotes data appears stale (matches last candle); skipping today's synthesized D candle for %s/%s",
                                    symbol, exchange,
                                )
                        if not is_stale:
                            today_ts_ms = int(
                                (
                                    datetime.combine(today, datetime.min.time())
                                    + timedelta(hours=5, minutes=30)
                                ).timestamp()
                            ) * 1000
                            all_candles.append([
                                today_ts_ms,
                                q.get("open", q.get("ltp", 0)),
                                q.get("high", q.get("ltp", 0)),
                                q.get("low", q.get("ltp", 0)),
                                q.get("ltp", 0),
                                q.get("volume", 0),
                                q.get("oi", 0),
                            ])
                except Exception as e:
                    logger.debug("Could not synthesize today's D candle from quotes: %s", e)

    if not all_candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    df = pd.DataFrame(
        all_candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
    )

    # Normalize timestamp → Unix seconds (int).
    def _safe_to_dt(ts):
        try:
            if isinstance(ts, str):
                return pd.to_datetime(ts)
            if isinstance(ts, pd.Timestamp):
                return ts
            return pd.to_datetime(ts, unit="ms")
        except Exception:
            return pd.NaT

    df["timestamp"] = df["timestamp"].apply(_safe_to_dt)
    df = df.dropna(subset=["timestamp"])

    if interval == "D":
        df["timestamp"] = df["timestamp"].apply(
            lambda x: x.date() if hasattr(x, "date") else pd.to_datetime(x).date()
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    df["timestamp"] = df["timestamp"].apply(
        lambda x: int(x.timestamp())
        if hasattr(x, "timestamp")
        else int(pd.to_datetime(x).timestamp())
    )

    numeric_cols = ["open", "high", "low", "close", "volume", "oi"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

    return df[["timestamp", "open", "high", "low", "close", "volume", "oi"]]


def _filter_candles_by_date(
    candles: list, start_dt: datetime, end_dt: datetime,
) -> list:
    """Filter raw candles to those within [start_dt, end_dt + 1 day) by ms ts."""
    if not candles:
        return []

    start_ms = start_dt.timestamp() * 1000
    end_ms = (end_dt + timedelta(days=1)).timestamp() * 1000

    out: list = []
    for candle in candles:
        ts = candle[0]
        if isinstance(ts, str):
            try:
                ts = pd.to_datetime(ts).timestamp() * 1000
            except Exception:
                continue
        elif isinstance(ts, (int, float)):
            if ts < 1e12:  # likely seconds; promote to ms
                ts = ts * 1000
        else:
            continue
        if start_ms <= ts < end_ms:
            new_candle = list(candle)
            new_candle[0] = ts
            out.append(new_candle)
    return out
