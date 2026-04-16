# Services Layer Documentation

The services layer (`backend/services/`) contains all business logic. API endpoints are thin handlers that resolve auth, parse the request body, call a service function, and return the response. Services are broker-agnostic — they use `importlib` to dynamically load the correct broker module.

## Service Catalog

### Order Services

| Service | File | Functions | Description |
|---------|------|-----------|-------------|
| **Order** | `order_service.py` | `place_order`, `place_order_with_auth`, `place_smart_order`, `modify_order_service`, `cancel_order_service`, `cancel_all_orders_service`, `close_all_positions_service`, `validate_order_data` | Core order placement, modification, and cancellation. Validates fields against `VALID_EXCHANGES`, `VALID_ACTIONS`, `VALID_PRICE_TYPES`, `VALID_PRODUCT_TYPES`. |
| **Basket Order** | `basket_order_service.py` | `place_basket_order` | Places multiple orders concurrently. BUY legs execute before SELL legs for margin efficiency. Batched in groups of 10 with 1s delay between batches. |
| **Split Order** | `split_order_service.py` | `split_order` | Splits a large quantity into chunks of `splitsize` plus remainder. Sequential execution with rate-limit delay from `ORDER_RATE_LIMIT`. Max 100 orders. |
| **Options Order** | `place_options_order_service.py` | `place_options_order` | Resolves offset (ATM/ITMn/OTMn) to a tradable symbol via `option_symbol_service`, then places the order. Supports optional `splitsize` for large quantities. |
| **Options MultiOrder** | `options_multiorder_service.py` | `place_options_multiorder` | Multi-leg options strategy (e.g., Iron Condor). Resolves each leg's offset independently, supports per-leg `expiry_date` override (diagonal spreads) and per-leg `splitsize`. BUY legs placed before SELL legs. |

### Account Services

| Service | File | Functions | Description |
|---------|------|-----------|-------------|
| **Funds** | `funds_service.py` | `get_funds_with_auth` | Fetches account balance/margin from broker. |
| **Margin** | `margin_service.py` | `calculate_margin` | Pre-trade margin calculator for a basket of positions (max 50). Validates each position. Loads `broker.{name}.api.margin_api`. |
| **OrderBook** | `orderbook_service.py` | `get_orderbook_with_auth` | Fetches all orders for the day. |
| **TradeBook** | `tradebook_service.py` | `get_tradebook_with_auth` | Fetches all trades for the day. |
| **Positions** | `positions_service.py` | `get_positions_with_auth` | Fetches current open positions. |
| **Holdings** | `holdings_service.py` | `get_holdings_with_auth` | Fetches portfolio holdings. |
| **OrderStatus** | `orderstatus_service.py` | `get_orderstatus_with_auth` | Fetches status of a specific order by `orderid`. |
| **OpenPosition** | `openposition_service.py` | `get_openposition_with_auth` | Returns net quantity for a specific symbol/exchange/product. Calls `broker.order_api.get_open_position`. |

### Market Data Services

| Service | File | Functions | Description |
|---------|------|-----------|-------------|
| **Quotes** | `quotes_service.py` | `get_quotes_with_auth`, `get_multi_quotes_with_auth` | Single and multi-symbol quotes. Response key is `results` (not `data`) for multi-quotes, matching OpenAlgo format. |
| **Depth** | `depth_service.py` | `get_depth_with_auth` | 5-level market depth (bid/ask). |
| **History** | `history_service.py` | `get_history_with_auth` | Historical OHLCV candles. Accepts `interval`, `start_date`, `end_date`. |
| **Market Data** | `market_data_service.py` | `get_symbol_info`, `search_symbols_api`, `get_expiry_dates`, `get_supported_intervals` | Symbol lookup, search, expiry dates (with `instrumenttype` filter: "options"/"futures"), and supported intervals. All DB-only — no broker API calls. |

### Options Analytics Services

| Service | File | Functions | Description |
|---------|------|-----------|-------------|
| **Option Symbol** | `option_symbol_service.py` | `get_option_symbol`, `clear_strikes_cache` | Resolves underlying+expiry+offset+type to a tradable symbol. Fetches LTP from broker quotes, finds ATM from actual DB strikes, applies ITM/OTM offset. In-memory strikes cache. |
| **Option Chain** | `option_chain_service.py` | `get_option_chain` | Builds a strikes-around-ATM chain. Queries symtoken for CE+PE metadata per strike, fetches live quotes via `get_multi_quotes_with_auth`, labels each strike (ATM/ITMn/OTMn for both CE and PE). |
| **Option Greeks** | `option_greeks_service.py` | `get_option_greeks` | Black-76 Greeks (delta, gamma, theta, vega, rho) and implied volatility. Pure-math implementation (no scipy/py_vollib). Bisection IV solver (80 iterations). Auto-fetches spot+option LTP from broker if not provided. Deep-ITM fallback returns theoretical greeks. |
| **Synthetic Future** | `synthetic_future_service.py` | `calculate_synthetic_future` | Computes synthetic future price from ATM CE+PE via multi-quotes. Formula: Strike + Call_LTP - Put_LTP. Returns basis (synthetic - spot). |

### Other Services

| Service | File | Functions | Description |
|---------|------|-----------|-------------|
| **Symbol** | `symbol_service.py` | Symbol-related helpers | Used by search/symbol endpoints. |
| **Master Contract** | `master_contract_status.py` | Download status tracking | Tracks master contract download progress. |

## Common Patterns

### Dynamic Broker Loading

Every service that calls a broker API follows this pattern:

```python
def _import_broker_module(broker_name: str):
    return importlib.import_module(f"backend.broker.{broker_name}.api.order_api")

module = _import_broker_module(broker)
result = module.place_order_api(data, auth_token)
```

### Return Signature

All public service functions return:

```python
(success: bool, response_data: dict, http_status_code: int)
```

### Validation

Order services validate against constants in `backend/utils/constants.py`:

```python
VALID_EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", ...}
VALID_PRODUCT_TYPES = {"CNC", "NRML", "MIS"}
VALID_PRICE_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
VALID_ACTIONS = {"BUY", "SELL"}
```

### DB Queries (sync from async context)

Services that query the DB from a synchronous context use:

```python
def _run_query(query_str, params):
    with ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _query_db(query_str, params)).result()
```

This is used by `market_data_service`, `option_symbol_service`, and `option_greeks_service`.
