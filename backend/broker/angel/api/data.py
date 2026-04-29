"""
Angel One market data API — quotes, depth, historical candles.
Adapted from OpenAlgo's angel data.py.

Public surface (matches openbull contract):
  - TIMEFRAME_MAP (module-level dict)
  - get_quotes(symbol, exchange, auth_token, config=None) -> dict
  - get_multi_quotes(symbols_list, auth_token, config=None) -> list[dict]
  - get_market_depth(symbol, exchange, auth_token, config=None) -> dict
  - get_history(symbol, exchange, interval, start_date, end_date, auth_token, config=None) -> list[dict]
"""

import json
import logging
import time
from datetime import datetime, timedelta

from backend.broker.upstox.mapping.order_data import (
    get_brsymbol_from_cache,
    get_token_from_cache,
)
from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


TIMEFRAME_MAP = {
    "1m": "ONE_MINUTE",
    "3m": "THREE_MINUTE",
    "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "D": "ONE_DAY",
}

# Per-interval candle window cap (in days) per Angel API docs.
_INTERVAL_LIMITS = {
    "1m": 30,
    "3m": 60,
    "5m": 100,
    "10m": 100,
    "15m": 200,
    "30m": 200,
    "1h": 400,
    "D": 2000,
}

# Angel multi-quote API: 50 symbols per request, 1 req/sec.
_QUOTE_BATCH_SIZE = 50
_QUOTE_RATE_DELAY = 1.0


def _split_token(auth_token: str, config: dict | None) -> tuple[str, str]:
    """Split combined ``api_key:jwt_token:feed_token`` token."""
    parts = auth_token.split(":") if auth_token else []
    if len(parts) >= 2:
        return parts[0], parts[1]
    api_key = (config or {}).get("api_key", "")
    return api_key, auth_token or ""


def _angel_headers(api_key: str, jwt_token: str) -> dict:
    return {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "CLIENT_LOCAL_IP",
        "X-ClientPublicIP": "CLIENT_PUBLIC_IP",
        "X-MACAddress": "MAC_ADDRESS",
        "X-PrivateKey": api_key,
    }


def _post(endpoint: str, payload: dict, auth_token: str, config: dict | None = None) -> dict:
    """POST to an Angel endpoint and parse the JSON envelope."""
    api_key, jwt_token = _split_token(auth_token, config)
    headers = _angel_headers(api_key, jwt_token)

    client = get_httpx_client()
    url = f"https://apiconnect.angelone.in{endpoint}"

    response = client.post(url, headers=headers, content=json.dumps(payload))
    if response.status_code == 403:
        logger.debug("Angel returned 403 Forbidden on %s", endpoint)
        raise Exception("Authentication failed. Please check your API key and auth token.")

    try:
        return response.json()
    except json.JSONDecodeError:
        logger.error("Angel non-JSON response on %s (status=%s): %s",
                     endpoint, response.status_code, response.text[:200])
        raise Exception(f"Failed to parse API response (status {response.status_code})")


def _api_exchange(exchange: str) -> str:
    """Map our INDEX-segmented exchanges back to bare codes Angel expects."""
    if exchange == "NSE_INDEX":
        return "NSE"
    if exchange == "BSE_INDEX":
        return "BSE"
    if exchange == "MCX_INDEX":
        return "MCX"
    return exchange


# ---------- Quotes ----------

