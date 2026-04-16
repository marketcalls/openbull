# Depth

Get 5-level market depth (Level 2) data for a symbol, including bid and ask price/quantity at each level.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/depth
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "INFY",
  "exchange": "NSE"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "symbol": "INFY",
    "exchange": "NSE",
    "ltp": 1508.25,
    "totalbuyqty": 234567,
    "totalsellqty": 312456,
    "bids": [
      {"price": 1508.20, "quantity": 1250, "orders": 12},
      {"price": 1508.15, "quantity": 890, "orders": 8},
      {"price": 1508.10, "quantity": 2100, "orders": 15},
      {"price": 1508.05, "quantity": 560, "orders": 5},
      {"price": 1508.00, "quantity": 3400, "orders": 22}
    ],
    "asks": [
      {"price": 1508.30, "quantity": 980, "orders": 9},
      {"price": 1508.35, "quantity": 1560, "orders": 11},
      {"price": 1508.40, "quantity": 720, "orders": 6},
      {"price": 1508.45, "quantity": 2340, "orders": 18},
      {"price": 1508.50, "quantity": 1890, "orders": 14}
    ]
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Depth data object |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| ltp | number | Last traded price |
| totalbuyqty | number | Total buy quantity in order book |
| totalsellqty | number | Total sell quantity in order book |
| bids | array | Top 5 bid (buy) levels |
| asks | array | Top 5 ask (sell) levels |

### Bid/Ask Object Fields

| Field | Type | Description |
|-------|------|-------------|
| price | number | Price level |
| quantity | number | Quantity at this level |
| orders | number | Number of orders at this level |

## Notes

- Returns **5 levels** of bid and ask data
- **bids** are sorted by price descending (best bid first)
- **asks** are sorted by price ascending (best ask first)
- The spread (difference between best ask and best bid) indicates liquidity
- Use for:
  - **Scalping strategies**: Monitor order flow
  - **Liquidity analysis**: Check depth before large orders
  - **Smart order routing**: Place limit orders at optimal levels
- **totalbuyqty** and **totalsellqty** represent the total quantities across all price levels

---

**Back to**: [API Documentation](../README.md)
