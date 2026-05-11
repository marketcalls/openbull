# CancelAllOrder

Cancel every open / trigger-pending order across the account.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/cancelallorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "canceled": ["250508001234567", "250508001234568"],
    "failed": []
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier — accepted for OpenAlgo SDK parity, currently **ignored** by the cancel logic | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| data.canceled | array of strings | Broker order IDs that were successfully cancelled |
| data.failed | array of strings | Broker order IDs the broker refused to cancel (already filled, already cancelled, etc.) |
| message | string | Error message (only present when `status="error"`) |

## Notes

- Cancels **every** cancellable order regardless of the `strategy` tag. The `strategy` field is accepted in the request body so OpenAlgo client SDKs work unchanged, but the cancel call doesn't filter on it (see `backend/services/order_service.py::cancel_all_orders_service`).
- Already-filled, already-cancelled, and rejected orders are not affected.
- Individual broker failures are surfaced in `data.failed` — the call still returns `status: "success"` if any cancellation worked.
- **Sandbox mode** — when the global trading mode is `sandbox`, the call is dispatched to `backend/sandbox` and only affects simulated orders.

---

**Back to**: [API Documentation](../README.md)