def get_quotes(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """Fetch a single LTP/OHLC quote."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)
    payload = {"mode": "FULL", "exchangeTokens": {api_exch: [token]}}

    response = _post(
        "/rest/secure/angelbroking/market/v1/quote/", payload, auth_token, config
    )
    if not response.get("status"):
        raise Exception(f"Error from Angel API: {response.get('message', 'Unknown error')}")

    fetched = (response.get("data") or {}).get("fetched") or []
    if not fetched:
        raise Exception("No quote data received")

    quote = fetched[0]
    depth = quote.get("depth") or {}
    bids = depth.get("buy") or []
    asks = depth.get("sell") or []
    top_bid = bids[0] if bids else {}
    top_ask = asks[0] if asks else {}

    return {
        "bid": float(top_bid.get("price", 0) or 0),
        "ask": float(top_ask.get("price", 0) or 0),
        "open": float(quote.get("open", 0) or 0),
        "high": float(quote.get("high", 0) or 0),
        "low": float(quote.get("low", 0) or 0),
        "ltp": float(quote.get("ltp", 0) or 0),
        "prev_close": float(quote.get("close", 0) or 0),
        "volume": int(quote.get("tradeVolume", 0) or 0),
        "oi": int(quote.get("opnInterest", 0) or 0),
    }


def _process_quote_batch(
    batch: list[dict], auth_token: str, config: dict | None = None
) -> list[dict]:
    """Fetch a single batch (<= 50 symbols) from Angel's quote API."""
    exchange_tokens: dict[str, list[str]] = {}
    token_map: dict[str, dict] = {}
    skipped: list[dict] = []

    for item in batch:
        sym = item.get("symbol")
        exch = item.get("exchange")
        if not sym or not exch:
            continue

        try:
            token = get_token_from_cache(sym, exch)
            if not token:
                logger.warning("Skipping %s/%s: token not found", sym, exch)
                skipped.append({"symbol": sym, "exchange": exch, "error": "Could not resolve token"})
                continue

            api_exch = _api_exchange(exch)
            exchange_tokens.setdefault(api_exch, []).append(token)
            token_map[f"{api_exch}:{token}"] = {
                "symbol": sym,
                "exchange": exch,
                "token": token,
            }
        except Exception as e:
            logger.warning("Skipping %s/%s: %s", sym, exch, e)
            skipped.append({"symbol": sym, "exchange": exch, "error": str(e)})

    if not exchange_tokens:
        return skipped

    payload = {"mode": "FULL", "exchangeTokens": exchange_tokens}
    response = _post(
        "/rest/secure/angelbroking/market/v1/quote/", payload, auth_token, config
    )
    if not response.get("status"):
        msg = f"Error from Angel API: {response.get('message', 'Unknown error')}"
        logger.error(msg)
        raise Exception(msg)

    fetched = (response.get("data") or {}).get("fetched") or []
    unfetched = (response.get("data") or {}).get("unfetched") or []
    if unfetched:
        logger.warning("Angel multi-quote unfetched: %s", unfetched)

    quotes_by_key: dict[str, dict] = {}
    for q in fetched:
        ex = q.get("exchange")
        tk = q.get("symbolToken")
        if ex and tk is not None:
            quotes_by_key[f"{ex}:{tk}"] = q

    results: list[dict] = []
    for key, original in token_map.items():
        quote = quotes_by_key.get(key)
        if not quote:
            results.append({
                "symbol": original["symbol"],
                "exchange": original["exchange"],
                "error": "No quote data available",
            })
            continue

        depth = quote.get("depth") or {}
        bids = depth.get("buy") or []
        asks = depth.get("sell") or []
        top_bid = bids[0] if bids else {}
        top_ask = asks[0] if asks else {}

        results.append({
            "symbol": original["symbol"],
            "exchange": original["exchange"],
            "ltp": float(quote.get("ltp", 0) or 0),
            "open": float(quote.get("open", 0) or 0),
            "high": float(quote.get("high", 0) or 0),
            "low": float(quote.get("low", 0) or 0),
            "close": float(quote.get("close", 0) or 0),
            "prev_close": float(quote.get("close", 0) or 0),
            "volume": int(quote.get("tradeVolume", 0) or 0),
            "oi": int(quote.get("opnInterest", 0) or 0),
            "bid": float(top_bid.get("price", 0) or 0),
            "ask": float(top_ask.get("price", 0) or 0),
            "bid_qty": int(top_bid.get("quantity", 0) or 0),
            "ask_qty": int(top_ask.get("quantity", 0) or 0),
        })

    return skipped + results


def get_multi_quotes(
    symbols_list: list[dict], auth_token: str, config: dict | None = None
) -> list[dict]:
    """Fetch quotes for many symbols, batching to Angel's 50-per-request cap."""
    if not symbols_list:
        return []

    if len(symbols_list) <= _QUOTE_BATCH_SIZE:
        return _process_quote_batch(symbols_list, auth_token, config)

    all_results: list[dict] = []
    total = len(symbols_list)
    for i in range(0, total, _QUOTE_BATCH_SIZE):
        batch = symbols_list[i : i + _QUOTE_BATCH_SIZE]
        all_results.extend(_process_quote_batch(batch, auth_token, config))
        if i + _QUOTE_BATCH_SIZE < total:
            time.sleep(_QUOTE_RATE_DELAY)
    logger.info(
        "Angel get_multi_quotes processed %d symbols across %d batches",
        total, (total + _QUOTE_BATCH_SIZE - 1) // _QUOTE_BATCH_SIZE,
    )
    return all_results


# ---------- Market depth ----------

def get_market_depth(
    symbol: str, exchange: str, auth_token: str, config: dict | None = None
) -> dict:
    """5-level market depth (bids / asks / OHLC / volume / OI)."""
    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)
    payload = {"mode": "FULL", "exchangeTokens": {api_exch: [token]}}

    response = _post(
        "/rest/secure/angelbroking/market/v1/quote/", payload, auth_token, config
    )
    if not response.get("status"):
        raise Exception(f"Error from Angel API: {response.get('message', 'Unknown error')}")

    fetched = (response.get("data") or {}).get("fetched") or []
    if not fetched:
        raise Exception("No depth data received")

    quote = fetched[0]
    depth = quote.get("depth") or {}

    bids = []
    asks = []
    buy_orders = depth.get("buy") or []
    sell_orders = depth.get("sell") or []

    for i in range(5):
        if i < len(buy_orders):
            b = buy_orders[i]
            bids.append({
                "price": float(b.get("price", 0) or 0),
                "quantity": int(b.get("quantity", 0) or 0),
                "orders": int(b.get("orders", 0) or 0),
            })
        else:
            bids.append({"price": 0, "quantity": 0, "orders": 0})

        if i < len(sell_orders):
            a = sell_orders[i]
            asks.append({
                "price": float(a.get("price", 0) or 0),
                "quantity": int(a.get("quantity", 0) or 0),
                "orders": int(a.get("orders", 0) or 0),
            })
        else:
            asks.append({"price": 0, "quantity": 0, "orders": 0})

    return {
        "bids": bids,
        "asks": asks,
        "high": float(quote.get("high", 0) or 0),
        "low": float(quote.get("low", 0) or 0),
        "ltp": float(quote.get("ltp", 0) or 0),
        "ltq": int(quote.get("lastTradeQty", 0) or 0),
        "open": float(quote.get("open", 0) or 0),
        "prev_close": float(quote.get("close", 0) or 0),
        "close": float(quote.get("close", 0) or 0),
        "volume": int(quote.get("tradeVolume", 0) or 0),
        "oi": int(quote.get("opnInterest", 0) or 0),
        "totalbuyqty": int(quote.get("totBuyQuan", 0) or 0),
        "totalsellqty": int(quote.get("totSellQuan", 0) or 0),
    }


