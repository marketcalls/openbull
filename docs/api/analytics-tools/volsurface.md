# VolSurface

Rectangular Implied-Volatility grid across `(strikes × expiries)`. For each expiry, the service builds the OTM-leg list (CE IV for strikes ≥ ATM, PE IV for strikes < ATM), batch-quotes them, and solves Black-76 IV per cell. Powers the `/tools/volsurface` 3-D Plotly page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/volsurface
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_dates": ["28APR26", "05MAY26", "12MAY26"],
  "strike_count": 10
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
    "strikes": [25450.0, 25500.0, 25550.0, 25600.0, 25650.0],
    "expiries": [
      {"date": "28APR26", "dte": 8.2},
      {"date": "05MAY26", "dte": 15.2},
      {"date": "12MAY26", "dte": 22.2}
    ],
    "surface": [
      [14.95, 14.50, 13.95, 13.42, 13.18],
      [14.65, 14.25, 13.85, 13.55, 13.40],
      [14.40, 14.15, 13.85, 13.65, 13.55]
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
| expiry_dates | List of `DDMMMYY` expiry strings. **Max 8 expiries** per call. Must be a non-empty array. | Mandatory | - |
| strike_count | Number of strikes either side of ATM. Range `[1, 40]`. | Optional | `10` |

Error responses (all HTTP 400):
- `"underlying and exchange are required"`
- `"expiry_dates must be a non-empty list"`
- `"At most 8 expiries supported"`
- `"strike_count must be an integer"`
- `"strike_count must be between 1 and 40"`

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data.underlying | string | Echo of the request underlying |
| data.underlying_ltp | number | Spot LTP at request time |
| data.atm_strike | number | ATM strike (closest to spot) |
| data.strikes | array | Strikes covered by the surface, low→high |
| data.expiries | array | One row per expiry: `{date: "DDMMMYY", dte: <days-to-expiry float>}` |
| data.surface | matrix | 2-D IV grid: `surface[i][j]` is the IV (percent) at `expiries[i].date` × `strikes[j]` |

## OTM-Leg Convention

For each row of the surface, the service uses:

- **CE IV** for strikes `≥ ATM` — out-of-the-money calls
- **PE IV** for strikes `< ATM` — out-of-the-money puts

This is the standard OTM convention — at any given strike, the OTM leg's IV is the cleaner volatility signal (the ITM leg's IV is contaminated by intrinsic value and tight spreads).

## Notes

- The off-hours fallback: when `ltp` is 0 or missing on a cell, the service falls back to `close`/`prev_close` so the surface still renders outside market hours.
- DTE is computed in `Asia/Kolkata` with a fixed `+05:30` offset (no DST, no pytz).
- The 3-D page uses Plotly `mesh3d` with a manual aspect-ratio override so the surface stays visible at any data range.
- For thinly-traded expiries with empty cells, the grid returns `null` for that cell; the frontend interpolates visually.

## Related

- [IV Smile](./ivsmile.md) — single-expiry slice of the surface
- [IV Chart](./ivchart.md) — historical ATM IV over time

---

**Back to**: [API Documentation](../README.md)
