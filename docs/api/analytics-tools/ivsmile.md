# IVSmile

Per-strike Implied Volatility for one expiry — CE IV and PE IV at every chain strike, plus the ATM IV and a 25-delta proxy skew. Powers the `/tools/ivsmile` page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/ivsmile
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
  "atm_iv": 13.55,
  "skew": 1.20,
  "expiry_date": "28APR26",
  "chain": [
    {"strike": 25500.0, "ce_iv": 14.85, "pe_iv": 14.20},
    {"strike": 25700.0, "ce_iv": 14.20, "pe_iv": 13.95},
    {"strike": 25950.0, "ce_iv": 13.42, "pe_iv": 13.68},
    {"strike": 26200.0, "ce_iv": 13.30, "pe_iv": 14.00},
    {"strike": 26400.0, "ce_iv": 13.18, "pe_iv": 13.95}
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker | Mandatory | - |
| exchange | Underlying exchange | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` format | Mandatory | - |

A request missing any of the three required fields returns HTTP 400.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| underlying | string | Echo of the request underlying |
| spot_price | number | Current spot LTP |
| atm_strike | number | ATM strike (closest to spot) |
| atm_iv | number | IV of the ATM CE (in percent) |
| skew | number | Proxy 25-delta skew: `PE IV at ~ATM−5% minus CE IV at ~ATM+5%`. Positive = PE more expensive than CE at equivalent distance. |
| expiry_date | string | Echo of the request expiry |
| chain | array | Per-strike CE/PE IV pairs. See below. |

### Chain Array Object

| Field | Type | Description |
|-------|------|-------------|
| strike | number | Strike price |
| ce_iv | number | Implied volatility of the CE leg at this strike (percent) |
| pe_iv | number | Implied volatility of the PE leg at this strike (percent) |

## Notes

- Around 25 strikes are returned, centred on ATM, sized to fit broker per-call quote caps.
- IV is solved by Newton iteration on Black-76 against the current LTP. Failed convergence (deep ITM / OTM, zero-bid options) returns `0.0` for that leg's IV.
- The "smile" name is folklore — a true smile (symmetric U) is rare in Indian index options; a forward / backwardation skew is the norm.
- The page at `/tools/ivsmile` consumes this endpoint and overlays both legs on one chart with the ATM IV badged in the corner.

## Related

- [IV Chart](./ivchart.md) — historical ATM IV time series
- [Vol Surface](./volsurface.md) — strikes × expiries IV grid
- [Option Greeks](../options-services/optiongreeks.md) — ATM CE/PE snapshot Greeks (current bar only)

---

**Back to**: [API Documentation](../README.md)
