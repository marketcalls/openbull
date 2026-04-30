"""
Dhan market data API - quotes, multi-quotes, depth, history.
Adapted from OpenAlgo's dhan data.py.

Rate limiting: Dhan caps data API at ~1 req/sec. We serialize requests with
a module-level lock + min-interval gate.
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta

from backend.broker.upstox.mapping.order_data import get_token_from_cache
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)

DHAN_BASE_URL = "https://api.dhan.co"

# Standard intervals -> Dhan resolutions.
TIMEFRAME_MAP: dict = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "25m": "25",
    "1h": "60",
    "D": "D",
}

SUPPORTED_INTERVALS = {
    "seconds": [],
    "minutes": ["1m", "5m", "15m", "25m"],
    "hours": ["1h"],
    "days": ["D"],
}

DHAN_MIN_REQUEST_INTERVAL = 1.1
_last_api_call_time = 0.0
_rate_limit_lock = threading.Lock()


def _apply_rate_limit() -> None:
    """Serialize Dhan data API calls to avoid error 805.

    Dhan's /v2/marketfeed/* enforces 1 req/sec on a rolling window; spacing
    at exactly 1.0s frequently lands on the boundary and trips 805. 100ms
    of margin keeps polls inside the allowed window with no latency cost.
    """
    global _last_api_call_time
    sleep_time = 0.0
    with _rate_limit_lock:
        now = time.time()
        delta = now - _last_api_call_time
        if delta < DHAN_MIN_REQUEST_INTERVAL:
            sleep_time = DHAN_MIN_REQUEST_INTERVAL - delta
        _last_api_call_time = now + sleep_time

    if sleep_time > 0:
        time.sleep(sleep_time)


def _client_id_from_config(config: dict | None) -> str | None:
    if not config:
        return None
    cid = config.get("client_id") or config.get("dhan_client_id")
    if cid:
        return str(cid)
    api_key = config.get("api_key") or ""
    if ":::" in api_key:
        head, _, _ = api_key.partition(":::")
        return head or None
    return None


def _strip_token_suffix(token: str) -> str:
    return str(token).split("::::")[0] if "::::" in str(token) else str(token)


def _exchange_segment(exchange: str) -> str | None:
    return {
        "NSE": "NSE_EQ",
        "BSE": "BSE_EQ",
        "NFO": "NSE_FNO",
        "BFO": "BSE_FNO",
        "MCX": "MCX_COMM",
        "CDS": "NSE_CURRENCY",
        "BCD": "BSE_CURRENCY",
        "NSE_INDEX": "IDX_I",
        "BSE_INDEX": "IDX_I",
    }.get(exchange)


def _instrument_type(exchange: str, symbol: str) -> str:
    if exchange in ("NSE", "BSE"):
        return "EQUITY"
    if exchange in ("NSE_INDEX", "BSE_INDEX"):
        return "INDEX"

    INDEX_NAMES = (
        "NIFTY", "NIFTYNXT50", "FINNIFTY", "BANKNIFTY", "MIDCPNIFTY",
        "INDIAVIX", "SENSEX", "BANKEX", "SENSEX50",
    )

    if exchange in ("NFO", "BFO"):
        if symbol.endswith("CE") or symbol.endswith("PE"):
            return "OPTIDX" if any(idx in symbol for idx in INDEX_NAMES) else "OPTSTK"
        return "FUTIDX" if any(idx in symbol for idx in INDEX_NAMES) else "FUTSTK"

    if exchange == "MCX":
        if symbol.endswith("CE") or symbol.endswith("PE"):
            return "OPTFUT"
        return "FUTCOM"

    if exchange in ("CDS", "BCD"):
        if symbol.endswith("CE") or symbol.endswith("PE"):
            return "OPTCUR"
        return "FUTCUR"

    raise ValueError(f"Unsupported exchange: {exchange}")


def _make_api_call(
    endpoint: str, auth_token: str, config: dict | None,
    method: str = "POST", payload: str = "", retry_count: int = 0,
) -> dict:
    """Call Dhan with rate limiting + retry on 805 (rate limit hit)."""
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0

    _apply_rate_limit()

    client_id = _client_id_from_config(config)

    headers = {
        "access-token": auth_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if client_id:
        headers["client-id"] = client_id

    url = f"{DHAN_BASE_URL}{endpoint}"
    client = get_httpx_client()

    if method == "GET":
        res = client.get(url, headers=headers)
    elif method == "POST":
        res = client.post(url, headers=headers, content=payload)
    elif method == "PUT":
        res = client.put(url, headers=headers, content=payload)
    else:
        res = client.request(method, url, headers=headers, content=payload)

    try:
        response = json.loads(res.text)
    except (json.JSONDecodeError, ValueError):
        raise Exception(f"Invalid Dhan response (HTTP {res.status_code}): {res.text[:200]}")

    if isinstance(response, dict) and response.get("status") == "failed":
        error_data = response.get("data", {}) or {}
        error_code = list(error_data.keys())[0] if error_data else "unknown"
        error_message = error_data.get(error_code, "Unknown error")

        if error_code == "805" and retry_count < MAX_RETRIES:
            retry_delay = RETRY_DELAY * (2 ** retry_count)
            logger.warning(
                "Rate limit hit (805). Retrying in %.1fs (attempt %d/%d)",
                retry_delay, retry_count + 1, MAX_RETRIES,
            )
            time.sleep(retry_delay)
            return _make_api_call(endpoint, auth_token, config, method, payload, retry_count + 1)

        error_mapping = {
            "805": "Rate limit exceeded.",
            "806": "Data APIs not subscribed.",
            "810": "Authentication failed: Invalid client ID",
            "401": "Invalid or expired access token",
            "820": "Market data subscription required",
            "821": "Market data subscription required",
        }
        msg = error_mapping.get(error_code, f"Dhan API Error {error_code}: {error_message}")
        logger.error("Dhan data API error: %s", msg)
        raise Exception(msg)

    return response


def _empty_quote() -> dict:
    return {
        "ltp": 0, "open": 0, "high": 0, "low": 0,
        "volume": 0, "oi": 0, "bid": 0, "ask": 0, "prev_close": 0,
    }


def get_quotes(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
    """Get LTP/OHLC quote for a single symbol."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")
    security_id = _strip_token_suffix(token)
    exchange_type = _exchange_segment(exchange)
    if not exchange_type:
        raise ValueError(f"Unsupported exchange: {exchange}")

    payload = {exchange_type: [int(security_id)]}
    response = _make_api_call(
        "/v2/marketfeed/quote", auth_token, config, "POST", json.dumps(payload),
    )
    quote_data = (
        response.get("data", {}).get(exchange_type, {}).get(str(security_id), {})
    )
    if not quote_data:
        return _empty_quote()

    last_price = quote_data.get("last_price") or quote_data.get("lastPrice") or 0
    ohlc = quote_data.get("ohlc", {}) or {}

    result = {
        "ltp": float(last_price),
        "open": float(ohlc.get("open", 0) or 0),
        "high": float(ohlc.get("high", 0) or 0),
        "low": float(ohlc.get("low", 0) or 0),
        "volume": int(float(quote_data.get("volume", 0) or 0)),
        "oi": int(float(quote_data.get("oi") or quote_data.get("open_interest") or 0)),
        "bid": 0.0,
        "ask": 0.0,
        "prev_close": float(ohlc.get("close", 0) or 0),
    }

    depth = quote_data.get("depth", {}) or {}
    buy_orders = depth.get("buy", []) or []
    sell_orders = depth.get("sell", []) or []
    if buy_orders:
        result["bid"] = float(buy_orders[0].get("price", 0) or 0)
    if sell_orders:
        result["ask"] = float(sell_orders[0].get("price", 0) or 0)

    return result


