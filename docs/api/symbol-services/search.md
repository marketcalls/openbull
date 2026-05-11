# Search

Tokenised search across the master-contract table. Returns up to 50 matching instruments — equities, futures, options, and indices — across every exchange the broker plugin has hydrated.

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

Optional `exchange` filter:

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "query": "INFY",
  "exchange": "NSE"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": [
    {
      "symbol": "INFY",
      "brsymbol": "INFY-EQ",
      "name": "INFOSYS LIMITED",
      "exchange": "NSE",
      "brexchange": "NSE",
      "token": "1594",
      "expiry": "",
      "strike": 0.0,
      "lotsize": 1,
      "instrumenttype": "EQ",
      "tick_size": 0.05
    },
    {
      "symbol": "INFY28APR26FUT",
      "brsymbol": "INFY26APR24FUT",
      "name": "INFY",
      "exchange": "NFO",
      "brexchange": "NSE_FO",
      "token": "57124",
      "expiry": "28-APR-26",
      "strike": 0.0,
      "lotsize": 400,
      "instrumenttype": "FUTSTK",
      "tick_size": 0.05
    },
    {
      "symbol": "INFY28APR261600CE",
      "brsymbol": "INFY26APR241600CE",
      "name": "INFY",
      "exchange": "NFO",
      "brexchange": "NSE_FO",
      "token": "59820",
      "expiry": "28-APR-26",
      "strike": 1600.0,
      "lotsize": 400,
      "instrumenttype": "OPTSTK",
      "tick_size": 0.05
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| query | Substring to match against `symbol` or `name` (case-insensitive `ILIKE`) | Mandatory | - |
| exchange | Filter to a specific exchange. One of: `NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `BCD`, `MCX`, `NCDEX`, `NSE_INDEX`, `BSE_INDEX`, `MCX_INDEX` | Optional | All exchanges |

A request missing `query` returns HTTP 400 `{"status": "error", "message": "query is required"}`.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data | array | Up to 50 matching `symtoken` rows. Empty array on no match. |

### Data Array Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | OpenBull canonical symbol |
| brsymbol | string | Broker-native ticker |
| name | string | Underlying for F&O rows; company/index name for cash & index rows |
| exchange | string | OpenBull canonical exchange |
| brexchange | string | Broker-native exchange code |
| token | string | Broker instrument token |
| expiry | string | `DD-MMM-YY` uppercase, empty for cash |
| strike | number | Strike price (`0.0` for non-options) |
| lotsize | integer | Contract size (`1` for cash equities) |
| instrumenttype | string | `EQ`, `FUTSTK`, `OPTSTK`, `FUTIDX`, `OPTIDX`, `INDEX`, etc. |
| tick_size | number | Minimum price increment in rupees |

## Notes

- Backed by an `ILIKE '%query%'` against `symbol` and `name` columns of the `symtoken` master-contract table — substring match, case-insensitive.
- Hard-limited to **50 rows** per call. Narrow the query (e.g. include the expiry month) for sharper results.
- The optional `exchange` filter is the cheapest way to disambiguate when an underlying name collides (e.g. dual-listed equities on NSE + BSE).
- The same backend powers the in-app `/search` page and the underlying-picker combobox in every options tool.

---

**Back to**: [API Documentation](../README.md)
