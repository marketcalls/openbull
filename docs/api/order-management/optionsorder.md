# OptionsOrder

Place an options order by specifying offset (ATM/ITM/OTM) instead of exact strike price. The API automatically resolves the correct option symbol based on the current underlying price.

## Endpoint URL

```http
POST http://127.0.0.1:8000/api/v1/optionsorder
```

## Sample API Request (ATM Option)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "offset": "ATM",
  "option_type": "CE",
  "action": "BUY",
  "quantity": "65",
  "pricetype": "MARKET",
  "product": "MIS"
}
```

## Sample API Response (ATM Option)

```json
{
  "exchange": "NFO",
  "offset": "ATM",
  "option_type": "CE",
  "orderid": "260415000386285",
  "status": "success",
  "symbol": "NIFTY28APR2624250CE",
  "underlying": "NIFTY",
  "underlying_ltp": 24231.30
}
```

## Sample API Request (ITM Option)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "offset": "ITM4",
  "option_type": "PE",
  "action": "BUY",
  "quantity": "65",
  "pricetype": "MARKET",
  "product": "NRML"
}
```

## Sample API Request (OTM Option)

```json
{
  "apikey": "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6",
  "strategy": "Python",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "expiry_date": "28APR26",
  "offset": "OTM5",
  "option_type": "CE",
  "action": "BUY",
  "quantity": "65",
  "pricetype": "MARKET",
  "product": "NRML"
}
```

## Offset Values

| Offset | Description |
|--------|-------------|
| ATM | At-The-Money (strike closest to current price) |
| ITM1 to ITM50 | In-The-Money (1-50 strikes away) |
| OTM1 to OTM50 | Out-of-The-Money (1-50 strikes away) |

### Understanding ITM/OTM for CE and PE

| Option Type | ITM Direction | OTM Direction |
|-------------|---------------|---------------|
| CE (Call) | Lower strikes | Higher strikes |
| PE (Put) | Higher strikes | Lower strikes |

## Request Body

| Parameter | Description | Mandatory/Optional | Default Value |
|-----------|-------------|-------------------|---------------|
| apikey | Your OpenBull API key | Mandatory | - |
| strategy | Strategy identifier | Optional | - |
| underlying | Underlying symbol (NIFTY, BANKNIFTY, etc.) | Mandatory | - |
| exchange | Exchange: NSE_INDEX, BSE_INDEX, NFO, BFO | Mandatory | - |
| expiry_date | Expiry date in DDMMMYY format (e.g., 28APR26) | Mandatory | - |
| offset | Strike offset: ATM, ITM1-ITM50, OTM1-OTM50 | Mandatory | - |
| option_type | Option type: CE or PE | Mandatory | - |
| action | Order action: BUY or SELL | Mandatory | - |
| quantity | Order quantity | Mandatory | - |
| pricetype | Price type: MARKET, LIMIT, SL, SL-M | Mandatory | - |
| product | Product type: MIS or NRML | Mandatory | - |
| splitsize | Split order into chunks (0 = no split) | Optional | 0 |
| price | Limit price (for LIMIT orders) | Optional | 0 |
| trigger_price | Trigger price (for SL orders) | Optional | 0 |

## Response Fields

Response shape **differs based on `splitsize`**:

### Without split (default — `splitsize=0` or omitted)

| Field | Type | Description |
|-------|------|-------------|
| status | string | `"success"` or `"error"` |
| symbol | string | Resolved option symbol (e.g. `NIFTY28APR2624250CE`) |
| exchange | string | Resolved options exchange (`NFO` for `NSE_INDEX`, `BFO` for `BSE_INDEX`) |
| underlying | string | Echo of the request `underlying` |
| underlying_ltp | number | Spot LTP used for ATM resolution |
| offset | string | Echo of the request `offset` |
| option_type | string | Echo of the request `option_type` |
| orderid | string | Broker order ID for the placed order |

### With split (`splitsize > 0`)

The `orderid` field is replaced with a nested `split` object containing the SplitOrder response — one row per child order:

```json
{
  "status": "success",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO",
  "underlying": "NIFTY",
  "underlying_ltp": 24231.30,
  "offset": "ATM",
  "option_type": "CE",
  "split": {
    "status": "success",
    "split_size": 50,
    "total_quantity": 150,
    "results": [
      {"order_num": 1, "quantity": 50, "orderid": "260415000386285", "status": "success"},
      {"order_num": 2, "quantity": 50, "orderid": "260415000386286", "status": "success"},
      {"order_num": 3, "quantity": 50, "orderid": "260415000386287", "status": "success"}
    ]
  }
}
```

See [SplitOrder](./splitorder.md) for the inner `split` object shape.

## Notes

- The `underlying` is used to fetch the current LTP for ATM resolution.
- For `NSE_INDEX` the order routes to `NFO`; for `BSE_INDEX` to `BFO`; for `MCX_INDEX` to `MCX`.
- The `expiry_date` is `DDMMMYY` on input (`28APR26`). It's not echoed in the response — the resolved `symbol` already encodes it.
- Use `splitsize` to break large orders into smaller chunks (max 100 child orders per split).
- ATM resolution uses the spot LTP — not the synthetic future — for compatibility with OpenAlgo SDKs that pass `NSE_INDEX` as the exchange.

---

**Back to**: [API Documentation](../README.md)
