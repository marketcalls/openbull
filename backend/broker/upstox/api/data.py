"""
Upstox market data API - quotes, depth, historical candles.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from backend.broker.upstox.mapping.order_data import (
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

# Upstox v2 historical candle resolutions
TIMEFRAME_MAP = {
    "1m": "1minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
    "D": "day",
    "W": "week",
    "M": "month",
}

SUPPORTED_INTERVALS = {
    "seconds": [],
    "minutes": ["1m", "5m", "15m", "30m"],
    "hours": ["1h"],
    "days": ["D", "W", "M"],
}


def _headers(auth_token: str) -> dict:
    return {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/json",
    }


def _encode_key(instrument_key: str) -> str:
    return quote(instrument_key, safe="")


def get_quotes(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get LTP/OHLC quotes for a single symbol using v2 full quote endpoint."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    client = get_httpx_client()
    encoded = _encode_key(token)

    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        raise ValueError(data.get("message", "Failed to fetch quotes"))

    quote_data = None
    for key, val in data["data"].items():
        quote_data = val
        break

    if not quote_data:
        raise ValueError("No quote data in response")

    ohlc = quote_data.get("ohlc", {})
    return {
        "ltp": quote_data.get("last_price", 0),
        "open": ohlc.get("open", 0),
        "high": ohlc.get("high", 0),
        "low": ohlc.get("low", 0),
        "close": ohlc.get("close", 0),
        "prev_close": quote_data.get("prev_close", ohlc.get("close", 0)),
        "volume": quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
    }


def get_multi_quotes(symbols_list: list[dict], auth_token: str, config: dict | None = None) -> list[dict]:
    """Get quotes for multiple symbols using v2 full quote endpoint."""
    keys_map = {}  # token -> {symbol, exchange}
    encoded_keys = []
    for item in symbols_list:
        sym, exch = item["symbol"], item["exchange"]
        token = get_token_from_cache(sym, exch)
        if token:
            keys_map[token] = {"symbol": sym, "exchange": exch}
            encoded_keys.append(_encode_key(token))

    if not encoded_keys:
        return []

    client = get_httpx_client()
    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={','.join(encoded_keys)}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        return []

    results = []
    for response_key, quote_data in data["data"].items():
        info = keys_map.get(response_key)
        if not info:
            for orig_token, orig_info in keys_map.items():
                if orig_token in response_key or response_key in orig_token:
                    info = orig_info
                    break
        if not info:
            continue

        ohlc = quote_data.get("ohlc", {})
        results.append({
            "symbol": info["symbol"],
            "exchange": info["exchange"],
            "ltp": quote_data.get("last_price", 0),
            "open": ohlc.get("open", 0),
            "high": ohlc.get("high", 0),
            "low": ohlc.get("low", 0),
            "close": ohlc.get("close", 0),
            "prev_close": quote_data.get("prev_close", ohlc.get("close", 0)),
            "volume": quote_data.get("volume", 0),
            "oi": quote_data.get("oi", 0),
        })

    return results


def get_market_depth(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get 5-level market depth for a symbol."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    client = get_httpx_client()
    encoded = _encode_key(token)

    url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={encoded}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        raise ValueError(data.get("message", "Failed to fetch market depth"))

    quote_data = None
    for key, val in data["data"].items():
        quote_data = val
        break

    if not quote_data:
        raise ValueError("No depth data in response")

    depth = quote_data.get("depth", {})
    ohlc = quote_data.get("ohlc", {})

    bids = [
        {"price": b.get("price", 0), "quantity": b.get("quantity", 0), "orders": b.get("orders", 0)}
        for b in depth.get("buy", [])
    ]
    asks = [
        {"price": a.get("price", 0), "quantity": a.get("quantity", 0), "orders": a.get("orders", 0)}
        for a in depth.get("sell", [])
    ]

    return {
        "bids": bids,
        "asks": asks,
        "ltp": quote_data.get("last_price", 0),
        "open": ohlc.get("open", 0),
        "high": ohlc.get("high", 0),
        "low": ohlc.get("low", 0),
        "close": ohlc.get("close", 0),
        "prev_close": quote_data.get("prev_close", ohlc.get("close", 0)),
        "volume": quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
        "totalbuyqty": quote_data.get("total_buy_quantity", 0),
        "totalsellqty": quote_data.get("total_sell_quantity", 0),
    }


def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get historical OHLCV candles with automatic date chunking."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")

    if interval not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP.keys())}")

    resolution = TIMEFRAME_MAP[interval]
    encoded = _encode_key(token)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Chunk size based on interval type
    if resolution in ("1minute", "5minute", "15minute", "30minute", "60minute"):
        chunk_days = 30
    else:
        chunk_days = 365

    client = get_httpx_client()
    all_candles = []

    current_start = start_dt
    while current_start <= end_dt:
        current_end = min(current_start + timedelta(days=chunk_days), end_dt)

        url = (
            f"https://api.upstox.com/v2/historical-candle/{encoded}"
            f"/{resolution}/{current_end.isoformat()}/{current_start.isoformat()}"
        )

        try:
            response = client.get(url, headers=_headers(auth_token))
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "success" and data.get("data", {}).get("candles"):
                for candle in data["data"]["candles"]:
                    # candle format: [timestamp_str, open, high, low, close, volume, oi]
                    if len(candle) >= 6:
                        ts = candle[0]
                        if isinstance(ts, str):
                            try:
                                ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
                            except (ValueError, TypeError):
                                ts = 0

                        all_candles.append({
                            "timestamp": ts,
                            "open": candle[1],
                            "high": candle[2],
                            "low": candle[3],
                            "close": candle[4],
                            "volume": candle[5] if len(candle) > 5 else 0,
                            "oi": candle[6] if len(candle) > 6 else 0,
                        })
        except Exception as e:
            logger.error("Error fetching history chunk %s to %s: %s", current_start, current_end, e)

        current_start = current_end + timedelta(days=1)

    # Sort by timestamp ascending and deduplicate
    seen = set()
    unique_candles = []
    for c in sorted(all_candles, key=lambda x: x["timestamp"]):
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique_candles.append(c)

    return unique_candles
