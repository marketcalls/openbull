# Search

Search for trading instruments by name or keyword. Returns a list of matching symbols across exchanges.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/search
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "query": "INFY"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": [
    {
      "symbol": "INFY",
      "exchange": "NSE",
      "name": "INFOSYS LIMITED",
      "instrument_type": "EQ",
      "lotsize": 1
    },
    {
      "symbol": "INFY",
      "exchange": "BSE",
      "name": "INFOSYS LIMITED",
      "instrument_type": "EQ",
      "lotsize": 1
    },
    {
      "symbol": "INFY28APR26FUT",
      "exchange": "NFO",
      "name": "INFY",
      "instrument_type": "FUTSTK",
      "lotsize": 300
    },
    {
      "symbol": "INFY28APR2624250CE",
      "exchange": "NFO",
      "name": "INFY",
      "instrument_type": "OPTSTK",
      "lotsize": 300
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| query | Search term (symbol name or keyword) | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | array | Array of matching instruments |

### Data Array Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| exchange | string | Exchange code |
| name | string | Instrument name / company name |
| instrument_type | string | Instrument type (EQ, FUTSTK, OPTSTK, FUTIDX, OPTIDX) |
| lotsize | number | Lot size |

## Notes

- Search is **case-insensitive**
- Returns matches across all exchanges (NSE, BSE, NFO, BFO, etc.)
- Results include equity, futures, and options instruments matching the query
- Use this endpoint for **symbol discovery** and building autocomplete functionality
- The number of results returned may be limited; use specific queries for best results

---

**Back to**: [API Documentation](../README.md)
