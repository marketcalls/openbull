# Intervals

Get the list of supported candle intervals for historical data. Returns intervals grouped by category (seconds, minutes, hours, days).

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/intervals
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "seconds": ["1s", "5s", "10s", "15s", "30s"],
    "minutes": ["1m", "2m", "3m", "5m", "10m", "15m", "30m"],
    "hours": ["1h", "2h", "4h"],
    "days": ["D", "W", "M"]
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Intervals grouped by category |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| seconds | array | Available second-level intervals |
| minutes | array | Available minute-level intervals |
| hours | array | Available hour-level intervals |
| days | array | Available day-level intervals (D=daily, W=weekly, M=monthly) |

## Notes

- Available intervals vary by **broker**
- Not all brokers support sub-minute (second-level) intervals
- Use these interval values in the [History](./history.md) endpoint
- Common intervals supported by most brokers: **1m, 5m, 15m, 30m, 1h, D**
- The response structure groups intervals by time granularity for easy parsing

## Related Endpoints

- [History](./history.md) - Get historical OHLCV data using these intervals

---

**Back to**: [API Documentation](../README.md)
