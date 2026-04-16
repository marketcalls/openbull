# Depth (WebSocket)

Subscribe to real-time market depth (Level 2) updates via WebSocket, including 5 levels of bid/ask data.

## WebSocket URL

```
Local Host   :  ws://127.0.0.1:8765
Custom Host  :  ws://<your-host>:8765
```

## Subscribe to Depth

### Subscribe Message

```json
{
  "action": "subscribe",
  "mode": "depth",
  "instruments": [
    {"exchange": "NSE", "symbol": "INFY"},
    {"exchange": "NFO", "symbol": "NIFTY28APR2624250CE"}
  ]
}
```

### Depth Update Message

```json
{
  "type": "depth",
  "data": {
    "exchange": "NSE",
    "symbol": "INFY",
    "ltp": 1508.25,
    "ltq": 50,
    "open": 1495.00,
    "high": 1515.80,
    "low": 1490.50,
    "close": 1493.80,
    "volume": 5678900,
    "totalbuyqty": 234567,
    "totalsellqty": 312456,
    "depth": {
      "buy": [
        {"price": 1508.20, "quantity": 1250, "orders": 12},
        {"price": 1508.15, "quantity": 890, "orders": 8},
        {"price": 1508.10, "quantity": 2100, "orders": 15},
        {"price": 1508.05, "quantity": 560, "orders": 5},
        {"price": 1508.00, "quantity": 3400, "orders": 22}
      ],
      "sell": [
        {"price": 1508.30, "quantity": 980, "orders": 9},
        {"price": 1508.35, "quantity": 1560, "orders": 11},
        {"price": 1508.40, "quantity": 720, "orders": 6},
        {"price": 1508.45, "quantity": 2340, "orders": 18},
        {"price": 1508.50, "quantity": 1890, "orders": 14}
      ]
    },
    "ltt": "2026-04-15 14:30:22"
  }
}
```

## Unsubscribe from Depth

```json
{
  "action": "unsubscribe",
  "mode": "depth",
  "instruments": [
    {"exchange": "NSE", "symbol": "INFY"}
  ]
}
```

## Python Example

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    if data.get("type") == "depth":
        d = data["data"]
        depth = d["depth"]
        print(f"Depth: {d['symbol']}")
        print(f"  LTP: {d['ltp']}")
        print(f"  Best Bid: {depth['buy'][0]['price']} x {depth['buy'][0]['quantity']}")
        print(f"  Best Ask: {depth['sell'][0]['price']} x {depth['sell'][0]['quantity']}")
        print(f"  Total Buy Qty: {d['totalbuyqty']}")
        print(f"  Total Sell Qty: {d['totalsellqty']}")

def on_open(ws):
    subscribe_msg = {
        "action": "subscribe",
        "mode": "depth",
        "instruments": [
            {"exchange": "NSE", "symbol": "INFY"}
        ]
    }
    ws.send(json.dumps(subscribe_msg))

ws = websocket.WebSocketApp(
    "ws://127.0.0.1:8765",
    on_message=on_message,
    on_open=on_open
)
ws.run_forever()
```

## Message Fields

### Subscribe/Unsubscribe Message

| Field | Type | Description |
|-------|------|-------------|
| action | string | "subscribe" or "unsubscribe" |
| mode | string | "depth" |
| instruments | array | Array of instrument objects |

### Depth Update Message

| Field | Type | Description |
|-------|------|-------------|
| type | string | "depth" |
| data | object | Depth data object |

### Data Object

| Field | Type | Description |
|-------|------|-------------|
| exchange | string | Exchange code |
| symbol | string | Trading symbol |
| ltp | number | Last traded price |
| ltq | number | Last traded quantity |
| open | number | Day's open price |
| high | number | Day's high price |
| low | number | Day's low price |
| close | number | Previous close price |
| volume | number | Total traded volume |
| totalbuyqty | number | Total buy quantity in order book |
| totalsellqty | number | Total sell quantity in order book |
| depth | object | Depth object with buy and sell arrays |
| ltt | string | Last trade time |

### Depth Object

| Field | Type | Description |
|-------|------|-------------|
| buy | array | Top 5 bid levels |
| sell | array | Top 5 ask levels |

### Buy/Sell Level Object

| Field | Type | Description |
|-------|------|-------------|
| price | number | Price level |
| quantity | number | Quantity at this level |
| orders | number | Number of orders at this level |

## Notes

- Depth mode provides **full order book** data (top 5 levels)
- Highest bandwidth consumption among streaming modes
- Updates on every order book change
- The depth data is nested under a **"depth"** key with **"buy"** and **"sell"** arrays
- Use for:
  - Scalping strategies
  - Order flow analysis
  - Liquidity monitoring
  - Smart order routing

## Related Endpoints

- [LTP WebSocket](./ltp.md) - Minimal data, lowest latency
- [Quote WebSocket](./quote.md) - OHLCV data

---

**Back to**: [API Documentation](../README.md)
