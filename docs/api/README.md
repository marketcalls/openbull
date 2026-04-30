# OpenBull API Documentation

REST + WebSocket API documentation for the OpenBull Options Trading Platform. Endpoints follow the OpenAlgo standard format for cross-compatibility — existing OpenAlgo SDKs / scripts work against OpenBull unchanged.

## Base URL

```http
Local Host    :  http://127.0.0.1:8000/api/v1
Custom Domain :  https://<your-custom-domain>/api/v1
```

The same backend serves the web UI at port `5173` (dev) or behind nginx at `/` (prod). API endpoints live under `/api/v1` (key-auth) and `/web` (cookie-auth — used by the SPA).

## Authentication

### External API (`/api/v1/*`) — API key

Generate an API key from the in-app `/apikey` page. Pass it in the request body:

```json
{
  "apikey": "<your_openbull_apikey>"
}
```

…or as a header:

```http
X-API-KEY: <your_openbull_apikey>
```

### Web API (`/web/*`) — session cookie

Used by the React SPA. JWT in an httpOnly cookie, established by `/web/auth/login`. Not intended for external consumers — use `/api/v1/*` for SDK integration.

## API Categories

### Order Management
Place, modify, and cancel orders across all supported exchanges.

| Endpoint | Description |
|----------|-------------|
| [PlaceOrder](./order-management/placeorder.md) | Place a new order |
| [PlaceSmartOrder](./order-management/placesmartorder.md) | Place position-aware smart order |
| [OptionsOrder](./order-management/optionsorder.md) | Place options order with offset |
| [OptionsMultiOrder](./order-management/optionsmultiorder.md) | Place multi-leg options strategy |
| [BasketOrder](./order-management/basketorder.md) | Place multiple orders simultaneously (BUY-before-SELL ordering) |
| [SplitOrder](./order-management/splitorder.md) | Split large order into smaller chunks |
| [ModifyOrder](./order-management/modifyorder.md) | Modify an existing order |
| [CancelOrder](./order-management/cancelorder.md) | Cancel a specific order |
| [CancelAllOrder](./order-management/cancelallorder.md) | Cancel all open orders |
| [ClosePosition](./order-management/closeposition.md) | Close all open positions |

### Order Information
Query order status and per-symbol position information.

| Endpoint | Description |
|----------|-------------|
| [OrderStatus](./order-information/orderstatus.md) | Get current status of an order |
| [OpenPosition](./order-information/openposition.md) | Get net qty for a symbol position |

### Market Data
Real-time and historical market data.

| Endpoint | Description |
|----------|-------------|
| [Quotes](./market-data/quotes.md) | Get LTP / OHLC quotes for a symbol |
| [MultiQuotes](./market-data/multiquotes.md) | Get quotes for multiple symbols in one call |
| [Depth](./market-data/depth.md) | Get 5-level market depth |
| [History](./market-data/history.md) | Get historical OHLCV candles |
| [Intervals](./market-data/intervals.md) | List supported candle intervals for the broker |

### Symbol Services
Symbol lookup, search, and instrument data.

| Endpoint | Description |
|----------|-------------|
| [Symbol](./symbol-services/symbol.md) | Get full symbol information |
| [Search](./symbol-services/search.md) | Tokenized async search across the master-contract table |
| [Expiry](./symbol-services/expiry.md) | Get expiry dates for an underlying |

### Options Services
Options-specific operations and analytics.

| Endpoint | Description |
|----------|-------------|
| [OptionSymbol](./options-services/optionsymbol.md) | Resolve OpenAlgo option symbol from offset |
| [OptionChain](./options-services/optionchain.md) | Get full option chain with live quotes (CE + PE per strike) |
| [OptionGreeks](./options-services/optiongreeks.md) | Black-76 IV + Delta / Gamma / Theta / Vega for the ATM CE & PE |
| [SyntheticFuture](./options-services/syntheticfuture.md) | Synthetic future from put-call parity |

### Account Services
Funds, margin, orders, positions, holdings.

| Endpoint | Description |
|----------|-------------|
| [Funds](./account-services/funds.md) | Account funds + utilised margin |
| [Margin](./account-services/margin.md) | Pre-trade margin calculator (basket-aware: hedge-benefits applied) |
| [OrderBook](./account-services/orderbook.md) | All orders for the day |
| [TradeBook](./account-services/tradebook.md) | All trades for the day |
| [PositionBook](./account-services/positionbook.md) | Current positions (net qty) |
| [Holdings](./account-services/holdings.md) | Portfolio holdings |

### Analytics Tools
Server-computed options analytics powering the in-app `/tools/*` pages. Open to external consumers via `/api/v1`.

| Endpoint | Description |
|----------|-------------|
| `oitracker` | OI change tracker — per-strike OI deltas across the chain |
| `maxpain` | Max Pain calculation for a given expiry |
| `ivchart` | Historical ATM IV time series |
| `ivsmile` | Per-strike CE / PE IV at the chosen expiry — also exposes `atm_iv` |
| `volsurface` | Full IV surface across strikes × expiries (3-D plot data) |
| `straddle` | Historical ATM straddle premium (CE+PE) + synthetic future |
| `gex` | Gamma exposure — chain-wide gamma profile, ZGI, GEX flip |

### WebSocket Streaming
Real-time tick stream for the user's broker feed. Not part of `/api/v1` — separate WebSocket endpoint.

