"""
Zerodha market data API - quotes, depth, historical candles.
"""

import logging
import time
from datetime import datetime, timedelta

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


class ZerodhaAPIError(Exception):
    """Base exception for Kite Connect API errors with structured error_type."""

    def __init__(self, message: str, error_type: str | None = None):
        super().__init__(message)
        self.error_type = error_type


class ZerodhaPermissionError(ZerodhaAPIError):
    """Raised when Kite returns error_type == 'PermissionException'.

    Distinct from generic ValueError so callers can suppress noisy
    permission-denied logs (e.g. user without F&O segment trying to
    fetch index option quotes).
    """


def _raise_for_kite_error(data: dict) -> None:
    """Inspect a Kite envelope and raise the right exception class."""
    if data.get("status") == "success":
        return
    error_type = data.get("error_type")
    message = data.get("message") or "Kite API error"
    if error_type == "PermissionException":
        raise ZerodhaPermissionError(message, error_type=error_type)
    raise ZerodhaAPIError(message, error_type=error_type)


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

# Kite's /quote endpoint accepts up to 500 instruments per request.
QUOTE_BATCH_SIZE = 500
QUOTE_BATCH_DELAY = 1.0


def _headers(auth_token: str) -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth_token}",
        "Accept": "application/json",
    }


# Kite's /quote endpoint expects bare exchange codes (NSE/BSE/...) in the
# `EXCHANGE:SYMBOL` param. Our cache may store INDEX-segmented variants
# (NSE_INDEX, BSE_INDEX, ...) for clarity. Rewrite back to the bare code
# when building URL params, or index quotes return null.
_KITE_QUOTE_EXCHANGE = {
    "NSE_INDEX": "NSE",
    "BSE_INDEX": "BSE",
    "MCX_INDEX": "MCX",
    "CDS_INDEX": "CDS",
}


def _get_instrument_param(symbol: str, exchange: str) -> str:
    """Build Zerodha quote param: EXCHANGE:BRSYMBOL"""
    brsymbol = get_brsymbol_from_cache(symbol, exchange) or symbol
    kite_exchange = _KITE_QUOTE_EXCHANGE.get(exchange, exchange)
    return f"{kite_exchange}:{brsymbol}"


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
    response = client.get(
        "https://api.kite.trade/quote",
        params=[("i", instrument_param)],
        headers=_headers(auth_token),
    )
    response.raise_for_status()
    data = response.json()

    _raise_for_kite_error(data)
    if not data.get("data"):
        raise ZerodhaAPIError("Empty quote payload from Kite")

    # Data is keyed by "EXCHANGE:SYMBOL". Skip null entries — Kite returns
    # them for unknown / expired / illiquid instruments, and a downstream
    # `.get()` would AttributeError.
    quote_data = next(
        (val for val in data["data"].values() if val is not None),
        None,
    )

    if not quote_data:
        raise ZerodhaAPIError("No quote data in response")

    ohlc = quote_data.get("ohlc") or {}
    depth = quote_data.get("depth") or {}
    bids = depth.get("buy") or []
    asks = depth.get("sell") or []
    top_bid = bids[0] if bids else {}
    top_ask = asks[0] if asks else {}

    return {
        "ltp": quote_data.get("last_price", 0),
        "open": ohlc.get("open", 0),
        "high": ohlc.get("high", 0),
        "low": ohlc.get("low", 0),
        "close": ohlc.get("close", 0),
        "prev_close": ohlc.get("close", 0),
        "volume": quote_data.get("volume", 0),
        "oi": quote_data.get("oi", 0),
        "bid": top_bid.get("price", 0),
        "ask": top_ask.get("price", 0),
        "bid_qty": top_bid.get("quantity", 0),
        "ask_qty": top_ask.get("quantity", 0),
    }


def _process_quote_batch(
    batch: list[dict], auth_token: str
) -> list[dict]:
    """Fetch a single batch of <= QUOTE_BATCH_SIZE symbols from /quote."""
    params_map: dict = {}
    params: list[tuple[str, str]] = []
    for item in batch:
        sym, exch = item["symbol"], item["exchange"]
        instrument_param = _get_instrument_param(sym, exch)
        params_map[instrument_param] = {"symbol": sym, "exchange": exch}
        params.append(("i", instrument_param))

    if not params:
        return []

    client = get_httpx_client()
    response = client.get(
        "https://api.kite.trade/quote", params=params, headers=_headers(auth_token)
    )
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "success" or not data.get("data"):
        return []

    results: list[dict] = []
    for key, quote_data in data["data"].items():
        # Zerodha returns null for unknown/expired/illiquid instruments —
        # without skipping these the .get() calls below AttributeError and
        # the entire batch raises, zeroing every leg.
        if not quote_data:
            continue
        info = params_map.get(key)
        if not info:
            continue

        # `or {}` covers both missing-key and explicit-None values — Kite
        # sometimes returns "ohlc": null on illiquid contracts, which would
        # AttributeError below.
        ohlc = quote_data.get("ohlc") or {}
        depth = quote_data.get("depth") or {}
        bids = depth.get("buy") or []
        asks = depth.get("sell") or []
        top_bid = bids[0] if bids else {}
        top_ask = asks[0] if asks else {}
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
            "bid": top_bid.get("price", 0),
            "ask": top_ask.get("price", 0),
            "bid_qty": top_bid.get("quantity", 0),
            "ask_qty": top_ask.get("quantity", 0),
        })

    return results


def get_multi_quotes(symbols_list: list[dict], auth_token: str, config: dict | None = None) -> list[dict]:
    """Get quotes for multiple symbols using batch Kite quote API.

    Kite's /quote endpoint caps each request at 500 instruments. Above that
    we split into batches and pace them at QUOTE_BATCH_DELAY seconds to stay
    inside Kite's 1 req/sec quote rate limit.
    """
    if not symbols_list:
        return []

    if len(symbols_list) <= QUOTE_BATCH_SIZE:
        return _process_quote_batch(symbols_list, auth_token)

    all_results: list[dict] = []
    total = len(symbols_list)
    for i in range(0, total, QUOTE_BATCH_SIZE):
        batch = symbols_list[i : i + QUOTE_BATCH_SIZE]
        all_results.extend(_process_quote_batch(batch, auth_token))
        if i + QUOTE_BATCH_SIZE < total:
            time.sleep(QUOTE_BATCH_DELAY)
    logger.info(
        "get_multi_quotes processed %d symbols across %d batches",
        total, (total + QUOTE_BATCH_SIZE - 1) // QUOTE_BATCH_SIZE,
    )
    return all_results


def get_market_depth(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get 5-level market depth for a symbol."""
    instrument_param = _get_instrument_param(symbol, exchange)

    client = get_httpx_client()
    response = client.get(
        "https://api.kite.trade/quote",
        params=[("i", instrument_param)],
        headers=_headers(auth_token),
    )
    response.raise_for_status()
    data = response.json()

    _raise_for_kite_error(data)
    if not data.get("data"):
        raise ZerodhaAPIError("Empty depth payload from Kite")

    quote_data = next(
        (val for val in data["data"].values() if val is not None),
        None,
    )

    if not quote_data:
        raise ZerodhaAPIError("No depth data in response")

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
        "ltp": quote_data.get("last_price", 0),
        "ltq": quote_data.get("last_quantity", 0),
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
