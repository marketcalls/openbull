# ClosePosition

Close all open positions. Optionally filter by strategy to close only positions belonging to a specific strategy.

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

```json
{
  "status": "success",
  "message": "All positions closed"
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier (filters positions to close) | Optional | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| message | string | Confirmation or error message |

## Notes

- If **strategy** is provided, only positions opened with that strategy tag are closed
- If **strategy** is omitted, **all** open positions are closed
- Positions are closed using **MARKET** orders for immediate execution
- Long positions are closed with SELL orders, short positions with BUY orders
- The quantity is automatically set to the current open position size
- Use with caution as this can close positions across all strategies if no filter is applied

---

**Back to**: [API Documentation](../README.md)
