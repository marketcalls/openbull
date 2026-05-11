# BasketOrder

Place multiple orders simultaneously in a single API call. Ideal for portfolio rebalancing, multi-stock strategies, or executing correlated trades.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/basketorder
```

## Sample API Request

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "orders": [
    {
      "symbol": "INFY",
      "exchange": "NSE",
      "action": "BUY",
      "quantity": "2",
      "pricetype": "MARKET",
      "product": "MIS"
    },
    {
      "symbol": "TCS",
      "exchange": "NSE",
      "action": "BUY",
      "quantity": "2",
      "pricetype": "MARKET",
      "product": "MIS"
    }
  ]
}
```

## Sample API Response

```json
{
  "status": "success",
  "results": [
    {
      "symbol": "INFY",
      "status": "success",
      "orderid": "260415000382403"
    },
    {
      "symbol": "TCS",
      "status": "success",
      "orderid": "260415000382404"
    }
  ]
}
```

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier | Optional | - |
| orders | Array of order objects | Mandatory | - |

### Order Object Fields

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| symbol | Trading symbol | Mandatory | - |
| exchange | Exchange code: NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCDEX | Mandatory | - |
| action | Order action: BUY or SELL | Mandatory | - |
| quantity | Order quantity | Mandatory | - |
| pricetype | Price type: MARKET, LIMIT, SL, SL-M | Mandatory | - |
| product | Product type: MIS, CNC, NRML | Mandatory | - |
| price | Order price (for LIMIT orders) | Optional | 0 |
| trigger_price | Trigger price (for SL orders) | Optional | 0 |

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| status | string | "success" if at least one order succeeded |
| results | array | Array of individual order results |

### Results Array Fields

| Field | Type | Description |
|-------|------|-------------|
| symbol | string | Trading symbol |
| status | string | "success" or "error" |
| orderid | string | Order ID from broker (on success) |
| message | string | Error message (on failure) |

## Notes

- **BUY-before-SELL ordering is enforced server-side.** All BUY legs are placed first (concurrently via a thread pool), then all SELL legs — this frees up margin from the BUY purchases before the SELLs hit, important for credit spreads and multi-leg option strategies that would otherwise be margin-blocked.
- Within each BUY / SELL batch, orders fire **concurrently** via `ThreadPoolExecutor` — the basket completes in roughly the time of one slow broker call, not N × that.
- Each order is independent — if some fail, others still complete. The response always carries `status: "success"` at the envelope level; per-order outcomes are in `results[]`.
- **Sandbox mode** — when the global trading mode is `sandbox`, every leg is dispatched to the simulator sequentially (no concurrency — sandbox is a local DB write, not a network call) but in the same BUY-then-SELL order.
- Maximum orders per basket depends on broker limits.
- Use for:
  - **Portfolio rebalancing**: Buy/sell multiple stocks together.
  - **Pair trading**: Simultaneous long/short positions.
  - **Multi-leg option strategies**: Execute Iron Condor / Butterfly / Strangle as a single call. (The Strategy Builder's "Execute Basket" button uses this endpoint.)

## Example Use Cases

### Portfolio Rebalancing
```json
{
  "apikey": "<your_openbull_apikey>",
  "strategy": "Rebalance",
  "orders": [
    {"symbol": "TCS", "exchange": "NSE", "action": "BUY", "quantity": "5", "pricetype": "MARKET", "product": "CNC"},
    {"symbol": "INFY", "exchange": "NSE", "action": "BUY", "quantity": "10", "pricetype": "MARKET", "product": "CNC"},
    {"symbol": "WIPRO", "exchange": "NSE", "action": "SELL", "quantity": "8", "pricetype": "MARKET", "product": "CNC"}
  ]
}
```

### Pair Trading
```json
{
  "apikey": "<your_openbull_apikey>",
  "strategy": "PairTrade",
  "orders": [
    {"symbol": "SBIN", "exchange": "NSE", "action": "BUY", "quantity": "100", "pricetype": "MARKET", "product": "MIS"},
    {"symbol": "BANKBARODA", "exchange": "NSE", "action": "SELL", "quantity": "200", "pricetype": "MARKET", "product": "MIS"}
  ]
}
```

---

**Back to**: [API Documentation](../README.md)
