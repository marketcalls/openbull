# Depth (WebSocket)

Subscribe to real-time market depth (Level 2) updates — Quote payload plus 5-level bid/ask book — via the OpenBull WebSocket proxy.

> **Full protocol spec:** [`docs/design/websockets-format.md`](../../design/websockets-format.md). This page is the quick reference for Depth-mode subscriptions.

## WebSocket URL

```
Local:        ws://127.0.0.1:8765
Production:   wss://<your-domain>/ws         # nginx terminates TLS, forwards to 127.0.0.1:8765
```

## Mode Hierarchy

`DEPTH ⊇ QUOTE ⊇ LTP`. Subscribing to DEPTH automatically delivers QUOTE and LTP messages for the same symbol — three message types from one subscription.

On the wire, `DEPTH` and `FULL` are synonyms — both result in `mode: "full"` on the published tick (mirroring the Zerodha KiteTicker convention).

## Flow

1. Open the WebSocket.
2. Send `authenticate` with your OpenBull API key.
3. Send `subscribe` with `mode: "DEPTH"` (or `"FULL"`).
4. Receive `market_data` ticks of type `full` (and `quote`, `ltp` per the hierarchy).

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
  "mode": "DEPTH"
}
```

Success:

```json
{
  "type": "subscribe",
  "status": "success",
  "subscriptions": [
    {"symbol": "INFY", "exchange": "NSE", "mode": "DEPTH"},
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO", "mode": "DEPTH"}
  ]
}
```

## 3. Depth tick (mode `"full"`)

```json
{
  "type": "market_data",
  "symbol": "INFY",
  "exchange": "NSE",
  "mode": "full",
  "data": {
    "symbol": "INFY",
    "exchange": "NSE",
    "mode": "full",
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
    "total_sell_quantity": 312456,
    "depth": {
      "buy": [
        {"price": 1508.20, "quantity": 1250, "orders": 12},
        {"price": 1508.15, "quantity":  890, "orders":  8},
        {"price": 1508.10, "quantity": 2100, "orders": 15},
        {"price": 1508.05, "quantity":  560, "orders":  5},
        {"price": 1508.00, "quantity": 3400, "orders": 22}
      ],
      "sell": [
        {"price": 1508.30, "quantity":  980, "orders":  9},
        {"price": 1508.35, "quantity": 1560, "orders": 11},
        {"price": 1508.40, "quantity":  720, "orders":  6},
        {"price": 1508.45, "quantity": 2340, "orders": 18},
        {"price": 1508.50, "quantity": 1890, "orders": 14}
      ]
    }
  }
}
```

**Depth shape notes:**

- Always 5 levels per side. Empty levels are padded with `{price: 0, quantity: 0, orders: 0}`.
- `orders` is **always 0 on Upstox** — the v3 protobuf doesn't carry per-level order counts. Zerodha's binary feed carries real values.
- DEPTH messages are not throttled. The proxy forwards every tick from the broker.

## 4. Unsubscribe

```json
{
  "action": "unsubscribe",
  "symbols": [{"symbol": "INFY", "exchange": "NSE"}],
  "mode": "DEPTH"
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
            "mode": "DEPTH",
        }))
    elif msg.get("type") == "market_data" and msg.get("mode") == "full":
        d = msg["data"]
        bb = d["depth"]["buy"][0]
        ba = d["depth"]["sell"][0]
        print(f"{msg['symbol']} "
              f"bid={bb['price']}x{bb['quantity']} "
              f"ask={ba['price']}x{ba['quantity']}")

ws = websocket.WebSocketApp("ws://127.0.0.1:8765",
                            on_open=on_open, on_message=on_message)
ws.run_forever()
```

## When to use Depth

- Order-book aware execution (price-improvement on aggressive orders).
- Microstructure / order-flow research.
- Smart routing across venues.
- Liquidity monitoring on thinly-traded options strikes.

For plain price-tracking, **prefer LTP or QUOTE** — DEPTH is the highest-bandwidth mode and adds load on the broker session.

## Limits

| Limit | Value |
|---|---|
| Concurrent client connections | 10 |
| Max WS message size (client) | 64 KB |
| Symbols per subscribe call | 1000 |

## Related

- [LTP WebSocket](./ltp.md) — minimal payload, 50 ms throttle
- [Quote WebSocket](./quote.md) — LTP + OHLC + OI
- Full protocol spec: [`docs/design/websockets-format.md`](../../design/websockets-format.md)

---

**Back to:** [API Documentation](../README.md)
