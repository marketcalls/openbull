# IVChart

Historical Implied Volatility + Greeks time series for the ATM CE and ATM PE of a chosen underlying/expiry. Aligns underlying / CE / PE OHLCV candles on common timestamps and solves Black-76 IV at every bar. Powers the `/tools/greeks` page.

NSE / BSE underlyings only — broker history endpoints for indices on `*_INDEX` exchanges are mapped to the matching `NFO`/`BFO` contracts before fanout.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/ivchart
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
    "atm_strike": 25950.0,
    "ce_symbol": "NIFTY28APR2625950CE",
    "pe_symbol": "NIFTY28APR2625950PE",
    "interval": "5m",
    "days": 3,
    "expiry_date": "28APR26",
    "interest_rate": 0.0,
    "series": [
      {
        "symbol": "NIFTY28APR2625950CE",
        "option_type": "CE",
        "strike": 25950.0,
        "iv_data": [
          {"time": 1761625800, "iv": 13.42, "delta": 0.5302,
           "gamma": 0.000845, "theta": -8.43, "vega": 17.62,
           "option_price": 142.5, "underlying_price": 25966.05}
        ]
      },
      {
        "symbol": "NIFTY28APR2625950PE",
        "option_type": "PE",
        "strike": 25950.0,
        "iv_data": [
          {"time": 1761625800, "iv": 13.68, "delta": -0.4698,
           "gamma": 0.000841, "theta": -8.20, "vega": 17.55,
           "option_price": 124.3, "underlying_price": 25966.05}
        ]
      }
    ]
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying — base ticker | Mandatory | - |
| exchange | Underlying exchange (resolves to `NFO` or `BFO`; other exchanges return 400) | Mandatory | - |
| expiry_date | Expiry in `DDMMMYY` format | Mandatory | - |
| interval | Candle interval. One of `1m`, `5m`, `15m`, `30m`, `1h`, `D`. | Optional | `"5m"` |
| days | Trading-day lookback window. Must be in `[1, 30]`. | Optional | `1` |
| interest_rate | Annualised risk-free rate as percent (e.g. `7.0` for 7%). | Optional | `0.0` |

Invalid `days` or `interest_rate` (non-numeric) returns HTTP 400.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data.underlying | string | Echo of the request underlying |
| data.underlying_ltp | number | Current underlying LTP at request time |
| data.atm_strike | number | ATM strike used for the CE/PE symbols |
| data.ce_symbol | string | Resolved CE symbol |
| data.pe_symbol | string | Resolved PE symbol |
| data.interval | string | Echo of the request interval |
| data.days | integer | Echo of the request lookback window |
| data.expiry_date | string | Echo of the request expiry |
| data.interest_rate | number | Risk-free rate used in Black-76 (in decimal — e.g. `0.07`) |
| data.series | array | Two entries: one for CE, one for PE. Each carries an `iv_data` array of candle rows. |

### iv_data Row Object

| Field | Type | Description |
|-------|------|-------------|
| time | int | Candle close timestamp, Unix epoch seconds |
| iv | number | Black-76 implied volatility (in percent — e.g. `13.42` for 13.42%) |
| delta | number | First derivative of option price w.r.t. underlying |
| gamma | number | First derivative of delta w.r.t. underlying |
| theta | number | First derivative of option price w.r.t. time (per day) |
| vega | number | First derivative of option price w.r.t. volatility (per 1% IV change) |
| option_price | number | Option close at this candle |
| underlying_price | number | Underlying close at the same candle |

## Notes

- IV is solved per candle by Newton iteration on Black-76 against the candle's option close. Failed convergence (extreme deep ITM / OTM, no time value) yields `iv: null` for that row.
- The CE and PE series share the same timestamp grid because candles are intersected before fanout — phantom dips from a single delayed broker candle are eliminated.
- Greeks units match the snapshot endpoint (`theta/day`, `vega/1%`) so simulator and snapshot Greeks agree at zero-shift.
- The Greeks page at `/tools/greeks` is a thin Plotly wrapper over this endpoint.

## Related

- [IV Smile](./ivsmile.md) — per-strike IV cross-section at a single expiry
- [Vol Surface](./volsurface.md) — strikes × expiries IV grid
- [Option Greeks (snapshot)](../options-services/optiongreeks.md) — live Greeks for the ATM CE+PE

---

**Back to**: [API Documentation](../README.md)
