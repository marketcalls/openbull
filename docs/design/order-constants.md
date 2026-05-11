# Order Constants

Canonical enums for every order placed through OpenBull. Validation source of truth lives at `backend/utils/constants.py`; the API and service layers reject any value outside these sets with `status: "error"` and HTTP 400.

## Exchange

| Code | Description |
|---|---|
| `NSE` | National Stock Exchange — cash equities |
| `BSE` | Bombay Stock Exchange — cash equities |
| `NFO` | NSE Futures & Options |
| `BFO` | BSE Futures & Options |
| `CDS` | NSE Currency Derivatives |
| `BCD` | BSE Currency Derivatives |
| `MCX` | Multi Commodity Exchange |
| `NCDEX` | National Commodity & Derivatives Exchange |
| `NSE_INDEX` | NSE index quote feed (`NIFTY`, `BANKNIFTY`, `FINNIFTY`, …) |
| `BSE_INDEX` | BSE index quote feed (`SENSEX`, `BANKEX`, …) |
| `MCX_INDEX` | MCX commodity index feed |

`*_INDEX` codes are **read-only** — they appear in market-data and symbol endpoints (quotes, history, depth, ticker) but not in order placement, since indices are not tradable on their own. Order endpoints reject them with a 400.

A specific broker's `plugin.json` advertises its `supported_exchanges` subset; the order service additionally checks against that subset before dispatching to the broker.

## Product Type

| Code | Description |
|---|---|
| `MIS` | Margin Intraday Square-off — leveraged, force-closed at the exchange bucket cut-off |
| `CNC` | Cash & Carry — equity delivery (T+1 settlement) |
| `NRML` | Normal — overnight F&O positions, normal margin |

Broker-side mapping (e.g. Upstox's `D` ↔ `CNC`) is handled in each plugin's `mapping/transform_data.py` — services and clients only ever see the canonical OpenBull codes above.

## Price Type

| Code | Description | Requires `price` | Requires `trigger_price` |
|---|---|---|---|
| `MARKET` | Market order — fills at the best available price | — | — |
| `LIMIT` | Limit order — fills at `price` or better | yes | — |
| `SL` | Stop-Loss Limit — once `trigger_price` is hit, a LIMIT order is placed at `price` | yes | yes |
| `SL-M` | Stop-Loss Market — once `trigger_price` is hit, a MARKET order is placed | — | yes |

## Action

| Code | Description |
|---|---|
| `BUY` | Buy |
| `SELL` | Sell |

Both are auto-uppercased by the API layer before validation.

## Validation Reference

```python
# backend/utils/constants.py
VALID_EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NCDEX",
                   "NSE_INDEX", "BSE_INDEX", "MCX_INDEX"}
VALID_PRODUCT_TYPES = {"CNC", "NRML", "MIS"}
VALID_PRICE_TYPES   = {"MARKET", "LIMIT", "SL", "SL-M"}
VALID_ACTIONS       = {"BUY", "SELL"}
```

A request with any field outside these sets is rejected by `validate_order_data` (`backend/services/order_service.py`) before the broker plugin is invoked.

## See also

- [Symbol Format](./symbol-format.md) — canonical symbol strings per instrument type
- [Broker Integration](./broker-integration.md) — how a plugin advertises its `supported_exchanges` subset and how broker-side codes map to the canonical set above
