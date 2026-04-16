# Quote (WebSocket)

Subscribe to real-time quote updates via WebSocket, including LTP, OHLC, volume, and open interest.

## WebSocket URL

```
Local Host   :  ws://127.0.0.1:8765
Custom Host  :  ws://<your-host>:8765
```

## Subscribe to Quote

### Subscribe Message

```json
{
  "action": "subscribe",
  "mode": "quote",
  "instruments": [
    {"exchange": "NSE", "symbol": "INFY"},
    {"exchange": "NFO", "symbol": "NIFTY28APR2624250CE"}
  ]
}
```

### Quote Update Message

```json
{
  "type": "quote",
  "data": {
    "exchange": "NSE",
    "symbol": "INFY",
    "ltp": 1508.25,
    "open": 1495.00,
    "high": 1515.80,
    "low": 1490.50,
    "close": 1493.80,
    "volume": 5678900,
    "oi": 0,
    "change": 14.45,
    "change_percent": 0.97,
    "prev_close": 1493.80,
    "ltt": "2026-04-15 14:30:22",
    "ltq": 50,
    "totalbuyqty": 234567,
    "totalsellqty": 312456
  }
}
```

## Unsubscribe from Quote

```json
{
  "action": "unsubscribe",
  "mode": "quote",
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
    if data.get("type") == "quote":
        d = data["data"]
        print(f"Quote: {d['symbol']} LTP={d['ltp']} "
              f"O={d['open']} H={d['high']} L={d['low']} "
              f"Vol={d['volume']} OI={d['oi']}")

def on_open(ws):
    subscribe_msg = {
        "action": "subscribe",
        "mode": "quote",
        "instruments": [
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
| mode | string | "quote" |
| instruments | array | Array of instrument objects |

### Instrument Object

| Field | Type | Description |
|-------|------|-------------|
| exchange | string | Exchange code (NSE, BSE, NFO, etc.) |
| symbol | string | Trading symbol |

### Quote Update Message

| Field | Type | Description |
|-------|------|-------------|
| type | string | "quote" |
| data | object | Quote data object |

### Data Object

| Field | Type | Description |
|-------|------|-------------|
| exchange | string | Exchange code |
| symbol | string | Trading symbol |
| ltp | number | Last traded price |
| open | number | Day's open price |
| high | number | Day's high price |
| low | number | Day's low price |
| close | number | Previous close price |
| volume | number | Total traded volume |
| oi | number | Open interest (F&O only, 0 for equity) |
| change | number | Price change from previous close |
| change_percent | number | Percentage change |
| prev_close | number | Previous day close |
| ltt | string | Last trade time |
| ltq | number | Last traded quantity |
| totalbuyqty | number | Total buy quantity in order book |
| totalsellqty | number | Total sell quantity in order book |

## Notes

- Quote mode provides **OHLCV and market summary** data
- More data than LTP mode but less than Depth mode
- Updates on every tick with full OHLC recalculation
- Use for:
  - Real-time charting
  - Strategy monitoring with OHLC context
  - Portfolio dashboards
  - Volume and OI analysis

## Related Endpoints

- [LTP WebSocket](./ltp.md) - Minimal data, lowest latency
- [Depth WebSocket](./depth.md) - Full market depth

---

**Back to**: [API Documentation](../README.md)