def get_multi_quotes(
    symbols_list: list[dict], auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get quotes for multiple symbols, batched (Dhan supports up to 1000/req)."""
    if not symbols_list:
        return []

    BATCH_SIZE = 1000

    if len(symbols_list) <= BATCH_SIZE:
        return _process_quotes_batch(symbols_list, auth_token, config)

    all_results: list[dict] = []
    for i in range(0, len(symbols_list), BATCH_SIZE):
        batch = symbols_list[i : i + BATCH_SIZE]
        all_results.extend(_process_quotes_batch(batch, auth_token, config))
    return all_results


def _process_quotes_batch(
    symbols: list[dict], auth_token: str, config: dict | None,
) -> list[dict]:
    exchange_securities: dict[str, list[int]] = {}
    security_map: dict[str, dict] = {}

    for item in symbols:
        sym, exch = item.get("symbol"), item.get("exchange")
        if not sym or not exch:
            continue
        token = get_token_from_cache(sym, exch)
        if not token:
            continue
        security_id = _strip_token_suffix(token)
        exchange_segment = _exchange_segment(exch)
        if not exchange_segment:
            continue
        try:
            sec_int = int(security_id)
        except (TypeError, ValueError):
            continue
        exchange_securities.setdefault(exchange_segment, []).append(sec_int)
        security_map[f"{exchange_segment}:{security_id}"] = {
            "symbol": sym, "exchange": exch, "security_id": security_id,
        }

    if not exchange_securities:
        return []

    response = _make_api_call(
        "/v2/marketfeed/quote", auth_token, config,
        "POST", json.dumps(exchange_securities),
    )

    results: list[dict] = []
    response_data = response.get("data", {}) or {}

    for key, original in security_map.items():
        exchange_segment, security_id = key.split(":")
        segment_data = response_data.get(exchange_segment, {}) or {}
        quote_data = segment_data.get(str(security_id), {}) or {}

        if not quote_data:
            results.append({
                "symbol": original["symbol"],
                "exchange": original["exchange"],
                "error": "No quote data available",
            })
            continue

        ohlc = quote_data.get("ohlc", {}) or {}
        depth = quote_data.get("depth") or {}
        buy_orders = depth.get("buy", []) or []
        sell_orders = depth.get("sell", []) or []

        last_price = quote_data.get("last_price") or quote_data.get("lastPrice") or 0
        volume = quote_data.get("volume") or 0
        oi = quote_data.get("oi") or quote_data.get("open_interest") or 0

        results.append({
            "symbol": original["symbol"],
            "exchange": original["exchange"],
            "data": {
                "bid": float(buy_orders[0].get("price", 0) or 0) if buy_orders else 0,
                "ask": float(sell_orders[0].get("price", 0) or 0) if sell_orders else 0,
                "open": float(ohlc.get("open", 0) or 0),
                "high": float(ohlc.get("high", 0) or 0),
                "low": float(ohlc.get("low", 0) or 0),
                "ltp": float(last_price),
                "prev_close": float(ohlc.get("close", 0) or 0),
                "volume": int(float(volume or 0)),
                "oi": int(float(oi or 0)),
            },
        })

    return results


def get_market_depth(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None,
) -> dict:
    """Get 5-level market depth for a symbol."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")
    security_id = _strip_token_suffix(token)
    exchange_type = _exchange_segment(exchange)
    if not exchange_type:
        raise ValueError(f"Unsupported exchange: {exchange}")

    payload = {exchange_type: [int(security_id)]}
    response = _make_api_call(
        "/v2/marketfeed/quote", auth_token, config, "POST", json.dumps(payload),
    )
    quote_data = (
        response.get("data", {}).get(exchange_type, {}).get(str(security_id), {}) or {}
    )

    if not quote_data:
        return {
            "bids": [{"price": 0, "quantity": 0} for _ in range(5)],
            "asks": [{"price": 0, "quantity": 0} for _ in range(5)],
            "ltp": 0, "ltq": 0, "volume": 0, "open": 0, "high": 0,
            "low": 0, "prev_close": 0, "oi": 0,
            "totalbuyqty": 0, "totalsellqty": 0,
        }

    depth = quote_data.get("depth", {}) or {}
    ohlc = quote_data.get("ohlc", {}) or {}

    bids = []
    asks = []
    buy_orders = depth.get("buy", []) or []
    sell_orders = depth.get("sell", []) or []
    for i in range(5):
        if i < len(buy_orders):
            bids.append({
                "price": float(buy_orders[i].get("price", 0) or 0),
                "quantity": int(buy_orders[i].get("quantity", 0) or 0),
            })
        else:
            bids.append({"price": 0, "quantity": 0})
        if i < len(sell_orders):
            asks.append({
                "price": float(sell_orders[i].get("price", 0) or 0),
                "quantity": int(sell_orders[i].get("quantity", 0) or 0),
            })
        else:
            asks.append({"price": 0, "quantity": 0})

    return {
        "bids": bids,
        "asks": asks,
        "ltp": float(quote_data.get("last_price", 0) or 0),
        "ltq": int(quote_data.get("last_quantity", 0) or 0),
        "volume": int(quote_data.get("volume", 0) or 0),
        "open": float(ohlc.get("open", 0) or 0),
        "high": float(ohlc.get("high", 0) or 0),
        "low": float(ohlc.get("low", 0) or 0),
        "prev_close": float(ohlc.get("close", 0) or 0),
        "oi": int(quote_data.get("oi", 0) or 0),
        "totalbuyqty": sum(b["quantity"] for b in bids),
        "totalsellqty": sum(a["quantity"] for a in asks),
    }


