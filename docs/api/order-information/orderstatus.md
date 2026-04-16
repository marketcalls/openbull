# OrderStatus

Get the current status and details of a specific order by its order ID.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/orderstatus
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "orderid": "260415000386285",
  "strategy": "Python"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
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
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| orderid | Order ID to query | Mandatory | - |
| strategy | Strategy identifier | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Order details object |

### Data Object Fields

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
| order_status | string | Current order status |
| filled_quantity | number | Quantity filled so far |
| pending_quantity | number | Remaining quantity |
| average_price | number | Average fill price |
| timestamp | string | Order timestamp |

## Order Status Values

| Status | Description |
|--------|-------------|
| open | Order is pending in the order book |
| complete | Order fully executed |
| cancelled | Order was cancelled |
| rejected | Order was rejected by exchange/broker |
| trigger pending | Stop-loss order waiting for trigger |

## Notes

- The **strategy** field is optional and used for tracking/filtering purposes
- **average_price** reflects the weighted average price for partial fills
- For MARKET orders, the **price** field will be 0 while **average_price** shows actual execution price
- **filled_quantity** and **pending_quantity** together equal the total order **quantity**

---

**Back to**: [API Documentation](../README.md)
