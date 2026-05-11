# ClosePosition

Square off every open position across the account with broker-side MARKET orders.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/closeposition
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python"
}
```

## Sample API Response

When at least one position was closed:

```json
{
  "status": "success",
  "message": "All Open Positions SquaredOff"
}
```

When the account had no open positions:

```json
{
  "message": "No Open Positions Found"
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier — accepted for OpenAlgo SDK parity, currently **ignored** by the close-positions logic | Optional | - |

## Response Fields

The exact shape comes from the broker plugin's `close_all_positions(api_key, auth_token)` and varies slightly per broker. The common fields are:

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` when at least one position was squared off; omitted when no positions were found. |
| message | string | Human-readable status — `"All Open Positions SquaredOff"`, `"No Open Positions Found"`, or an error description. |

## Behavior

- Closes **every** open position regardless of the `strategy` tag — the field is accepted in the request body for client-SDK compatibility but is not used as a filter in `backend/services/order_service.py::close_all_positions_service`.
- Each open position is closed with a MARKET order tagged `strategy="Squareoff"`. Long positions get a SELL, shorts get a BUY, both at the current open quantity.
- The product code on each squareoff order matches the original position (`MIS`/`CNC`/`NRML` preserved).
- Individual broker failures on a per-position close don't stop the loop — other positions continue closing. The aggregate success/failure is summarised in the response message.
- **Sandbox mode** — when the global trading mode is `sandbox`, the call is dispatched to the simulator (`backend/sandbox`) and only flattens simulated positions; the live broker is not contacted.

## Notes

- Closing uses **MARKET** orders for immediate execution — be aware of slippage on illiquid contracts.
- For a single-symbol close, place an opposite-direction order via [PlaceOrder](./placeorder.md) instead.

---

**Back to**: [API Documentation](../README.md)
