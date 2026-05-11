# GEX (Gamma Exposure)

Per-strike Gamma Exposure computed from live OI and Black-76 gamma. Returns per-strike CE / PE / Net GEX, the matching-expiry futures price, OI walls, and aggregate metrics including a put-call ratio. Powers the `/tools/gex` page.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/gex
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
  "total_ce_oi": 12345678,
  "total_pe_oi": 11234567,
  "total_ce_gex": 12345.67,
  "total_pe_gex": 11234.56,
  "total_net_gex": 1111.11,
  "chain": [
    {
      "strike": 25950.0,
      "ce_oi": 234567,
      "pe_oi": 234567,
      "ce_gamma": 0.000845,
      "pe_gamma": 0.000821,
      "ce_gex": 14854.32,
      "pe_gex": 14431.20,
      "net_gex": 423.12
    }
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
| futures_price | number | Matching-expiry FUT price (or synthetic via put-call parity) |
| lot_size | integer | Contract size — used to dollarise GEX |
| atm_strike | number | ATM strike (closest to spot) |
| expiry_date | string | Echo of the request expiry |
| pcr_oi | number | Put-Call Ratio by OI |
| total_ce_oi | integer | Aggregate CE OI across the returned chain window |
| total_pe_oi | integer | Aggregate PE OI |
| total_ce_gex | number | Aggregate CE GEX (lot-size-weighted) |
| total_pe_gex | number | Aggregate PE GEX |
| total_net_gex | number | `total_ce_gex − total_pe_gex` |
| chain | array | Per-strike rows. See below. |

### Chain Array Object

| Field | Type | Description |
|-------|------|-------------|
| strike | number | Strike price |
| ce_oi | integer | CE Open Interest |
| pe_oi | integer | PE Open Interest |
| ce_gamma | number | Black-76 gamma for the CE leg at this strike |
| pe_gamma | number | Black-76 gamma for the PE leg |
| ce_gex | number | `ce_gamma × ce_oi × lot_size` |
| pe_gex | number | `pe_gamma × pe_oi × lot_size` |
| net_gex | number | `ce_gex − pe_gex` (positive = call-dominated, negative = put-dominated) |

## Formulas

```
GEX_CE(K) = γ_CE(K) × OI_CE(K) × lot_size
GEX_PE(K) = γ_PE(K) × OI_PE(K) × lot_size
NetGEX(K) = GEX_CE(K) − GEX_PE(K)

Total_Net_GEX = Σ NetGEX(K)
```

Black-76 gamma is solved per leg via `option_greeks_service.calculate_greeks()` against the matching-expiry futures price (not spot — futures price is the correct underlying for Black-76 on F&O).

## Interpretation

- **Positive Net GEX** at a strike: market makers are net long gamma there — they sell into rallies and buy into dips, suppressing volatility.
- **Negative Net GEX** at a strike: market makers are net short gamma — they buy into rallies and sell into dips, amplifying moves.
- **GEX flip** (zero-gamma index, ZGI): the strike where cumulative Net GEX crosses zero — often a magnet / pivot.

## Notes

- Around 45 strikes are returned, centred on ATM.
- The page at `/tools/gex` overlays CE-GEX (red) and PE-GEX (green) as walls, with Net GEX as a separate trace, and annotates the GEX flip strike.

## Related

- [OI Tracker](./oitracker.md) — raw OI per strike without gamma weighting
- [Max Pain](./maxpain.md) — strike that minimises writer pain at expiry

---

**Back to**: [API Documentation](../README.md)