| Mode | Description |
|------|-------------|
| [LTP](./websocket-streaming/ltp.md) | Last traded price + change/cp/ltq |
| [Quote](./websocket-streaming/quote.md) | OHLCV + OI + volume + bid/ask totals |
| [Depth](./websocket-streaming/depth.md) | 5-level bid/ask market depth |

Full protocol: [`docs/design/websockets-format.md`](../design/websockets-format.md).

## Web-only API (`/web/*`)

Cookie-authed endpoints used by the SPA. Not key-authed; not part of the OpenAlgo-compatible surface. Documented here for completeness — third parties should integrate via `/api/v1` instead.

### Auth & Setup
`auth/check-setup`, `auth/setup`, `auth/login`, `auth/logout`, `auth/me`, `auth/broker-redirect`, broker OAuth callbacks (`upstox/callback`, `zerodha/callback`, …).

### Strategies (saved multi-leg strategies)
| Endpoint | Description |
|----------|-------------|
| `strategies` | CRUD: list / get / create / update / delete saved strategies |
| `strategybuilder/snapshot` | One-shot live pricing for a leg set (spot + per-leg LTP + IV + Greeks + position totals) |
| `strategybuilder/chart` | Historical combined-premium time series for a leg set, with openalgo-parity columns (`net_premium`, `combined_premium`, `tag`) |
| `strategybuilder/multi-strike-oi` | Per-leg historical Open Interest series + underlying close on a common timeline |

### Trading Mode
`trading-mode` (GET / POST) — global Live ↔ Sandbox toggle.

### Sandbox
`sandbox/config`, `sandbox/reset`, `sandbox/summary`, `sandbox/mypnl`, `sandbox/squareoff-now`, `sandbox/settle-now`.

### Symbols
`symbols/search`, `symbols/underlyings`, `symbols/status` (master-contract download status).

### Logs
`api-logs`, `error-logs`.

## Supported Brokers

All five broker plugins ship REST + streaming, with auth, orders, funds, history, depth, margin, and a WebSocket adapter feeding the unified WS proxy.

| Broker | Auth flow | Streaming protocol |
|--------|-----------|---------------------|
| **Upstox** | OAuth (request token → access token) | Protobuf v3 |
| **Zerodha** | Kite Connect OAuth | KiteTicker binary |
| **Angel One** (SmartAPI) | API + TOTP secret | Angel SmartStream binary |
| **Dhan** | Static access token | Dhan binary |
| **Fyers** | OAuth (auth code → access token) | HSM binary (fyers v3 streaming) |

A new broker = one folder under `backend/broker/{name}/` + a `plugin.json`. See [docs/design/broker-integration.md](../design/broker-integration.md) for the full guide.

## Order Constants

### Exchange Codes
| Code | Description |
|------|-------------|
| `NSE` | National Stock Exchange (Equity) |
| `BSE` | Bombay Stock Exchange (Equity) |
| `NFO` | NSE Futures & Options |
| `BFO` | BSE Futures & Options |
| `CDS` | Currency Derivatives (NSE) |
| `BCD` | Currency Derivatives (BSE) |
| `MCX` | Multi Commodity Exchange |
| `NSE_INDEX` | NSE Index quote feed |
| `BSE_INDEX` | BSE Index quote feed |

### Product Types
| Code | Description |
|------|-------------|
| `MIS` | Margin Intraday Square-off |
| `CNC` | Cash and Carry (Delivery) |
| `NRML` | Normal (F&O Overnight) |

### Price Types
| Code | Description |
|------|-------------|
| `MARKET` | Market order |
| `LIMIT` | Limit order |
| `SL` | Stop-loss limit |
| `SL-M` | Stop-loss market |

### Symbol Format

OpenAlgo-compatible. See [docs/design/symbol-format.md](../design/symbol-format.md) for the full spec.

```
Equity:   RELIANCE, SBIN, TCS, INFY
Index:    NIFTY, BANKNIFTY, FINNIFTY, SENSEX
Futures:  NIFTY28APR26FUT
Options:  NIFTY28APR2624250CE
Currency: USDINR27MAY2684CE
Commodity: CRUDEOIL19MAY266100CE
```

## Response Format

All `/api/v1` endpoints follow the OpenAlgo standard envelope.

### Success
```json
{"status": "success", "data": {...}}
```

### Error
```json
{"status": "error", "message": "Error description"}
```

Status codes follow REST norms (`200`, `400`, `401`, `404`, `429`, `500`). Error bodies always include `message`.

## Rate Limits

Backed by SlowAPI; configurable in `backend/main.py`. Limits apply per IP (or per API key when authenticated).

| API surface | Default limit |
|-------------|---------------|
| Order Management | 10 / second |
| General APIs | 50 / second |
| Login (`/web/auth/login`) | 5 / minute, 25 / hour |
| Strategy Chart, Multi-Strike OI | 30 / minute |

429s carry a `Retry-After` header. Broker-side 429s on transient calls are retried in-process (Fyers / Upstox).

## Trading Mode

Every order/info path checks the global `trading-mode` setting. When sandbox is active:

- Order endpoints route to `backend/sandbox/` instead of the broker plugin.
- Position / funds / orderbook reads return sandbox state.
- WebSocket subscriptions still hit the live feed (sandbox isn't a backtester — it simulates fills against live prices).

Toggle via `POST /web/trading-mode` (admin-only) or the topbar switch in the UI.

## Logging

Every API call lands in `api_logs` (DB) with request id, status, latency, and the redacted request body. Filterable in `/logs`. Errors also land in `error_logs` with a stack trace; both tables are bounded so attacker floods can't blow up storage.
