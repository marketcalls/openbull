# MaxPain

Max-pain analysis for a given underlying + expiry. Returns the strike at which option writers collectively suffer the smallest loss at expiry (the "max pain" strike), per-strike pain breakdown, and aggregate writer-loss metrics. Powers the `/tools/maxpain` page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/maxpain
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
  "atm_strike": 25950.0,
  "max_pain_strike": 25900.0,
  "expiry_date": "28APR26",
  "total_ce_pain": 8765432100,
  "total_pe_pain": 6543210987,
  "chain": [
    {"strike": 25500.0, "ce_pain": 12345670, "pe_pain": 9876543, "total_pain": 22222213},
    {"strike": 25900.0, "ce_pain":  9876543, "pe_pain": 9123456, "total_pain": 19000000},
    {"strike": 26400.0, "ce_pain":  7654321, "pe_pain": 13456789, "total_pain": 21111110}
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker | Mandatory | - |
| exchange | Underlying exchange (e.g. `NSE_INDEX`, `BSE_INDEX`, `MCX_INDEX`, `NFO`, `BFO`, `MCX`) | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` format | Mandatory | - |

A request missing any of the three required fields returns HTTP 400.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| underlying | string | Echo of the request underlying |
| spot_price | number | Current spot LTP |
| atm_strike | number | ATM strike |
| max_pain_strike | number | Strike where `total_pain` is minimised |
| expiry_date | string | Echo of the request expiry |
| total_ce_pain | number | Aggregate CE-writer notional loss (in rupees, summed over the chain) |
| total_pe_pain | number | Aggregate PE-writer notional loss |
| chain | array | Per-strike pain breakdown. See below. |

### Chain Array Object

| Field | Type | Description |
|-------|------|-------------|
| strike | number | Strike price |
| ce_pain | number | Total CE-writer loss at expiry if spot lands at this strike: `Σ max(0, K_test − K) × OI_CE × lot_size` over all chain strikes K |
| pe_pain | number | Total PE-writer loss at expiry: `Σ max(0, K − K_test) × OI_PE × lot_size` |
| total_pain | number | `ce_pain + pe_pain` |

## Calculation

For each candidate strike `K_test` in the chain:

```
ce_pain(K_test) = Σ_{K_i}  max(0, K_test - K_i) × OI_CE(K_i) × lot_size
pe_pain(K_test) = Σ_{K_i}  max(0, K_i - K_test) × OI_PE(K_i) × lot_size
total_pain(K_test) = ce_pain(K_test) + pe_pain(K_test)
```

The `max_pain_strike` is `argmin(total_pain)` across all candidates.

## Notes

- Max pain is a market-folklore signal: writers (who collectively have the most capital at risk) are presumed to "pin" the expiry to the strike that minimises their loss.
- The signal is most useful close to expiry, when remaining time value is small and intrinsic-value pain dominates.
- Off-hours fallback: when bid/ask collapses (weekends, holidays), the service falls back to last close + cached OI rather than emitting `NaN`.

## Related

- [OI Tracker](./oitracker.md) — per-strike OI and PCR
- [GEX](./gex.md) — gamma-weighted OI exposure

---

**Back to**: [API Documentation](../README.md)
