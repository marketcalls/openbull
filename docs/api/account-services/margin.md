# Margin

Calculate margin requirement for a basket of positions. Useful for pre-trade margin checks, especially for multi-leg options strategies.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/margin
```

## Sample API Request (Iron Condor)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "positions": [
    {
      "symbol": "NIFTY28APR2624550CE",
      "exchange": "NFO",
      "action": "BUY",
      "product": "NRML",
      "pricetype": "MARKET",
      "quantity": "65"
    },
    {
      "symbol": "NIFTY28APR2623950PE",
      "exchange": "NFO",
      "action": "BUY",
      "product": "NRML",
      "pricetype": "MARKET",
      "quantity": "65"
    },
    {
      "symbol": "NIFTY28APR2624450CE",
      "exchange": "NFO",
      "action": "SELL",
      "product": "NRML",
      "pricetype": "MARKET",
      "quantity": "65"
    },
    {
      "symbol": "NIFTY28APR2624050PE",
      "exchange": "NFO",
      "action": "SELL",
      "product": "NRML",
      "pricetype": "MARKET",
      "quantity": "65"
    }
  ]
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "total_margin_required": 42850.75,
    "span_margin": 28500.50,
    "exposure_margin": 14350.25,
    "margin_benefit": 85420.30
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| positions | Array of position objects (max 50) | Mandatory | - |

### Position Object Fields

| Field | Description | Mandatory/Optional | Default Value |
|-------|-------------|-------------------|---------------|
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, NFO, BFO, etc. | Mandatory | - |
| action | BUY or SELL | Mandatory | - |
| quantity | Position quantity | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Mandatory | - |
| pricetype | Price type: MARKET, LIMIT | Mandatory | - |
| price | Order price (for LIMIT) | Optional | 0 |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Margin calculation results |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| total_margin_required | number | Total margin required for the basket |
| span_margin | number | SPAN margin component |
| exposure_margin | number | Exposure margin component |
| margin_benefit | number | Margin benefit from hedged positions |

## Notes

- Maximum **50 positions** per request
- Margin calculation includes **hedging benefits** for spread positions
- An Iron Condor receives significant **margin_benefit** because the bought legs hedge the sold legs
- Actual margin may vary slightly due to real-time price changes
- Not all brokers support margin calculation API
- Use this for **pre-trade validation** to check if sufficient margin exists

## Use Cases

- **Pre-trade check**: Verify margin before placing orders
- **Strategy planning**: Calculate margin for option strategies
- **Risk management**: Understand margin exposure
- **Strategy comparison**: Compare margin requirements across different strategies

---

**Back to**: [API Documentation](../README.md)
