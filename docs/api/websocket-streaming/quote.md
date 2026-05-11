# Quote (WebSocket)

Subscribe to real-time quote updates — LTP plus OHLC, volume, OI, ATP, and total buy/sell quantity — via the OpenBull WebSocket proxy.

> **Full protocol spec:** [`docs/design/websockets-format.md`](../../design/websockets-format.md). This page is the quick reference for Quote-mode subscriptions.

## WebSocket URL

```
Local:        ws://127.0.0.1:8765
Production:   wss://<your-domain>/ws         # nginx terminates TLS, forwards to 127.0.0.1:8765
```

## Mode Hierarchy

`DEPTH ⊇ QUOTE ⊇ LTP`. Subscribing to QUOTE also delivers matching LTP messages for the same symbol. If you only want LTPs, subscribe to LTP. If you want full depth, subscribe to DEPTH — you'll automatically get both QUOTE and LTP too.

## Flow

1. Open the WebSocket.
2. Send `authenticate` with your OpenBull API key.
3. Send `subscribe` with `mode: "QUOTE"`.
4. Receive `market_data` ticks of type `quote` (and `ltp`, per the hierarchy).

## 1. Authenticate

```json
{"action": "authenticate", "api_key": "YOUR_OPENBULL_API_KEY"}
```

Success: `{"type": "auth", "status": "success", "broker": "upstox"}`.

## 2. Subscribe

```json
{
  "action": "subscribe",
  "symbols": [
    {"symbol": "INFY", "exchange": "NSE"},
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO"}
  ],
  "mode": "QUOTE"
}
```

Success:

```json
{
  "type": "subscribe",
  "status": "success",
  "subscriptions": [
    {"symbol": "INFY", "exchange": "NSE", "mode": "QUOTE"},
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO", "mode": "QUOTE"}
  ]
}
```

Limits: 1000 symbols per subscribe call. Mode string is case-insensitive on the wire.

## 3. Quote tick

```json
{
  "type": "market_data",
  "symbol": "INFY",
  "exchange": "NSE",
  "mode": "quote",
  "data": {
    "symbol": "INFY",
    "exchange": "NSE",
    "mode": "quote",
    "ltp": 1508.25,
    "ltt": 1714742222,
    "ltq": 50,
    "cp": 1493.80,
    "change": 14.45,
    "change_percent": 0.97,
    "open": 1495.00,
    "high": 1515.80,
    "low": 1490.50,
    "close": 1493.80,
    "volume": 5678900,
    "oi": 0,
    "average_price": 1502.10,
    "total_buy_quantity": 234567,
    "total_sell_quantity": 312456
  }
}
```

| Field | Type | Description |
|---|---|---|
| `ltp`, `ltt`, `ltq`, `cp`, `change`, `change_percent` | — | Same as LTP tick |
| `open`, `high`, `low`, `close` | number | Day's OHLC (`close` is previous-day close) |
| `volume` | int | Total traded volume for the day |
| `oi` | int | Open interest (non-zero for F&O instruments only) |
| `average_price` | number | Average traded price (ATP) |
| `total_buy_quantity` | int | Aggregate of all buy orders in the book |
| `total_sell_quantity` | int | Aggregate of all sell orders in the book |

QUOTE messages are **not** throttled (only LTP is).

## 4. Unsubscribe

```json
{
  "action": "unsubscribe",
  "symbols": [{"symbol": "INFY", "exchange": "NSE"}],
  "mode": "QUOTE"
}
```

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
            "symbols": [{"symbol": "INFY", "exchange": "NSE"}],
            "mode": "QUOTE",
        }))
    elif msg.get("type") == "market_data" and msg.get("mode") == "quote":
        d = msg["data"]
        print(f"{msg['symbol']} OHLC=({d['open']},{d['high']},{d['low']},{d['ltp']})"
              f" vol={d['volume']} oi={d['oi']}")

ws = websocket.WebSocketApp("ws://127.0.0.1:8765",
                            on_open=on_open, on_message=on_message)
ws.run_forever()
```

## When to use Quote vs LTP vs Depth

| Use case | Mode |
|---|---|
| Strategy P&L, alert rules, simple tickers | LTP |
| Real-time charts, OHLC dashboards, volume / OI analysis, ATP-based execution | **QUOTE** |
| Order-book depth views, smart routing, microstructure work | DEPTH |

## Related

- [LTP WebSocket](./ltp.md) — minimal payload, 50 ms throttle
- [Depth WebSocket](./depth.md) — Quote + 5-level bid/ask book
- Full protocol spec: [`docs/design/websockets-format.md`](../../design/websockets-format.md)

---

**Back to:** [API Documentation](../README.md)
