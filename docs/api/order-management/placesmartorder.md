# PlaceSmartOrder

Place Order Smartly by analyzing the current open position. It matches the Position Size with the given position book. Buy/Sell Signal Orders will be traded accordingly to the Position Size.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/placesmartorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Test Strategy",
  "exchange": "NSE",
  "symbol": "INFY",
  "action": "BUY",
  "product": "MIS",
  "pricetype": "MARKET",
  "quantity": "1",
  "position_size": "5",
  "price": "0",
  "trigger_price": "0",
  "disclosed_quantity": "0"
}
```

## Sample API Response

```json
{
  "orderid": "260415000382450",
  "status": "success"
}
```

## How PlaceSmartOrder Works

PlaceSmartOrder analyzes the current open position and automatically calculates the quantity needed to reach the target position size.

| Action | Qty (API) | Pos Size (API) | Current Open Pos | Action by OpenBull |
|--------|-----------|----------------|------------------|-------------------|
| BUY | 100 | 0 | 0 | No Open Pos Found. Buy +100 qty |
| BUY | 100 | 100 | -100 | BUY 200 to match Open Pos in API Param |
| BUY | 100 | 100 | 100 | No Action. Position matched |
| BUY | 100 | 200 | 100 | BUY 100 to match Open Pos in API Param |
| SELL | 100 | 0 | 0 | No Open Pos Found. SELL 100 qty |
| SELL | 100 | -100 | +100 | SELL 200 to match Open Pos in API Param |
| SELL | 100 | -100 | -100 | No Action. Position matched |
| SELL | 100 | -200 | -100 | SELL 100 to match Open Pos in API Param |

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy name | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX | Mandatory | - |
| symbol | Trading symbol | Mandatory | - |
| action | Order action: BUY or SELL | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Optional | MIS |
| pricetype | Price type: MARKET, LIMIT, SL, SL-M | Optional | MARKET |
| quantity | Order quantity | Mandatory | - |
| position_size | Target position size (absolute) | Mandatory | - |
| price | Order price (for LIMIT orders) | Optional | 0 |
| trigger_price | Trigger price (for SL orders) | Optional | 0 |
| disclosed_quantity | Disclosed quantity | Optional | 0 |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" or "error" |
| orderid | string | Unique order ID from broker (on success) |
| message | string | Error message or "No action needed" if position already at target |

## Notes

- Smart orders are ideal for **position-based strategies** where you want to maintain a specific position size
- The **position_size** represents the absolute target position:
  - Positive values = Long position
  - Negative values = Short position
  - Zero = Flat (no position)
- If current position already matches target, no order is placed
- Smart orders have a configurable delay (default 0.5 seconds) to allow previous orders to fill
- Works across all exchanges and product types

---

**Back to**: [API Documentation](../README.md)
