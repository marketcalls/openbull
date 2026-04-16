# Expiry

Get available expiry dates for futures or options instruments.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/expiry
```

## Sample API Request (Options)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "NIFTY",
  "exchange": "NFO",
  "instrumenttype": "options"
}
```

## Sample API Response (Options)

```json
{
  "status": "success",
  "message": "Found 18 expiry dates for NIFTY options in NFO",
  "data": [
    "17-APR-26",
    "24-APR-26",
    "28-APR-26",
    "08-MAY-26",
    "15-MAY-26",
    "22-MAY-26",
    "29-MAY-26",
    "25-JUN-26",
    "24-SEP-26",
    "31-DEC-26",
    "24-JUN-27",
    "30-DEC-27",
    "29-JUN-28",
    "28-DEC-28",
    "28-JUN-29",
    "27-DEC-29",
    "25-JUN-30",
    "25-DEC-30"
  ]
}
```

## Sample API Request (Futures)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "NIFTY",
  "exchange": "NFO",
  "instrumenttype": "futures"
}
```

## Sample API Response (Futures)

```json
{
  "status": "success",
  "message": "Found 3 expiry dates for NIFTY futures in NFO",
  "data": [
    "24-APR-26",
    "29-MAY-26",
    "25-JUN-26"
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | Underlying symbol (e.g., NIFTY, BANKNIFTY) | Mandatory | - |
| exchange | Exchange code: NFO, BFO, CDS, BCD, MCX | Mandatory | - |
| instrumenttype | Instrument type: "options" or "futures" | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| message | string | Summary of results |
| data | array | Array of expiry dates in DD-MMM-YY format |

## Notes

- Expiry dates are sorted in **ascending order** (nearest first)
- Weekly expiries are included for index options (NIFTY, BANKNIFTY)
- Monthly expiries extend further into the future
- Options typically have more expiry dates available (18 for NIFTY) compared to futures (3)
- Use this data to populate expiry dropdowns in your application
- Format is **DD-MMM-YY** (e.g., 28-APR-26)

## Use Cases

- **Options trading**: Get available expiries for option selection
- **Futures trading**: Find current and far-month futures
- **Strategy building**: Select appropriate expiry for strategy

---

**Back to**: [API Documentation](../README.md)
