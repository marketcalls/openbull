# TradeBook

Get all executed trades for the current trading day, including fill price and quantity details.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/tradebook
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
      "orderid": "260415000382402",
      "tradeid": "T260415000382402",
      "symbol": "INFY",
      "exchange": "NSE",
      "action": "BUY",
      "quantity": 1,
      "price": 1508.25,
      "product": "MIS",
      "timestamp": "2026-04-15 10:15:32"
    },
    {
      "orderid": "260415000386285",
      "tradeid": "T260415000386285",
      "symbol": "NIFTY28APR2624250CE",
      "exchange": "NFO",
      "action": "BUY",
      "quantity": 65,
      "price": 311.85,
      "product": "MIS",
      "timestamp": "2026-04-15 14:30:22"
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
| data | array | Array of trade objects |

### Trade Object Fields

| Field | Type | Description |
|-------|------|-------------|
| orderid | string | Parent order ID |
| tradeid | string | Unique trade ID |
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| action | string | BUY or SELL |
| quantity | number | Executed quantity |
| price | number | Execution price |
| product | string | MIS, CNC, or NRML |
| timestamp | string | Trade execution timestamp |

## Notes

- Returns only **executed** trades (not pending or cancelled orders)
- A single order may have multiple trade entries if it was filled in parts
- The trade book resets at the start of each trading day
- **tradeid** is unique per trade fill, while **orderid** links back to the parent order
- Use for trade reconciliation, P&L calculation, and audit trails

---

**Back to**: [API Documentation](../README.md)