# ---------- Historical candles ----------

_IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60


def _parse_angel_ts(ts) -> int:
    """Convert Angel's timestamp string (ISO 8601) to Unix epoch seconds."""
    if isinstance(ts, (int, float)):
        return int(ts)
    if not isinstance(ts, str):
        return 0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def _fetch_oi_history(
    token: str,
    api_exch: str,
    interval: str,
    start_dt,
    end_dt,
    auth_token: str,
    config: dict | None,
) -> dict[int, int]:
    """Fetch OI candle history; returns {timestamp_epoch: oi_value}."""
    chunk_days = _INTERVAL_LIMITS.get(interval, 30)
    by_ts: dict[int, int] = {}

    cur = start_dt
    while cur <= end_dt:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
        payload = {
            "exchange": api_exch,
            "symboltoken": token,
            "interval": TIMEFRAME_MAP[interval],
            "fromdate": cur.strftime("%Y-%m-%d %H:%M"),
            "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            response = _post(
                "/rest/secure/angelbroking/historical/v1/getOIData",
                payload, auth_token, config,
            )
            if response and response.get("status"):
                rows = response.get("data") or []
                for row in rows:
                    if isinstance(row, dict):
                        ts = _parse_angel_ts(row.get("time") or row.get("timestamp"))
                        if interval == "D":
                            ts += _IST_OFFSET_SECONDS
                        try:
                            by_ts[ts] = int(row.get("oi", 0) or 0)
                        except (TypeError, ValueError):
                            by_ts[ts] = 0
        except Exception as e:
            logger.error("Angel OI chunk %s..%s failed: %s", cur, chunk_end, e)

        cur = chunk_end + timedelta(days=1)
        if cur <= end_dt:
            time.sleep(0.5)

    return by_ts


