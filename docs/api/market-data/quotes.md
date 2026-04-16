# Quotes

Get market quotes including last traded price (LTP), OHLC, and volume for a symbol.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/quotes
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "symbol": "NIFTY28APR2624250CE",
    "exchange": "NFO",
    "ltp": 311.85,
    "open": 288.00,
    "high": 340.50,
    "low": 267.15,
    "close": 297.40,
    "volume": 3254650,
    "oi": 1245800,
    "prev_close": 297.40,
    "change": 14.45,
    "change_percent": 4.86
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
| data | object | Quote data object |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| ltp | number | Last traded price |
| open | number | Day's open price |
| high | number | Day's high price |
| low | number | Day's low price |
| close | number | Previous close price |
| volume | number | Total traded volume |
| oi | number | Open interest (F&O only) |
| prev_close | number | Previous day close |
| change | number | Price change from previous close |
| change_percent | number | Percentage change from previous close |

## Notes

- Works for **equity**, **futures**, and **options** symbols
- **oi** (Open Interest) is only available for F&O instruments
- For equity symbols, oi will be 0
- Use the **symbol** field in OpenBull standard format:
  - Equity: `INFY`
  - Options: `NIFTY28APR2624250CE`
  - Futures: `NIFTY28APR26FUT`
- For multiple symbols in a single call, use the [MultiQuotes](./multiquotes.md) endpoint

---

**Back to**: [API Documentation](../README.md)
