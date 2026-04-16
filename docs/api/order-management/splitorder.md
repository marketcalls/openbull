# SplitOrder

Split a large order into multiple smaller orders to reduce market impact or comply with freeze quantity limits.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/splitorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "symbol": "YESBANK",
  "exchange": "NSE",
  "action": "BUY",
  "quantity": "105",
  "splitsize": "20",
  "pricetype": "MARKET",
  "product": "MIS"
}
```

## Sample API Response

```json
{
  "status": "success",
  "split_size": 20,
  "total_quantity": 105,
  "results": [
    {
      "order_num": 1,
      "orderid": "260415000382410",
      "quantity": 20,
      "status": "success"
    },
    {
      "order_num": 2,
      "orderid": "260415000382411",
      "quantity": 20,
      "status": "success"
    },
    {
      "order_num": 3,
      "orderid": "260415000382412",
      "quantity": 20,
      "status": "success"
    },
    {
      "order_num": 4,
      "orderid": "260415000382413",
      "quantity": 20,
      "status": "success"
    },
    {
      "order_num": 5,
      "orderid": "260415000382414",
      "quantity": 20,
      "status": "success"
    },
    {
      "order_num": 6,
      "orderid": "260415000382415",
      "quantity": 5,
      "status": "success"
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier | Optional | - |
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |
| action | Order action: BUY or SELL | Mandatory | - |
| quantity | Total quantity to split | Mandatory | - |
| splitsize | Size of each split order | Mandatory | - |
| pricetype | Price type: MARKET, LIMIT, SL, SL-M | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Mandatory | - |
| price | Order price (for LIMIT orders) | Optional | 0 |
| trigger_price | Trigger price (for SL orders) | Optional | 0 |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| split_size | number | Size used for splitting |
| total_quantity | number | Total quantity processed |
| results | array | Array of individual order results |

### Results Array Fields

| Field | Type | Description |
|-------|------|-------------|
| order_num | number | Order sequence number (1, 2, 3...) |
| orderid | string | Order ID from broker |
| quantity | number | Quantity for this order |
| status | string | "success" or "error" |
| message | string | Error message (on failure) |

## How Split Orders Work

For a total quantity of 105 with splitsize of 20:

```
Order 1: 20 units
Order 2: 20 units
Order 3: 20 units
Order 4: 20 units
Order 5: 20 units
Order 6: 5 units (remainder)
-----------------
Total: 105 units
```

## Notes

- **Maximum 100 orders** per split request
- The last order contains the **remainder** (quantity % splitsize)
- Orders are placed **sequentially** with a small delay between them
- Use for:
  - **Large F&O orders**: Splitting to stay within freeze quantity limits
  - **Reducing market impact**: Spreading execution over multiple orders
  - **TWAP strategies**: Time-weighted average price execution
- If splitsize is larger than quantity, a single order is placed
- All split orders share the same price type and price

## Freeze Quantity Reference

Common freeze quantities for popular F&O contracts:

| Contract | Freeze Quantity |
|----------|-----------------|
| NIFTY | 1800 lots |
| BANKNIFTY | 900 lots |
| FINNIFTY | 1200 lots |
| Stock Options | Varies by stock |

---

**Back to**: [API Documentation](../README.md)
