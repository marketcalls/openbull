# CancelOrder

Cancel a specific pending order by its order ID.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/cancelorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "orderid": "260415000382402",
  "strategy": "Python"
}
```

## Sample API Response

```json
{
  "orderid": "260415000382402",
  "status": "success"
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| orderid | Order ID to cancel | Mandatory | - |
| strategy | Strategy identifier | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| orderid | string | Cancelled order ID (on success) |
| message | string | Error message (on error) |

## Notes

- Only **pending orders** (open/trigger pending) can be cancelled
- Already executed, rejected, or cancelled orders will return an error
- The **strategy** field is optional and used for tracking purposes
- After cancellation, the order status will change to "cancelled" in the order book

---

**Back to**: [API Documentation](../README.md)
