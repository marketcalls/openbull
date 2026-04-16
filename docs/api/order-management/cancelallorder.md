# CancelAllOrder

Cancel all open/pending orders. Optionally filter by strategy to cancel only orders belonging to a specific strategy.

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
  "message": "All open orders cancelled"
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier (filters orders to cancel) | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| message | string | Confirmation or error message |

## Notes

- If **strategy** is provided, only orders placed with that strategy tag are cancelled
- If **strategy** is omitted, **all** open/pending orders are cancelled
- Already executed, rejected, or cancelled orders are not affected
- This is a bulk operation -- individual order cancellation failures do not prevent others from being cancelled
- Use with caution as this can cancel orders across all strategies if no filter is applied

---

**Back to**: [API Documentation](../README.md)