def get_history(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    auth_token: str,
    config: dict | None = None,
) -> list[dict]:
    """Fetch historical OHLCV(+oi for F&O) candles. Returns list[dict]."""
    if interval not in TIMEFRAME_MAP:
        raise ValueError(
            f"Unsupported interval: {interval}. Supported: {list(TIMEFRAME_MAP.keys())}"
        )

    token = get_token_from_cache(symbol, exchange)
    if not token:
        raise Exception(f"Instrument token not found for {symbol}/{exchange}")

    api_exch = _api_exchange(exchange)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(hour=0, minute=0)
    end_dt_only = datetime.strptime(end_date, "%Y-%m-%d")
    now = datetime.now()
    if end_dt_only.date() == now.date():
        end_dt = now.replace(second=0, microsecond=0)
    else:
        end_dt = end_dt_only.replace(hour=23, minute=59)

    chunk_days = _INTERVAL_LIMITS.get(interval, 30)

    all_candles: list[dict] = []
    cur = start_dt
    while cur <= end_dt:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_dt)
        payload = {
            "exchange": api_exch,
            "symboltoken": token,
            "interval": TIMEFRAME_MAP[interval],
            "fromdate": cur.strftime("%Y-%m-%d %H:%M"),
            "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            response = _post(
                "/rest/secure/angelbroking/historical/v1/getCandleData",
                payload, auth_token, config,
            )
            if response and response.get("status") and response.get("data"):
                for row in response["data"]:
                    # row format: [timestamp, open, high, low, close, volume]
                    if not row or len(row) < 6:
                        continue
                    ts = _parse_angel_ts(row[0])
                    if interval == "D":
                        ts += _IST_OFFSET_SECONDS
                    all_candles.append({
                        "timestamp": ts,
                        "open": float(row[1] or 0),
                        "high": float(row[2] or 0),
                        "low": float(row[3] or 0),
                        "close": float(row[4] or 0),
                        "volume": int(row[5] or 0),
                        "oi": 0,
                    })
        except Exception as e:
            logger.error("Angel candle chunk %s..%s failed: %s", cur, chunk_end, e)

        cur = chunk_end + timedelta(days=1)
        if cur <= end_dt:
            time.sleep(0.5)

    # F&O: enrich with open interest.
    if exchange in ("NFO", "BFO", "CDS", "MCX"):
        try:
            oi_by_ts = _fetch_oi_history(
                token, api_exch, interval, start_dt, end_dt, auth_token, config
            )
            if oi_by_ts:
                for c in all_candles:
                    c["oi"] = oi_by_ts.get(c["timestamp"], 0)
        except Exception as e:
            logger.error("Angel OI enrichment failed: %s", e)

    # Sort by timestamp ascending and dedupe.
    seen = set()
    unique: list[dict] = []
    for c in sorted(all_candles, key=lambda x: x["timestamp"]):
        if c["timestamp"] not in seen:
            seen.add(c["timestamp"])
            unique.append(c)
    return unique
