# Holdings

Get portfolio holdings (delivery/CNC positions carried forward from previous trading days).

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/holdings
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
      "quantity": 10,
      "average_price": 1450.50,
      "ltp": 1508.25,
      "pnl": 577.50,
      "pnl_percent": 3.98
    },
    {
      "symbol": "TCS",
      "exchange": "NSE",
      "quantity": 5,
      "average_price": 3520.00,
      "ltp": 3580.75,
      "pnl": 303.75,
      "pnl_percent": 1.73
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
| data | array | Array of holding objects |

### Holding Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| quantity | number | Number of shares held |
| average_price | number | Average buy price |
| ltp | number | Last traded price |
| pnl | number | Current profit/loss |
| pnl_percent | number | P&L as percentage of investment |

## Notes

- Holdings represent **delivery (CNC)** positions only
- These are shares that have been settled (T+1) and are in your demat account
- Intraday (MIS) and F&O (NRML) positions do not appear in holdings
- **average_price** reflects the cost basis including all historical purchases
- Holdings persist across trading days until sold
- Useful for portfolio tracking, rebalancing decisions, and long-term P&L monitoring

---

**Back to**: [API Documentation](../README.md)
