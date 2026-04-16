# OpenPosition

Get the current net open position quantity for a specific symbol. Useful for checking position before placing smart orders or managing risk.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/openposition
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "INFY",
  "exchange": "NSE",
  "product": "MIS",
  "strategy": "Python"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "symbol": "INFY",
    "exchange": "NSE",
    "product": "MIS",
    "quantity": 1,
    "strategy": "Python"
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Mandatory | - |
| strategy | Strategy identifier | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Position data object |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| product | string | Product type |
| quantity | number | Net open position quantity (positive = long, negative = short, 0 = flat) |
| strategy | string | Strategy identifier |

## Notes

- Returns the **net quantity** for the specified symbol, exchange, and product combination
- Positive quantity indicates a **long** position
- Negative quantity indicates a **short** position
- Zero quantity means the position is **flat** (no open position)
- When **strategy** is provided, only positions matching that strategy tag are considered
- This endpoint is commonly used before PlaceSmartOrder to verify current position state

---

**Back to**: [API Documentation](../README.md)
