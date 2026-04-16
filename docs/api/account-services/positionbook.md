# PositionBook

Get all current open positions. This endpoint is an alias of `/positions` for OpenAlgo API compatibility.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/positionbook
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
  "data": [
    {
      "symbol": "INFY",
      "exchange": "NSE",
      "product": "MIS",
      "quantity": 1,
      "average_price": 1508.25,
      "ltp": 1512.40,
      "pnl": 4.15,
      "pnl_percent": 0.28
    },
    {
      "symbol": "NIFTY28APR2624250CE",
      "exchange": "NFO",
      "product": "MIS",
      "quantity": 65,
      "average_price": 311.85,
      "ltp": 318.50,
      "pnl": 432.25,
      "pnl_percent": 2.13
    }
  ]
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
| data | array | Array of position objects |

### Position Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| product | string | MIS, CNC, or NRML |
| quantity | number | Net position quantity (positive = long, negative = short) |
| average_price | number | Average entry price |
| ltp | number | Last traded price |
| pnl | number | Current profit/loss |
| pnl_percent | number | P&L as percentage of entry value |

## Notes

- Returns **net positions** for the current trading day
- Positive **quantity** indicates a long position; negative indicates short
- Positions with quantity 0 (squared off) may or may not appear depending on broker
- **pnl** is calculated as: (LTP - average_price) * quantity
- This endpoint is equivalent to `/positions` and exists for **OpenAlgo API compatibility**
- MIS positions are auto-squared off at exchange-defined timings

---

**Back to**: [API Documentation](../README.md)
