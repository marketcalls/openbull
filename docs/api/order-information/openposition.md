# OpenPosition

Get the current net open quantity for a specific symbol + exchange + product. Useful before placing a smart order or for inline risk checks.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/openposition
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "symbol": "INFY",
  "exchange": "NSE",
  "product": "MIS",
  "strategy": "Python"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "quantity": 1
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| symbol | OpenBull trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCDEX | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Mandatory | - |
| strategy | Strategy identifier — accepted for OpenAlgo SDK parity, **not used** as a filter (see Notes) | Optional | - |

A request missing any of `symbol`, `exchange`, or `product` returns HTTP 400 `{"status": "error", "message": "symbol, exchange, and product are required"}`.

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data.quantity | integer | Net open quantity. Positive = long, negative = short, `0` = flat. |
| message | string | Error description (only present when `status="error"`). |

## Notes

- The response only carries the `quantity`. The request `symbol`/`exchange`/`product`/`strategy` are not echoed in the response body — they're the keys you sent, not data the service computes.
- **Strategy filter** — the `strategy` field is accepted so OpenAlgo SDKs work unchanged, but the broker call does not filter positions by strategy tag (it can't — brokers don't track strategy attribution server-side). `quantity` is the **aggregate** net position across every strategy that has traded the symbol on the same product.
- Positive `quantity` = long, negative = short, `0` = flat — commonly used right before [PlaceSmartOrder](../order-management/placesmartorder.md) to compute the delta to a target position size.

---

**Back to**: [API Documentation](../README.md)
