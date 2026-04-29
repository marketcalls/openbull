# OpenBull WebSocket Protocol

This doc lives at `docs/design/websockets-format.md` in the openbull repo and
describes the wire protocol implemented by `backend/websocket_proxy/server.py`.

## Overview

OpenBull exposes a broker-agnostic WebSocket endpoint streaming real-time
market data (LTP, Quote, full Depth) from the configured broker (Upstox or
Zerodha). A per-broker adapter connects upstream, normalizes ticks, and
publishes them on an internal ZeroMQ PUB socket; the WS proxy fans those
messages out to authenticated clients per their subscriptions.

The protocol is JSON over WebSocket, single-user (one broker session at a
time), and stateless on reconnect — clients must re-authenticate and
re-subscribe.

## WebSocket URL

Local development:

```
ws://127.0.0.1:8765
```

Host and port are configurable in `.env` via `WEBSOCKET_HOST` (default
`127.0.0.1`) and `WEBSOCKET_PORT` (default `8765`). See `backend/config.py`.

Production behind nginx with TLS:

```
wss://<your-domain>/ws
```

## Authentication

Every session must send an `authenticate` message before any subscribe or
unsubscribe call. The `api_key` is the OpenBull API key issued to the user
(see Apps screen).

Request:

```json
{"action": "authenticate", "api_key": "YOUR_OPENBULL_API_KEY"}
```

Success response:

```json
{"type": "auth", "status": "success", "broker": "upstox"}
```

`broker` is the name of the broker the user is logged into, currently
`"upstox"` or `"zerodha"`.

Error responses:

```json
{"type": "auth", "status": "error", "message": "api_key required"}
{"type": "auth", "status": "error", "message": "Authentication failed"}
{"type": "auth", "status": "error", "message": "Broker connection failed"}
```

Any subscribe/unsubscribe call before successful authentication returns:

```json
{"type": "subscribe", "status": "error", "message": "Not authenticated"}
```

## Data Modes

The `mode` field on subscribe/unsubscribe is a STRING. Values are
case-insensitive on input.

| Mode      | Description                                              |
| --------- | -------------------------------------------------------- |
| `"LTP"`   | Last traded price, prev close, change, change%, ltt, ltq |
| `"QUOTE"` | LTP fields plus OHLC, volume, OI, ATP, total buy/sell qty |
| `"DEPTH"` | Quote fields plus 5-level buy/sell order book            |
| `"FULL"`  | Alias for `"DEPTH"`                                      |

Mode hierarchy: when a client subscribes to `DEPTH`, the proxy also delivers
matching `QUOTE` and `LTP` messages for that symbol. `QUOTE` subscribers also
receive `LTP` messages. `LTP` subscribers receive only `LTP`. This is
implemented in `_zmq_listener` (`server.py`) which iterates `range(1, mode+1)`
across the subscription index.

The server-published `mode` field on `market_data` messages is LOWERCASE:
`"ltp"`, `"quote"`, or `"full"` (note: depth publishes as `"full"`, mirroring
the Zerodha KiteTicker convention).

## Subscribe

Subscribing takes a LIST of `{symbol, exchange}` items and a single `mode`
that applies to all items.

Request:

```json
{
  "action": "subscribe",
  "symbols": [
    {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
    {"symbol": "RELIANCE", "exchange": "NSE"}
  ],
  "mode": "Quote"
}
```

Success response:

```json
{
  "type": "subscribe",
  "status": "success",
  "subscriptions": [
    {"symbol": "NIFTY", "exchange": "NSE_INDEX", "mode": "QUOTE"},
    {"symbol": "RELIANCE", "exchange": "NSE", "mode": "QUOTE"}
  ]
}
```

The `mode` echoed back in `subscriptions` is normalized to uppercase
(`"LTP"`, `"QUOTE"`, `"DEPTH"`).

Error responses:

```json
{"type": "subscribe", "status": "error", "message": "Not authenticated"}
{"type": "subscribe", "status": "error", "message": "symbols must be a list (max 1000)"}
```

Limits: max 1000 items per subscribe call (`MAX_SYMBOLS_PER_SUBSCRIBE`).

## Unsubscribe

Same shape as subscribe, with `action: "unsubscribe"`:

```json
{
  "action": "unsubscribe",
  "symbols": [{"symbol": "RELIANCE", "exchange": "NSE"}],
  "mode": "Quote"
}
```

Response:

```json
{"type": "unsubscribe", "status": "success"}
```

The proxy only forwards an unsubscribe to the broker once the last client for
that `(symbol, exchange, mode)` tuple has dropped — other clients continue to
receive ticks.

## Market Data Format

All ticks share the same envelope:

```json
{
  "type": "market_data",
  "symbol": "NIFTY",
  "exchange": "NSE_INDEX",
  "mode": "ltp",
  "data": { ... }
}
```

`mode` here is lowercase (`"ltp"` / `"quote"` / `"full"`). The per-mode `data`
shapes below are taken from the Upstox and Zerodha adapters; field semantics
are identical across brokers, but values like `oi` may be 0 on cash equities
and `orders` is always 0 on Upstox depth (see Notes).

### LTP (`mode: "ltp"`)

```json
{
  "type": "market_data",
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "mode": "ltp",
  "data": {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "mode": "ltp",
    "ltp": 1424.0,
    "ltt": 1722150645,
    "ltq": 50,
    "cp": 1418.0,
    "change": 6.0,
    "change_percent": 0.4231
  }
}
```

