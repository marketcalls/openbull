# OptionSymbol

Resolve an option symbol from an underlying, expiry date, offset (ATM/ITM/OTM), and option type. Returns the exact trading symbol and lot size without placing an order.

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
  "underlying": "NIFTY",
  "exchange": "NFO",
  "offset": "ATM",
  "option_type": "CE",
  "strike": 24250.0,
  "expiry_date": "28APR26",
  "underlying_ltp": 24231.30,
  "lotsize": 65
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying symbol (NIFTY, BANKNIFTY, etc.) | Mandatory | - |
| exchange | Exchange: NSE_INDEX, BSE_INDEX | Mandatory | - |
| expiry_date | Expiry date in DDMMMYY format (e.g., 28APR26) | Mandatory | - |
| offset | Strike offset: ATM, ITM1-ITM50, OTM1-OTM50 | Mandatory | - |
| option_type | Option type: CE or PE | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| symbol | string | Resolved option trading symbol |
| underlying | string | Underlying symbol |
| exchange | string | Exchange where the option trades (NFO/BFO) |
| offset | string | Offset used for resolution |
| option_type | string | CE or PE |
| strike | number | Resolved strike price |
| expiry_date | string | Expiry date |
| underlying_ltp | number | Current underlying price used for ATM calculation |
| lotsize | number | Lot size for the option |

## Offset Values

| Offset | Description |
|--------|-------------|
| ATM | At-The-Money (strike closest to current price) |
| ITM1 to ITM50 | In-The-Money (1-50 strikes away) |
| OTM1 to OTM50 | Out-of-The-Money (1-50 strikes away) |

## Notes

- This is a **read-only** endpoint -- it resolves the symbol without placing any order
- Use this to discover the correct option symbol before using [PlaceOrder](../order-management/placeorder.md) or [Quotes](../market-data/quotes.md)
- The ATM strike is calculated using the current underlying LTP
- For **NSE_INDEX** exchange, the resolved symbol trades on **NFO**
- The **lotsize** can be used to determine the minimum tradeable quantity

## Use Cases

- **Symbol discovery**: Find exact option symbol for a given offset
- **Pre-trade validation**: Verify the correct strike before ordering
- **Strategy planning**: Determine lot sizes and strikes for multi-leg strategies

---

**Back to**: [API Documentation](../README.md)
