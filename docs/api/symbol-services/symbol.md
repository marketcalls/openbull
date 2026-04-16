# Symbol

Get detailed symbol information including instrument type, lot size, tick size, and other metadata for a specific trading symbol.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/symbol
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
    "token": "54650",
    "name": "NIFTY",
    "instrument_type": "OPTIDX",
    "strike": 24250.0,
    "option_type": "CE",
    "expiry": "2026-04-28",
    "lotsize": 65,
    "tick_size": 0.05,
    "segment": "NFO-OPT"
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
| data | object | Symbol metadata object |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| token | string | Broker-specific instrument token |
| name | string | Underlying name (e.g., NIFTY, INFY) |
| instrument_type | string | Instrument type (EQ, OPTIDX, FUTIDX, OPTSTK, etc.) |
| strike | number | Strike price (options only) |
| option_type | string | CE or PE (options only) |
| expiry | string | Expiry date in YYYY-MM-DD format (F&O only) |
| lotsize | number | Lot size for F&O instruments |
| tick_size | number | Minimum price movement |
| segment | string | Market segment (NSE-EQ, NFO-OPT, NFO-FUT, etc.) |

## Notes

- For **equity** symbols, fields like strike, option_type, and expiry will not be present
- The **token** is the broker-specific instrument identifier used internally for order routing
- **lotsize** for equity is typically 1
- Use this endpoint to validate symbols before placing orders
- The **instrument_type** field helps distinguish between equity, futures, and options

---

**Back to**: [API Documentation](../README.md)