def _convert_timestamp_to_ist(timestamp: int, is_daily: bool = False) -> int:
    """Convert Dhan UTC timestamp to IST epoch (matches openalgo behavior)."""
    if is_daily:
        utc_dt = datetime.utcfromtimestamp(timestamp)
        ist_dt = utc_dt + timedelta(hours=5, minutes=30)
        start_of_day = datetime(ist_dt.year, ist_dt.month, ist_dt.day)
        return int(start_of_day.timestamp() + 19800)
    utc_dt = datetime.utcfromtimestamp(timestamp)
    ist_dt = utc_dt + timedelta(hours=5, minutes=30)
    return int(ist_dt.timestamp())


def _is_trading_day(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _adjust_dates(start_date: str, end_date: str) -> tuple[str, str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while start.weekday() >= 5:
        start += timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _intraday_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Split a range into 90-day chunks (Dhan intraday API limit)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    chunks: list[tuple[str, str]] = []
    while start < end:
        chunk_end = min(start + timedelta(days=90), end)
        chunks.append((start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        start = chunk_end
    return chunks


def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:
    """Get OHLCV history for a symbol.

    Returns list[dict] with keys: timestamp (int), open, high, low, close,
    volume, oi.
    """
    if interval not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported interval '{interval}'. Supported: {list(TIMEFRAME_MAP.keys())}"
        )

    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise ValueError(f"Instrument token not found for {symbol}/{exchange}")
    security_id = _strip_token_suffix(token)

    exchange_segment = _exchange_segment(exchange)
    if not exchange_segment:
        raise ValueError(f"Unsupported exchange: {exchange}")
    instrument_type = _instrument_type(exchange, symbol)

    # Convert datetime objects to strings if needed
    if not isinstance(start_date, str):
        start_date = start_date.strftime("%Y-%m-%d")
    if not isinstance(end_date, str):
        end_date = end_date.strftime("%Y-%m-%d")

    start_date, end_date = _adjust_dates(start_date, end_date)

    if not _is_trading_day(start_date) and not _is_trading_day(end_date):
        return []

    if start_date == end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_date = end_dt.strftime("%Y-%m-%d")

    all_candles: list[dict] = []

    if interval == "D":
        endpoint = "/v2/charts/historical"
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        request_data = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "fromDate": start_date,
            "toDate": end_dt.strftime("%Y-%m-%d"),
            "oi": True,
            "expiryCode": 0,
        }
        try:
            response = _make_api_call(
                endpoint, auth_token, config, "POST", json.dumps(request_data),
            )
            timestamps = response.get("timestamp", []) or []
            opens = response.get("open", []) or []
            highs = response.get("high", []) or []
            lows = response.get("low", []) or []
            closes = response.get("close", []) or []
            volumes = response.get("volume", []) or []
            ois = response.get("open_interest", []) or []
            for i in range(len(timestamps)):
                all_candles.append({
                    "timestamp": _convert_timestamp_to_ist(timestamps[i], is_daily=True),
                    "open": float(opens[i]) if i < len(opens) and opens[i] is not None else 0.0,
                    "high": float(highs[i]) if i < len(highs) and highs[i] is not None else 0.0,
                    "low": float(lows[i]) if i < len(lows) and lows[i] is not None else 0.0,
                    "close": float(closes[i]) if i < len(closes) and closes[i] is not None else 0.0,
                    "volume": int(float(volumes[i])) if i < len(volumes) and volumes[i] is not None else 0,
                    "oi": int(float(ois[i])) if i < len(ois) and ois[i] is not None else 0,
                })
        except Exception as e:
            logger.error("Error fetching daily Dhan history: %s", e)
    else:
        endpoint = "/v2/charts/intraday"
        chunks = _intraday_chunks(start_date, end_date) or [(start_date, end_date)]
        for chunk_start, chunk_end in chunks:
            if not _is_trading_day(chunk_start) and not _is_trading_day(chunk_end):
                continue
            request_data = {
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment,
                "instrument": instrument_type,
                "interval": TIMEFRAME_MAP[interval],
                "fromDate": chunk_start,
                "toDate": chunk_end,
                "oi": True,
                "expiryCode": 0,
            }
            try:
                response = _make_api_call(
                    endpoint, auth_token, config, "POST", json.dumps(request_data),
                )
                timestamps = response.get("timestamp", []) or []
                opens = response.get("open", []) or []
                highs = response.get("high", []) or []
                lows = response.get("low", []) or []
                closes = response.get("close", []) or []
                volumes = response.get("volume", []) or []
                ois = response.get("open_interest", []) or []
                for i in range(len(timestamps)):
                    all_candles.append({
                        "timestamp": _convert_timestamp_to_ist(timestamps[i]),
                        "open": float(opens[i]) if i < len(opens) and opens[i] is not None else 0.0,
                        "high": float(highs[i]) if i < len(highs) and highs[i] is not None else 0.0,
                        "low": float(lows[i]) if i < len(lows) and lows[i] is not None else 0.0,
                        "close": float(closes[i]) if i < len(closes) and closes[i] is not None else 0.0,
                        "volume": int(float(volumes[i])) if i < len(volumes) and volumes[i] is not None else 0,
                        "oi": int(float(ois[i])) if i < len(ois) and ois[i] is not None else 0,
                    })
            except Exception as e:
                logger.error("Error fetching intraday Dhan chunk %s..%s: %s", chunk_start, chunk_end, e)
                continue

    # Append today's candle for daily timeframe if today is in range
    if interval == "D":
        today = datetime.now().strftime("%Y-%m-%d")
        if start_date <= today <= end_date:
            try:
                quotes = get_quotes(symbol, exchange, auth_token, config)
                if quotes and float(quotes.get("ltp") or 0) > 0:
                    today_dt = datetime.strptime(today, "%Y-%m-%d")
                    today_ts = int(today_dt.timestamp() + 19800)
                    all_candles.append({
                        "timestamp": today_ts,
                        "open": float(quotes.get("open", 0) or 0),
                        "high": float(quotes.get("high", 0) or 0),
                        "low": float(quotes.get("low", 0) or 0),
                        "close": float(quotes.get("ltp", 0) or 0),
                        "volume": int(quotes.get("volume", 0) or 0),
                        "oi": int(quotes.get("oi", 0) or 0),
                    })
            except Exception as e:
                logger.debug("Could not synthesize today's D candle: %s", e)

    if not all_candles:
        return []

    # Sort by timestamp and dedup
    seen: set[int] = set()
    deduped: list[dict] = []
    for c in sorted(all_candles, key=lambda x: x["timestamp"]):
        if c["timestamp"] in seen:
            continue
        seen.add(c["timestamp"])
        deduped.append(c)
    return deduped
