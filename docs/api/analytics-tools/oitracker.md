# OITracker

Per-strike Open Interest and OI delta across the option chain, plus aggregate metrics (PCR-OI, PCR-Volume, CE / PE totals) and the matching-expiry futures price. Powers the `/tools/oitracker` page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/oitracker
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26"
}
```

## Sample API Response (truncated)

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "spot_price": 25966.05,
  "futures_price": 25985.50,
  "lot_size": 75,
  "atm_strike": 25950.0,
  "expiry_date": "28APR26",
  "pcr_oi": 0.91,
  "pcr_volume": 0.85,
  "total_ce_oi": 12345678,
  "total_pe_oi": 11234567,
  "chain": [
    {"strike": 25500.0, "ce_oi": 234000, "pe_oi": 1234567},
    {"strike": 25950.0, "ce_oi": 1234567, "pe_oi": 1234567},
    {"strike": 26400.0, "ce_oi": 1456000, "pe_oi": 234000}
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker (`NIFTY`, `BANKNIFTY`, etc.) | Mandatory | - |
| exchange | Underlying exchange. Use `NSE_INDEX` / `BSE_INDEX` for indices, `NFO`/`BFO`/`MCX` for stock-/commodity-option underlyings | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` format (`28APR26`) | Mandatory | - |

A request missing any of the three required fields returns HTTP 400 `{"status": "error", "message": "underlying, exchange and expiry_date are required"}`.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| underlying | string | Echo of the request underlying |
| spot_price | number | Current spot LTP |
| futures_price | number | Matching-expiry futures LTP (synthetic via put-call parity if no FUT contract exists) |
| lot_size | integer | Contract size |
| atm_strike | number | ATM strike (closest to spot) |
| expiry_date | string | Echo of the request expiry |
| pcr_oi | number | Put-Call Ratio by OI (`total_pe_oi / total_ce_oi`) |
| pcr_volume | number | Put-Call Ratio by traded volume |
| total_ce_oi | integer | Sum of CE OI across the returned chain window |
| total_pe_oi | integer | Sum of PE OI across the returned chain window |
| chain | array | One row per strike. See below. |

### Chain Array Object

| Field | Type | Description |
|-------|------|-------------|
| strike | number | Strike price |
| ce_oi | integer | Open Interest on the CE leg |
| pe_oi | integer | Open Interest on the PE leg |

## Notes

- The chain window is sized to fit broker per-call quote limits (e.g. Fyers caps quotes at 100 symbols per request). Around 25 strikes is typical.
- High CE OI = bearish wall (call writers expect resistance there). High PE OI = bullish wall (put writers expect support).
- Underlying convention: CE = red, PE = green on the dashboard side — opposite of the usual "calls are bullish" framing because writers, not buyers, drive OI levels.
- `pcr_oi > 1` typically reads bullish (more put writing than call writing) and vice versa.

## Related

- [Max Pain](./maxpain.md) — strike that minimises total option-writer pain
- [GEX](./gex.md) — gamma-weighted OI exposure
- [Option Chain](../options-services/optionchain.md) — full chain with LTP / IV / OI per strike

---

**Back to**: [API Documentation](../README.md)
