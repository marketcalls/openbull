# Symbol

Look up the master-contract row for a specific OpenBull symbol — instrument type, lot size, tick size, broker-native ticker, broker-native exchange, and the broker instrument token.

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
    "brsymbol": "NIFTY26APR2424250CE",
    "name": "NIFTY",
    "exchange": "NFO",
    "brexchange": "NSE_FO",
    "token": "54650",
    "expiry": "28-APR-26",
    "strike": 24250.0,
    "lotsize": 75,
    "instrumenttype": "OPTIDX",
    "tick_size": 0.05
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | OpenBull canonical trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCDEX, NSE_INDEX, BSE_INDEX, MCX_INDEX | Mandatory | - |

A request missing `symbol` or `exchange` returns HTTP 400 `{"status": "error", "message": "symbol and exchange are required"}`. An unknown symbol on the given exchange returns HTTP 404 `{"status": "error", "message": "Symbol not found"}`.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data | object | Master-contract row (see below) |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | OpenBull canonical symbol (the value you sent) |
| brsymbol | string | Broker-native ticker for the same instrument (used by `mapping/order_data.py` when round-tripping orders) |
| name | string | Underlying ticker for F&O rows (`NIFTY`, `RELIANCE`); company/index name for equity & index rows |
| exchange | string | OpenBull canonical exchange code |
| brexchange | string | Broker-native exchange code (e.g. Upstox's `NSE_FO`, Zerodha's `NFO-OPT`) |
| token | string | Broker instrument token |
| expiry | string | `DD-MMM-YY` uppercase (`28-APR-26`); empty string for cash equities and indices |
| strike | number | Strike price (`0.0` for non-options) |
| lotsize | integer | Contract size (`1` for cash equities) |
| instrumenttype | string | `EQ`, `OPTIDX` (index options), `OPTSTK` (stock options), `FUTIDX`, `FUTSTK`, `INDEX`, etc. (exact set is broker-dependent) |
| tick_size | number | Minimum price increment in rupees (e.g. `0.05` for NFO) |

## Notes

- The endpoint hits the in-process `symtoken` cache hydrated from PostgreSQL on startup (and mirrored to Redis under `symtoken:*` hashes). It does not call the broker — purely a master-contract lookup.
- For cash equities, `expiry` is empty and `strike` is `0.0` — those are F&O columns.
- The `brsymbol` and `brexchange` round-trip correctness is the integrator's contract with `mapping/order_data.py`: any orderbook response is mapped back from these broker-native values to OpenBull canonical via the same row.
- Validation source for the `exchange` field: `backend/utils/constants.py::VALID_EXCHANGES`. See [order-constants.md](../../design/order-constants.md).

---

**Back to**: [API Documentation](../README.md)
