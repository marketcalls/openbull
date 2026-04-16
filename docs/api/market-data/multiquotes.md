# MultiQuotes

Get market quotes for multiple symbols in a single API call.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/multiquotes
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbols": [
    {"symbol": "NIFTY28APR2624250CE", "exchange": "NFO"},
    {"symbol": "NIFTY28APR2624250PE", "exchange": "NFO"}
  ]
}
```

## Sample API Response

```json
{
  "status": "success",
  "results": [
    {
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
    },
    {
      "symbol": "NIFTY28APR2624250PE",
      "exchange": "NFO",
      "ltp": 285.20,
      "open": 310.00,
      "high": 335.60,
      "low": 262.45,
      "close": 305.80,
      "volume": 2876400,
      "oi": 1123500,
      "prev_close": 305.80,
      "change": -20.60,
      "change_percent": -6.73
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbols | Array of symbol objects | Mandatory | - |

### Symbol Object Fields

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| results | array | Array of quote objects |

### Quote Object Fields

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

- The response uses the **"results"** key (not "data") to return the array of quotes
- Symbols that fail to resolve return an error entry in the results array
- Use this endpoint to fetch quotes for option chain legs, basket monitoring, or portfolio tracking
- More efficient than making multiple individual [Quotes](./quotes.md) calls
- Maximum symbols per request depends on broker limits

---

**Back to**: [API Documentation](../README.md)
