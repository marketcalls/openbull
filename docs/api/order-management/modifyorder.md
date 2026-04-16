# ModifyOrder

Modify an existing pending order. You can change the quantity, price, price type, trigger price, or disclosed quantity of an open order.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/modifyorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "orderid": "260415000382402",
  "symbol": "INFY",
  "action": "BUY",
  "exchange": "NSE",
  "product": "MIS",
  "quantity": "2",
  "price": "1520",
  "pricetype": "LIMIT",
  "trigger_price": "0",
  "disclosed_quantity": "0"
}
```

## Sample API Response

```json
{
  "orderid": "260415000382402",
  "status": "success"
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| orderid | Order ID to modify | Mandatory | - |
| quantity | New order quantity | Optional | - |
| price | New order price | Optional | - |
| pricetype | New price type: MARKET, LIMIT, SL, SL-M | Optional | - |
| trigger_price | New trigger price | Optional | 0 |
| disclosed_quantity | New disclosed quantity | Optional | 0 |
| symbol | Trading symbol (for OpenAlgo parity) | Optional | - |
| action | Order action: BUY or SELL (for OpenAlgo parity) | Optional | - |
| exchange | Exchange code (for OpenAlgo parity) | Optional | - |
| product | Product type (for OpenAlgo parity) | Optional | - |
| strategy | Strategy identifier (for OpenAlgo parity) | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| orderid | string | Modified order ID (on success) |
| message | string | Error message (on error) |

## Notes

- Only **pending orders** (open/trigger pending) can be modified
- You can modify one or more fields in a single request
- The **orderid** is the only mandatory field besides apikey
- Fields like symbol, action, exchange, product, and strategy are accepted for **OpenAlgo API compatibility** but the order identity is determined by orderid
- To change a MARKET order to LIMIT, provide both **pricetype** and **price**
- Modification of already executed or cancelled orders will return an error

---

**Back to**: [API Documentation](../README.md)
