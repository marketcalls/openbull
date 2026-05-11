# LTP (WebSocket)

Subscribe to real-time Last Traded Price (LTP) updates via the OpenBull WebSocket proxy.

> **Full protocol spec:** [`docs/design/websockets-format.md`](../../design/websockets-format.md). This page is a quick reference for LTP-mode subscriptions; the spec is the source of truth for envelope, errors, and limits.

## WebSocket URL

```
Local:        ws://127.0.0.1:8765
Production:   wss://<your-domain>/ws         # nginx terminates TLS, forwards to 127.0.0.1:8765
```

## Flow

1. Open the WebSocket.
2. Send an **`authenticate`** message with your OpenBull API key.
3. After the auth success response, send a **`subscribe`** message with `mode: "LTP"` and the symbols you want.
4. Receive `market_data` messages for each tick (per-symbol 50 ms throttle).

## 1. Authenticate

```json
{"action": "authenticate", "api_key": "YOUR_OPENBULL_API_KEY"}
```

Success response:

```json
{"type": "auth", "status": "success", "broker": "upstox"}
```

`broker` is one of `"upstox"`, `"zerodha"`, `"angel"`, `"dhan"`, `"fyers"`. Any subscribe call before successful authentication returns `{"type": "subscribe", "status": "error", "message": "Not authenticated"}`.

## 2. Subscribe

```json
{
  "action": "subscribe",
  "symbols": [
    {"symbol": "INFY", "exchange": "NSE"},
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO"}
  ],
  "mode": "LTP"
}
```

Success response:

```json
{
  "type": "subscribe",
  "status": "success",
  "subscriptions": [
    {"symbol": "INFY", "exchange": "NSE", "mode": "LTP"},
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO", "mode": "LTP"}
  ]
}
```

Mode is case-insensitive on the wire (`"LTP"` / `"ltp"` / `"Ltp"`). Limit: 1000 symbols per subscribe call.

## 3. LTP tick

Every matching trade emits a `market_data` envelope (`mode` lowercased on the response):

```json
{
  "type": "market_data",
  "symbol": "INFY",
  "exchange": "NSE",
  "mode": "ltp",
  "data": {
    "symbol": "INFY",
    "exchange": "NSE",
    "mode": "ltp",
    "ltp": 1508.25,
    "ltt": 1714742222,
    "ltq": 50,
    "cp": 1493.80,
    "change": 14.45,
    "change_percent": 0.97
  }
}
```

LTP messages are throttled per `(symbol, exchange)` pair to one every 50 ms.

| Field | Type | Description |
|---|---|---|
| `ltp` | number | Last traded price |
| `ltt` | int | Last trade time (Unix epoch seconds) |
| `ltq` | int | Last traded quantity |
| `cp` | number | Previous-day close |
| `change` | number | `ltp - cp` |
| `change_percent` | number | `change / cp * 100` |

## 4. Unsubscribe

```json
{
  "action": "unsubscribe",
  "symbols": [{"symbol": "INFY", "exchange": "NSE"}],
  "mode": "LTP"
}
```

The proxy only forwards an unsubscribe to the broker when the last client for that `(symbol, exchange, mode)` tuple drops.

## Python Example

```python
import json
import websocket

def on_open(ws):
    ws.send(json.dumps({"action": "authenticate",
                        "api_key": "YOUR_OPENBULL_API_KEY"}))

def on_message(ws, raw):
    msg = json.loads(raw)
    if msg.get("type") == "auth" and msg.get("status") == "success":
        ws.send(json.dumps({
            "action": "subscribe",
            "symbols": [
                {"symbol": "INFY", "exchange": "NSE"},
                {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO"},
            ],
            "mode": "LTP",
        }))
    elif msg.get("type") == "market_data" and msg.get("mode") == "ltp":
        d = msg["data"]
        print(f"{msg['symbol']}: ltp={d['ltp']} change={d['change']:+.2f}")

ws = websocket.WebSocketApp("ws://127.0.0.1:8765",
                            on_open=on_open,
                            on_message=on_message)
ws.run_forever()
```

## Limits

| Limit | Value |
|---|---|
| Concurrent client connections | 10 |
| Max WS message size (client) | 64 KB |
| Symbols per subscribe call | 1000 |
| LTP fanout throttle | 50 ms per `(symbol, exchange)` |

See [`docs/design/websockets-format.md`](../../design/websockets-format.md#limits) for the full table and rationale.

## Related

- [Quote WebSocket](./quote.md) — LTP + OHLC + OI + total buy/sell qty
- [Depth WebSocket](./depth.md) — Quote + 5-level bid/ask book
- Full protocol spec: [`docs/design/websockets-format.md`](../../design/websockets-format.md)

---

**Back to:** [API Documentation](../README.md)
