# History

Get historical OHLCV (Open, High, Low, Close, Volume) data for a symbol.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/history
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "INFY",
  "exchange": "NSE",
  "interval": "D",
  "start_date": "2026-04-01",
  "end_date": "2026-04-15"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": [
    {
      "timestamp": "2026-04-01 00:00:00+05:30",
      "open": 1485.00,
      "high": 1498.50,
      "low": 1478.20,
      "close": 1492.30,
      "volume": 4567890
    },
    {
      "timestamp": "2026-04-02 00:00:00+05:30",
      "open": 1493.00,
      "high": 1510.75,
      "low": 1490.10,
      "close": 1507.45,
      "volume": 5234567
    },
    {
      "timestamp": "2026-04-03 00:00:00+05:30",
      "open": 1508.00,
      "high": 1515.90,
      "low": 1499.60,
      "close": 1512.80,
      "volume": 3890123
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |
| interval | Time interval (see below) | Mandatory | - |
| start_date | Start date (YYYY-MM-DD) | Mandatory | - |
| end_date | End date (YYYY-MM-DD) | Mandatory | - |

## Supported Intervals

| Interval | Description |
|----------|-------------|
| 1m | 1 minute |
| 3m | 3 minutes |
| 5m | 5 minutes |
| 10m | 10 minutes |
| 15m | 15 minutes |
| 30m | 30 minutes |
| 1h | 1 hour |
| D | Daily |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | array | Array of OHLCV candles |

### Data Array Fields

| Field | Type | Description |
|-------|------|-------------|
| timestamp | string | Candle timestamp (IST timezone) |
| open | number | Opening price |
| high | number | Highest price |
| low | number | Lowest price |
| close | number | Closing price |
| volume | number | Volume traded |

## Notes

- Historical data availability depends on broker
- Timestamps are in **IST (Indian Standard Time)**
- For intraday intervals, data is typically available for the last 30-90 days
- For daily data, longer history may be available
- Use [Intervals](./intervals.md) endpoint to check available intervals for your broker

## Example: Intraday Data

```json
{
  "apikey": "<your_openbull_apikey>",
  "symbol": "INFY",
  "exchange": "NSE",
  "interval": "5m",
  "start_date": "2026-04-15",
  "end_date": "2026-04-15"
}
```

## Related Endpoints

- [Intervals](./intervals.md) - Get available time intervals

---

**Back to**: [API Documentation](../README.md)
