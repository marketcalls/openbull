# OptionGreeks

Calculate Option Greeks (Delta, Gamma, Theta, Vega, Rho) and Implied Volatility for an option.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/optiongreeks
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO",
  "interest_rate": 0.00,
  "underlying_symbol": "NIFTY",
  "underlying_exchange": "NSE_INDEX"
}
```

## Sample API Response

```json
{
  "status": "success",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO",
  "underlying": "NIFTY",
  "strike": 24250.0,
  "option_type": "CE",
  "expiry_date": "28-Apr-2026",
  "days_to_expiry": 13.2541,
  "spot_price": 24231.30,
  "option_price": 311.85,
  "interest_rate": 0.0,
  "implied_volatility": 17.75,
  "greeks": {
    "delta": 0.497,
    "gamma": 0.000412,
    "theta": -12.56,
    "vega": 22.85,
    "rho": 4.325
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | Option symbol | Mandatory | - |
| exchange | Exchange: NFO, BFO, CDS, MCX | Mandatory | - |
| interest_rate | Risk-free interest rate (annualized %) | Optional | 0 |
| underlying_symbol | Underlying symbol for spot price | Optional | Derived from option |
| underlying_exchange | Underlying exchange | Optional | NSE_INDEX |
| forward_price | Custom forward/synthetic futures price | Optional | - |
| expiry_time | Custom expiry time in "HH:MM" format | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| symbol | string | Option symbol |
| exchange | string | Exchange |
| underlying | string | Underlying symbol |
| strike | number | Strike price |
| option_type | string | CE or PE |
| expiry_date | string | Expiry date |
| days_to_expiry | number | Days remaining to expiry (fractional) |
| spot_price | number | Current spot/underlying price |
| option_price | number | Current option LTP |
| interest_rate | number | Risk-free rate used |
| implied_volatility | number | Calculated IV (%) |
| greeks | object | Greeks values |

### Greeks Object Fields

| Field | Type | Description |
|-------|------|-------------|
| delta | number | Price sensitivity to underlying movement |
| gamma | number | Delta sensitivity to underlying movement |
| theta | number | Time decay per day (negative) |
| vega | number | Price sensitivity to 1% IV change |
| rho | number | Price sensitivity to 1% interest rate change |

## Understanding Option Greeks

| Greek | Description | Typical Range |
|-------|-------------|---------------|
| **Delta** | How much option price moves for 1 point underlying move | CE: 0 to 1, PE: -1 to 0 |
| **Gamma** | Rate of change of delta | Higher near ATM |
| **Theta** | Daily time decay (negative for buyers) | Increases near expiry |
| **Vega** | Price change for 1% IV move | Higher for longer expiry |
| **Rho** | Price change for 1% interest rate move | Usually small |

## Notes

- Uses **Black-76 model** (appropriate for options on futures/forwards)
- **Implied Volatility** is calculated using Newton-Raphson method
- For **deep ITM** options with no time value, returns theoretical Greeks (delta = +/-1)
- **days_to_expiry** includes fractional days for accuracy
- The **underlying_symbol** parameter allows using spot price instead of futures

## Use Cases

- **Position sizing**: Use delta for hedge ratios
- **Risk management**: Monitor gamma exposure
- **Time decay analysis**: Track theta decay
- **Volatility trading**: Monitor vega exposure

---

**Back to**: [API Documentation](../README.md)
