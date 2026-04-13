"""
Zerodha market data API - quotes, depth, historical candles.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote as url_quote

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "1m": "minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
    "D": "day",
}

SUPPORTED_INTERVALS = {
    "seconds": [],
    "minutes": ["1m", "5m", "15m", "30m"],
    "hours": ["1h"],
    "days": ["D"],
}


def _headers(auth_token: str) -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth_token}",
        "Accept": "application/json",
    }


def _get_instrument_param(symbol: str, exchange: str) -> str:
    """Build Zerodha quote param: EXCHANGE:BRSYMBOL"""
    brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol
    return f"{exchange}:{brsymbol}"


def _get_instrument_token(symbol: str, exchange: str) -> str:
    """Get the numeric instrument token for history API.
    Zerodha stores tokens as 'instrument_token::::exchange_token' or plain number.
    """
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")
    # Handle composite format
    if "::::" in token:
        return token.split("::::")[0]
    return token


def get_quotes(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get LTP/OHLC quotes for a single symbol."""
    instrument_param = _get_instrument_param(symbol, exchange)

    client = get_httpx_client()
    url = f"https://api.kite.trade/quote?i={instrument_param}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        raise ValueError(data.get("message", "Failed to fetch quotes"))

    # Data is keyed by "EXCHANGE:SYMBOL"
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
        "prev_close": quote_data.get("ohlc", {}).get("close", 0),
        "volume": quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
    }


def get_multi_quotes(symbols_list: list[dict], auth_token: str, config: dict | None = None) -> list[dict]:
    """Get quotes for multiple symbols using batch Kite quote API."""
    # Build i= params: i=NSE:SBIN&i=NSE:TCS
    params_map = {}  # instrument_param -> {symbol, exchange}
    query_parts = []
    for item in symbols_list:
        sym, exch = item["symbol"], item["exchange"]
        instrument_param = _get_instrument_param(sym, exch)
        params_map[instrument_param] = {"symbol": sym, "exchange": exch}
        query_parts.append(f"i={instrument_param}")

    if not query_parts:
        return []

    client = get_httpx_client()
    url = f"https://api.kite.trade/quote?{'&'.join(query_parts)}"
    response = client.get(url, headers=_headers(auth_token))
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        return []

    results = []
    for key, quote_data in data["data"].items():
        info = params_map.get(key)
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
            "prev_close": ohlc.get("close", 0),
            "volume": quote_data.get("volume", 0),
            "oi": quote_data.get("oi", 0),
        })

    return results


def get_market_depth(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get 5-level market depth for a symbol."""
    instrument_param = _get_instrument_param(symbol, exchange)

    client = get_httpx_client()
    url = f"https://api.kite.trade/quote?i={instrument_param}"
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
        "prev_close": ohlc.get("close", 0),
        "volume": quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
        "totalbuyqty": quote_data.get("buy_quantity", 0),
        "totalsellqty": quote_data.get("sell_quantity", 0),
    }


def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get historical OHLCV candles with automatic date chunking."""
    instrument_token = _get_instrument_token(symbol, exchange)

    if interval not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP.keys())}")

    resolution = TIMEFRAME_MAP[interval]

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Zerodha limits: 60 days for intraday, 400 days for daily
    if resolution == "day":
        chunk_days = 400
    else:
        chunk_days = 60

    client = get_httpx_client()
    all_candles = []

    current_start = start_dt
    while current_start <= end_dt:
        current_end = min(current_start + timedelta(days=chunk_days), end_dt)

        from_str = f"{current_start.isoformat()}+00:00:00"
        to_str = f"{current_end.isoformat()}+23:59:59"

        url = (
            f"https://api.kite.trade/instruments/historical/{instrument_token}/{resolution}"
            f"?from={from_str}&to={to_str}&oi=1"
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
