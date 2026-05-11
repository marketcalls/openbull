# OptionSymbol

Resolve an option's exact OpenBull trading symbol from `(underlying, expiry, offset, option_type)`. Useful for SDK callers that want to pick "ATM call this expiry" without having to walk the chain themselves.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/optionsymbol
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "offset": "ATM",
  "option_type": "CE"
}
```

## Sample API Response

```json
{
  "status": "success",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO",
  "strike": 24250.0,
  "expiry": "28-APR-26",
  "lotsize": 75,
  "tick_size": 0.05,
  "underlying_ltp": 24231.30
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker (`NIFTY`) or futures-style with embedded expiry (`NIFTY28APR26FUT`) | Mandatory | - |
| exchange | Spot / quote exchange. Common values: `NSE_INDEX`, `BSE_INDEX`, `MCX_INDEX`, `NFO`, `BFO`, `MCX`, `CDS` | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` (`28APR26`). Optional only when embedded in `underlying`. | Conditional | - |
| offset | Strike offset — `ATM`, `ITM1`…`ITM50`, `OTM1`…`OTM50` | Mandatory | - |
| option_type | `CE` or `PE` | Mandatory | - |

An expiry that resolves to no strikes returns HTTP 404 `{"status": "error", "message": "No strikes found for ..."}`. An out-of-range offset returns HTTP 400. A symbol the master-contract doesn't know returns HTTP 404 `{"status": "error", "message": "Option <SYM> not found on <EXCH>."}`.

## Response Fields

The success response is **flat** (no `data` wrapper) — fields are placed directly on the response object.

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` |
| symbol | string | Resolved OpenBull option symbol (e.g. `NIFTY28APR2624250CE`) |
| exchange | string | Resolved options exchange (`NFO` for `NSE_INDEX`, `BFO` for `BSE_INDEX`, etc.) |
| strike | number | Resolved strike price |
| expiry | string | Expiry as `DD-MMM-YY` uppercase (`28-APR-26`) — note this differs from the `DDMMMYY` request format |
| lotsize | integer | Contract size |
| tick_size | number | Minimum price increment in rupees |
| underlying_ltp | number | Spot LTP used for ATM resolution (returned so the client can audit which spot was used) |

> The response does **not** echo `underlying`, `offset`, or `option_type` — those were inputs, the resolved `symbol` is what you act on.

## Offset Semantics

| Offset | Meaning |
|---|---|
| `ATM` | Strike closest to the current spot LTP |
| `ITM1`…`ITMn` | In-the-money — `n` strikes deep relative to ATM (CE: below ATM, PE: above ATM) |
| `OTM1`…`OTMn` | Out-of-the-money — `n` strikes away (CE: above ATM, PE: below ATM) |

## Notes

- This is a **read-only** endpoint — it resolves the symbol without placing any order. Pair with [PlaceOrder](../order-management/placeorder.md) or [OptionsOrder](../order-management/optionsorder.md) to act on it.
- Used by `place_options_order_service` internally — `OptionsOrder` and `OptionsMultiOrder` resolve symbols using the same logic before placing the broker call.
- For `NSE_INDEX` underlyings the resolved symbol trades on `NFO`; for `BSE_INDEX` it's `BFO`; for `MCX_INDEX` it's `MCX`.

---

**Back to**: [API Documentation](../README.md)
