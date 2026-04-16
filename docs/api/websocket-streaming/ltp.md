# LTP (WebSocket)

Subscribe to real-time Last Traded Price (LTP) updates via WebSocket.

## WebSocket URL

```
Local Host   :  ws://127.0.0.1:8765
Custom Host  :  ws://<your-host>:8765
```

## Subscribe to LTP

### Subscribe Message

```json
{
  "action": "subscribe",
  "mode": "ltp",
  "instruments": [
    {"exchange": "NSE", "symbol": "INFY"},
    {"exchange": "NFO", "symbol": "NIFTY28APR2624250CE"}
  ]
}
```

### LTP Update Message

```json
{
  "type": "ltp",
  "data": {
    "exchange": "NSE",
    "symbol": "INFY",
    "ltp": 1508.25,
    "ltt": "2026-04-15 14:30:22",
    "change": 14.45
  }
}
```

## Unsubscribe from LTP

```json
{
  "action": "unsubscribe",
  "mode": "ltp",
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
    if data.get("type") == "ltp":
        d = data["data"]
        print(f"LTP: {d['symbol']} = {d['ltp']} (change: {d['change']})")

def on_open(ws):
    subscribe_msg = {
        "action": "subscribe",
        "mode": "ltp",
        "instruments": [
            {"exchange": "NSE", "symbol": "INFY"},
            {"exchange": "NFO", "symbol": "NIFTY28APR2624250CE"}
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
| mode | string | "ltp" |
| instruments | array | Array of instrument objects |

### Instrument Object

| Field | Type | Description |
|-------|------|-------------|
| exchange | string | Exchange code (NSE, BSE, NFO, etc.) |
| symbol | string | Trading symbol |

### LTP Update Message

| Field | Type | Description |
|-------|------|-------------|
| type | string | "ltp" |
| data | object | LTP data object |

### Data Object

| Field | Type | Description |
|-------|------|-------------|
| exchange | string | Exchange code |
| symbol | string | Trading symbol |
| ltp | number | Last traded price |
| ltt | string | Last trade time |
| change | number | Price change from previous close |

## Notes

- LTP mode provides **minimal data** for lowest latency
- Updates are pushed **on every tick** (each trade)
- Subscribe to multiple symbols in a single message
- Use for:
  - Price displays
  - Trigger-based alerts
  - Simple strategy signals

## Related Endpoints

- [Quote WebSocket](./quote.md) - More data including OHLCV
- [Depth WebSocket](./depth.md) - Full market depth

---

**Back to**: [API Documentation](../README.md)
