# Funds

Get account funds information including available cash, collateral, and margin utilization.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/funds
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6"
}
```

## Sample API Response

```json
{
  "status": "success",
  "data": {
    "availablecash": "485320.66",
    "collateral": "0.00",
    "m2mrealized": "1250.45",
    "m2munrealized": "-3420.80",
    "utiliseddebits": "214679.34"
  }
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| data | object | Funds data object |

### Data Object Fields

| Field | Type | Description |
|-------|------|-------------|
| availablecash | string | Available cash for trading |
| collateral | string | Collateral margin (pledged holdings) |
| m2mrealized | string | Realized Mark-to-Market profit/loss |
| m2munrealized | string | Unrealized Mark-to-Market profit/loss |
| utiliseddebits | string | Margin utilized for positions |

## Understanding Funds

| Field | Description |
|-------|-------------|
| **Available Cash** | Free cash available for new trades |
| **Collateral** | Margin from pledged stocks/securities |
| **Realized M2M** | Profit/loss from closed positions today |
| **Unrealized M2M** | Profit/loss from open positions (not booked) |
| **Utilized Debits** | Margin blocked for existing positions |

## Notes

- Values are returned as **strings** for precision
- **availablecash** is the amount available for new orders
- **collateral** is margin from pledged holdings (varies by broker)
- M2M values update in real-time with market prices
- Total margin = availablecash + collateral

---

**Back to**: [API Documentation](../README.md)
