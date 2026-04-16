# OrderBook

Get all orders placed during the current trading day, including their status, fill details, and timestamps.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/orderbook
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
      "symbol": "INFY",
      "exchange": "NSE",
      "action": "BUY",
      "quantity": 1,
      "price": 0,
      "trigger_price": 0,
      "pricetype": "MARKET",
      "product": "MIS",
      "order_status": "complete",
      "filled_quantity": 1,
      "pending_quantity": 0,
      "average_price": 1508.25,
      "timestamp": "2026-04-15 10:15:32"
    },
    {
      "orderid": "260415000386285",
      "symbol": "NIFTY28APR2624250CE",
      "exchange": "NFO",
      "action": "BUY",
      "quantity": 65,
      "price": 0,
      "trigger_price": 0,
      "pricetype": "MARKET",
      "product": "MIS",
      "order_status": "complete",
      "filled_quantity": 65,
      "pending_quantity": 0,
      "average_price": 311.85,
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
| data | array | Array of order objects |

### Order Object Fields

| Field | Type | Description |
|-------|------|-------------|
| orderid | string | Order ID |
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| action | string | BUY or SELL |
| quantity | number | Order quantity |
| price | number | Order price |
| trigger_price | number | Trigger price |
| pricetype | string | MARKET, LIMIT, SL, or SL-M |
| product | string | MIS, CNC, or NRML |
| order_status | string | Current status (open, complete, cancelled, rejected) |
| filled_quantity | number | Quantity filled |
| pending_quantity | number | Quantity pending |
| average_price | number | Average fill price |
| timestamp | string | Order placement timestamp |

## Notes

- Returns **all orders** for the current trading day
- Includes orders in all states: open, complete, cancelled, rejected
- The order book resets at the start of each trading day
- Use [OrderStatus](../order-information/orderstatus.md) to query a specific order by ID
- Useful for building order monitoring dashboards and reconciliation

---

**Back to**: [API Documentation](../README.md)
