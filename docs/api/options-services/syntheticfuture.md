# SyntheticFuture

Calculate the synthetic futures price for an underlying using put-call parity from the ATM option pair. Also returns the basis (difference between synthetic future and spot price).

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/syntheticfuture
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

## Sample API Response

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "spot_price": 24231.30,
  "atm_strike": 24250.0,
  "synthetic_future": 24231.55,
  "basis": 0.25,
  "ce_ltp": 311.85,
  "pe_ltp": 285.20
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| underlying | Underlying symbol (NIFTY, BANKNIFTY, etc.) | Mandatory | - |
| exchange | Exchange: NSE_INDEX, BSE_INDEX | Mandatory | - |
| expiry_date | Expiry date in DDMMMYY format (e.g., 28APR26) | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| underlying | string | Underlying symbol |
| exchange | string | Exchange code |
| expiry_date | string | Expiry date |
| spot_price | number | Current spot/index price |
| atm_strike | number | ATM strike used for calculation |
| synthetic_future | number | Calculated synthetic futures price |
| basis | number | Difference between synthetic future and spot price |
| ce_ltp | number | ATM call option LTP used in calculation |
| pe_ltp | number | ATM put option LTP used in calculation |

## How Synthetic Future is Calculated

The synthetic futures price is derived from **put-call parity**:

```
Synthetic Future = ATM Strike + CE LTP - PE LTP
```

The **basis** indicates the cost of carry:

```
Basis = Synthetic Future - Spot Price
```

## Notes

- The synthetic future provides a more accurate forward price than the spot
- A **positive basis** indicates the market expects the underlying to be above current levels at expiry (contango)
- A **negative basis** indicates the market expects the underlying to be below current levels at expiry (backwardation)
- Near expiry, the basis approaches zero as synthetic future converges with spot
- This is used internally by [OptionsOrder](../order-management/optionsorder.md) for precise ATM calculation
- Useful for **volatility trading** and **arbitrage strategies**

## Use Cases

- **Precise ATM calculation**: Use synthetic future instead of spot for better strike selection
- **Basis trading**: Identify arbitrage between futures and synthetic futures
- **Cost of carry analysis**: Monitor the fair value of futures contracts
- **Options pricing**: Feed into option pricing models for more accurate Greeks

---

**Back to**: [API Documentation](../README.md)
