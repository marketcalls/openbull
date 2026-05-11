# Straddle

Dynamic-ATM straddle time series with synthetic-future overlay. For each underlying candle, the service recomputes ATM from that candle's close, looks up the matching CE+PE candle, and emits straddle premium + put-call-parity synthetic future. Powers the `/tools/straddle` page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/straddle
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "interval": "5m",
  "days": 3
}
```

## Sample API Response (truncated)

```json
{
  "status": "success",
  "data": {
    "underlying": "NIFTY",
    "underlying_ltp": 25966.05,
    "expiry_date": "28APR26",
    "interval": "5m",
    "days_to_expiry": 8,
    "series": [
      {"time": 1761625800, "spot": 25960.5, "atm_strike": 25950.0,
       "ce_price": 142.5, "pe_price": 124.3,
       "straddle": 266.8, "synthetic_future": 25968.2},
      {"time": 1761626100, "spot": 25963.2, "atm_strike": 25950.0,
       "ce_price": 144.8, "pe_price": 122.0,
       "straddle": 266.8, "synthetic_future": 25972.8}
    ]
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker | Mandatory | - |
| exchange | Underlying exchange | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` format | Mandatory | - |
| interval | Candle interval. Broker-specific (typically `1m`, `5m`, `15m`, `30m`, `1h`, `D`) | Optional | `"1m"` |
| days | Trading-day lookback. Range `[1, 30]`. The calendar window is padded by `2×days+2` to ride through weekends / holidays. | Optional | `5` |

Invalid `days` (non-numeric) returns HTTP 400.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data.underlying | string | Echo of the request underlying |
| data.underlying_ltp | number | Current spot LTP |
| data.expiry_date | string | Echo of the request expiry |
| data.interval | string | Echo of the request interval |
| data.days_to_expiry | integer | Trading days from now to expiry |
| data.series | array | Time series. One row per common timestamp where all three (underlying, CE, PE) candles exist. |

### Series Row Object

| Field | Type | Description |
|-------|------|-------------|
| time | int | Candle close timestamp, Unix epoch seconds |
| spot | number | Underlying close at this candle |
| atm_strike | number | ATM strike recomputed from this candle's spot (snapped to the chain's strike step) |
| ce_price | number | CE close at the recomputed ATM strike for this candle |
| pe_price | number | PE close at the matching ATM PE strike |
| straddle | number | `ce_price + pe_price` |
| synthetic_future | number | `atm_strike + ce_price − pe_price` (put-call parity) |

## Formulas

```
ATM(t) = round_to_strike_step(spot(t))
Straddle(t) = CE_close(ATM(t), t) + PE_close(ATM(t), t)
SyntheticFuture(t) = ATM(t) + CE_close(ATM(t), t) − PE_close(ATM(t), t)
```

ATM is **recomputed at every candle** — that's what makes this a "dynamic ATM" straddle rather than a fixed-strike one. As the underlying drifts during the day, the matched strike shifts with it.

## Notes

- The synthetic-future overlay is useful as a sanity check on the broker's listed future quote — large persistent gaps point at illiquid contracts.
- Off-hours fallback: candles outside trading hours are dropped from the series rather than emitting NaN.
- Common timestamps only — if one of `(spot, CE, PE)` is missing a candle, that timestamp is dropped from the output.
- The page at `/tools/straddle` consumes this endpoint and renders both `straddle` and `synthetic_future` on a `lightweight-charts` panel.

## Related

- [Synthetic Future](../options-services/syntheticfuture.md) — current-bar synthetic future via put-call parity
- [Option Chain](../options-services/optionchain.md) — full chain snapshot

---

**Back to**: [API Documentation](../README.md)
