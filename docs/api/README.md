# OpenBull API Documentation

REST API documentation for the OpenBull Options Trading Platform. All endpoints follow the OpenAlgo standard format for cross-compatibility.

## Base URL

```http
http://127.0.0.1:8000/api/v1
```

## Authentication

Include your API key in the request body or as a header:

```json
{
  "apikey": "<your_openbull_apikey>"
}
```

Or via header:
```
X-API-KEY: <your_openbull_apikey>
```

## API Categories

### Order Management

| Endpoint | Description |
|----------|-------------|
| [PlaceOrder](./order-management/placeorder.md) | Place a new order |
| [PlaceSmartOrder](./order-management/placesmartorder.md) | Place position-aware smart order |
| [OptionsOrder](./order-management/optionsorder.md) | Place options order with offset |
| [OptionsMultiOrder](./order-management/optionsmultiorder.md) | Place multi-leg options strategy |
| [BasketOrder](./order-management/basketorder.md) | Place multiple orders simultaneously |
| [SplitOrder](./order-management/splitorder.md) | Split large order into smaller chunks |
| [ModifyOrder](./order-management/modifyorder.md) | Modify an existing order |
| [CancelOrder](./order-management/cancelorder.md) | Cancel a specific order |
| [CancelAllOrder](./order-management/cancelallorder.md) | Cancel all open orders |
| [ClosePosition](./order-management/closeposition.md) | Close all open positions |

### Order Information

| Endpoint | Description |
|----------|-------------|
| [OrderStatus](./order-information/orderstatus.md) | Get status of a specific order |
| [OpenPosition](./order-information/openposition.md) | Get net qty for a symbol position |

### Market Data

| Endpoint | Description |
|----------|-------------|
| [Quotes](./market-data/quotes.md) | Get LTP/OHLC quotes for a symbol |
| [MultiQuotes](./market-data/multiquotes.md) | Get quotes for multiple symbols |
| [Depth](./market-data/depth.md) | Get 5-level market depth |
| [History](./market-data/history.md) | Get historical OHLCV candles |
| [Intervals](./market-data/intervals.md) | Get supported candle intervals |

### Symbol Services

| Endpoint | Description |
|----------|-------------|
| [Symbol](./symbol-services/symbol.md) | Get full symbol information |
| [Search](./symbol-services/search.md) | Search for symbols |
| [Expiry](./symbol-services/expiry.md) | Get expiry dates for F&O |

### Options Services

| Endpoint | Description |
|----------|-------------|
| [OptionSymbol](./options-services/optionsymbol.md) | Resolve option symbol from offset |
| [OptionChain](./options-services/optionchain.md) | Get option chain with live quotes |
| [OptionGreeks](./options-services/optiongreeks.md) | Calculate Greeks and IV |
| [SyntheticFuture](./options-services/syntheticfuture.md) | Calculate synthetic future price |

### Account Services

| Endpoint | Description |
|----------|-------------|
| [Funds](./account-services/funds.md) | Get account funds/margin |
| [Margin](./account-services/margin.md) | Calculate margin for positions |
| [OrderBook](./account-services/orderbook.md) | Get all orders for the day |
| [TradeBook](./account-services/tradebook.md) | Get all trades for the day |
| [PositionBook](./account-services/positionbook.md) | Get current positions |
| [Holdings](./account-services/holdings.md) | Get portfolio holdings |

### WebSocket Streaming

| Mode | Description |
|------|-------------|
| [LTP](./websocket-streaming/ltp.md) | Last traded price updates |
| [Quote](./websocket-streaming/quote.md) | OHLCV + OI quote updates |
| [Depth](./websocket-streaming/depth.md) | 5-level market depth updates |

## Supported Brokers

| Broker | Status |
|--------|--------|
| Upstox | Supported (REST + WebSocket) |
| Zerodha | Supported (REST + WebSocket) |

## Order Constants

### Exchange Codes
| Code | Description |
|------|-------------|
| NSE | National Stock Exchange (Equity) |
| BSE | Bombay Stock Exchange (Equity) |
| NFO | NSE Futures & Options |
| BFO | BSE Futures & Options |
| CDS | Currency Derivatives |
| BCD | Currency Derivatives (BSE) |
| MCX | Multi Commodity Exchange |
| NSE_INDEX | NSE Index |
| BSE_INDEX | BSE Index |

### Product Types
| Code | Description |
|------|-------------|
| MIS | Margin Intraday Square-off |
| CNC | Cash and Carry (Delivery) |
| NRML | Normal (F&O Overnight) |

### Price Types
| Code | Description |
|------|-------------|
| MARKET | Market order |
| LIMIT | Limit order |
| SL | Stop-loss limit |
| SL-M | Stop-loss market |

### Symbol Format

```
Equity:   RELIANCE, SBIN, TCS
Futures:  NIFTY28APR26FUT
Options:  NIFTY28APR2624250CE
```

## Response Format

### Success
```json
{"status": "success", "data": {...}}
```

### Error
```json
{"status": "error", "message": "Error description"}
```

## Rate Limits

| API Type | Rate Limit |
|----------|------------|
| Order Management | 10 per second |
| General APIs | 50 per second |
| Login | 5 per minute, 25 per hour |