`cp` is the previous-day close. `ltt` is an epoch second timestamp from the
broker (Zerodha falls back to the proxy's wall clock for LTP-only packets).
LTP messages are throttled per `(symbol, exchange)` pair to one every 50 ms
(`LTP_THROTTLE_SEC`); Quote and Depth are not throttled.

### Quote (`mode: "quote"`)

```json
{
  "type": "market_data",
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "mode": "quote",
  "data": {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "mode": "quote",
    "ltp": 1424.0,
    "ltt": 1722150645,
    "ltq": 50,
    "cp": 1418.0,
    "change": 6.0,
    "change_percent": 0.4231,
    "open": 1415.0,
    "high": 1432.5,
    "low": 1408.0,
    "close": 1418.0,
    "volume": 1284530,
    "oi": 0,
    "average_price": 1419.35,
    "total_buy_quantity": 24500,
    "total_sell_quantity": 21800
  }
}
```

`oi` is non-zero for F&O instruments (`NFO`, `BFO`, `MCX`). `close` is the
previous-day close; current-session close prints into `ltp`.

### Depth (`mode: "full"`)

Subscribe with `"mode": "Depth"` (or `"Full"`). The `data` payload is the
Quote payload plus a `depth` field with five buy and five sell levels:

```json
{
  "type": "market_data",
  "symbol": "RELIANCE", "exchange": "NSE", "mode": "full",
  "data": {
    "symbol": "RELIANCE", "exchange": "NSE", "mode": "full",
    "ltp": 1424.0, "ltt": 1722150645, "ltq": 50, "cp": 1418.0,
    "change": 6.0, "change_percent": 0.4231,
    "open": 1415.0, "high": 1432.5, "low": 1408.0, "close": 1418.0,
    "volume": 1284530, "oi": 0, "average_price": 1419.35,
    "total_buy_quantity": 24500, "total_sell_quantity": 21800,
    "depth": {
      "buy": [
        {"price": 1423.9, "quantity": 50, "orders": 3},
        {"price": 1423.5, "quantity": 35, "orders": 2},
        {"price": 1423.0, "quantity": 42, "orders": 4},
        {"price": 1422.5, "quantity": 28, "orders": 1},
        {"price": 1422.0, "quantity": 33, "orders": 5}
      ],
      "sell": [
        {"price": 1424.1, "quantity": 47, "orders": 2},
        {"price": 1424.5, "quantity": 39, "orders": 3},
        {"price": 1425.0, "quantity": 41, "orders": 4},
        {"price": 1425.5, "quantity": 32, "orders": 2},
        {"price": 1426.0, "quantity": 30, "orders": 1}
      ]
    }
  }
}
```

Notes:

- Depth is fixed at 5 levels for both brokers. Empty levels pad with
  `{price: 0, quantity: 0, orders: 0}`.
- `orders` is always `0` on Upstox — the v3 protobuf carries only bid/ask
  price and quantity. Zerodha's binary feed carries real per-level orders.

## Symbol Format

OpenBull uses simple symbol/exchange pairs; no broker-specific token codes
appear on the wire. See `docs/design/symbol-format.md` for the full table.
Examples: `RELIANCE` on `NSE`, `NIFTY` / `BANKNIFTY` on `NSE_INDEX`, `SENSEX`
on `BSE_INDEX`, `NIFTY28OCT2525950CE` on `NFO`, `CRUDEOIL19NOV25FUT` on `MCX`.

Recognized exchanges: `NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `BCD`, `MCX`,
`NSE_INDEX`, `BSE_INDEX`, `MCX_INDEX`.

## Errors

Top-level errors (not tied to an action) and action-scoped errors:

```json
{"type": "error", "message": "Invalid JSON"}
{"type": "error", "message": "Unknown action: foo"}
{"type": "error", "message": "Max connections reached"}
{"type": "auth", "status": "error", "message": "Authentication failed"}
{"type": "subscribe", "status": "error", "message": "Not authenticated"}
{"type": "subscribe", "status": "error", "message": "symbols must be a list (max 1000)"}
{"type": "unsubscribe", "status": "error", "message": "Not authenticated"}
```

`Max connections reached` is sent immediately before the server closes the
socket — OpenBull caps concurrent clients at 10 (`MAX_WS_CONNECTIONS`).

## Heartbeat and Reconnection

- Server sends WebSocket-level pings every 30 s with a 10 s timeout
  (`websockets.serve(ping_interval=30, ping_timeout=10)`). Standards-compliant
  WS clients pong automatically — no app-level heartbeat needed.
- On reconnect, clients MUST re-authenticate and re-subscribe. OpenBull does
  not restore prior subscriptions automatically.
- The broker session is shared across clients: the first authenticated client
  triggers broker connect, and the adapter stays up until proxy shutdown.

## Limits

| Limit                          | Value     | Source                          |
| ------------------------------ | --------- | ------------------------------- |
| Concurrent client connections  | 10        | `MAX_WS_CONNECTIONS`            |
| Max WS message size (client)   | 64 KB     | `MAX_MESSAGE_SIZE`              |
| Symbols per subscribe call     | 1000      | `MAX_SYMBOLS_PER_SUBSCRIBE`     |
| LTP fanout throttle            | 50 ms     | `LTP_THROTTLE_SEC` (per symbol) |
| Ping interval / timeout        | 30 s / 10 s | `websockets.serve` config     |

## Security

- All sessions require API-key authentication; subscribe/unsubscribe before
  auth is rejected.
- Use `wss://` (TLS) in production. Behind nginx, terminate TLS at the proxy
  and forward to `127.0.0.1:8765`.
- Bind `WEBSOCKET_HOST` to `127.0.0.1` when the proxy is fronted by a reverse
  proxy; only bind to `0.0.0.0` to expose `ws://` directly.
- Malformed JSON, unknown actions, oversized symbol lists, and unauthenticated
  actions are rejected with explicit error messages and never reach the
  adapter.
