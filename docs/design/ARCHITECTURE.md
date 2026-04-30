# OpenBull Architecture

## System Overview

```
                              React Frontend (port 5173)
                                       |
                              FastAPI Backend (port 8000)
                                       |
       +-------------------+-----------+------------+--------------------+
       |                   |                        |                    |
   Web Routes          /api/v1                WebSocket Proxy        Lifespan jobs
   (JWT cookie)        (API key)              (port 8765, API key)   (sandbox engine,
       |                   |                        |                  schedulers,
       |                   |                        |                  api-log writer)
       +-------+-----------+------+-----------------+
               |                  |                 |
        TradingMode dispatch      |          ZeroMQ PUB/SUB bus
        (live | sandbox)          |                 |
         |              |         |          Broker Adapter (thread)
   Live broker     Sandbox        |                 |
   (importlib)     engine         |          Upstox / Zerodha WS feed
         |          |             |
         |          +---> sandbox_* tables
         |
   broker/{name}/api -----> Upstox REST / Kite REST
                    \-----> mapping/  (transform_data, order_data)

       |                   |
       +---------+---------+
                 |
         PostgreSQL (asyncpg)        Redis 7 (cache-aside)
         + ORM models                 - api_key:valid|invalid:<hash>
         - users, sessions            - broker_ctx:<user_id>
         - api_keys                   - api_ctx:<user_id>
         - broker_auth/config         - symtoken:<hash>  (master contract mirror)
         - symtoken                   - md:<exch>:<sym>  (live tick snapshots)
         - sandbox_orders/trades
         - sandbox_positions
         - sandbox_funds/holdings
         - app_settings (mode)
         - api_logs / error_logs
                 |
            +----+-----+
            |          |
   MarketDataCache    Logging stack
   (singleton)         - request-id contextvar (X-Request-ID)
   - subscribes to     - sensitive-data redaction
     every ZMQ tick    - rotating files openbull.log/openbull-error.log
   - priority fanout   - DB error_logs sink (worker-trimmed)
     CRITICAL..LOW     - DB api_logs sink (auth-gated middleware)
   - mirrors to Redis
   - feeds RMS,
     sandbox engine,
     /api/websocket/*
```

The frontend talks to the backend over HTTP for everything except live ticks, which flow over the dedicated WebSocket proxy. Every order/position/funds path passes through a `TradingMode` dispatcher; when the user has flipped to sandbox mode the call lands on the simulated engine instead of the broker. Redis is the second-level cache for both auth context and market-data snapshots; PostgreSQL remains the durable system of record.

## Directory Structure

```
openbull/
├── alembic/
│   ├── env.py                       # Async-aware Alembic env
│   └── versions/                    # Migration scripts (additive on top of create_all)
├── alembic.ini
├── install/
│   ├── install.sh                   # First-time deploy: nginx, systemd, env, redis
│   ├── update.sh                    # Pull + migrate + reload services
│   └── perftuning.sh                # Postgres/redis kernel + ulimit tuning
├── backend/
│   ├── main.py                      # FastAPI app, lifespan, middleware order
│   ├── config.py                    # Pydantic Settings (.env)
│   ├── database.py                  # SQLAlchemy async engine + Base
│   ├── security.py                  # Argon2, Fernet, JWT helpers
│   ├── dependencies.py              # FastAPI DI: get_db, get_current_user,
│   │                                #   get_broker_context, get_api_user
│   │                                #   (Redis cache-aside on all auth paths)
│   ├── exceptions.py                # OpenBullException + handler
│   ├── limiter.py                   # SlowAPI limiter
│   ├── middleware.py                # RequestLoggingMiddleware (request-id, latency)
│   ├── middleware_api_log.py        # ApiLogMiddleware (auth-gated DB log)
│   ├── models/                      # SQLAlchemy ORM
│   │   ├── user.py, auth.py, audit.py
│   │   ├── broker_config.py
│   │   ├── settings.py              # app_settings (trading_mode, sandbox_config)
│   │   ├── symbol.py                # symtoken master contract
│   │   ├── sandbox.py               # sandbox_orders/trades/positions/funds/holdings
│   │   └── strategies.py            # saved multi-leg option strategies (JSONB legs)
│   ├── schemas/                     # Pydantic request/response
│   ├── routers/                     # Web routes (JWT cookie auth)
│   │   ├── auth.py, broker_config.py, broker_oauth.py
│   │   ├── dashboard.py, orderbook.py, tradebook.py,
│   │   │   positions.py, holdings.py
│   │   ├── api_key.py, websocket.py
│   │   ├── symbols.py               # OpenAlgo-style symbol search
│   │   ├── api_logs.py, error_logs.py   # Auth-gated log viewers
│   │   ├── trading_mode.py          # GET/PUT live|sandbox toggle
│   │   ├── sandbox.py               # /sandbox config + reset
│   │   ├── strategies.py            # /web/strategies CRUD
│   │   └── strategybuilder.py       # /web/strategybuilder/{snapshot,chart}
│   ├── api/                         # External API (/api/v1, API key auth)
│   │   ├── place_order.py, basket_order.py, split_order.py
│   │   ├── orderbook.py, tradebook.py, positions.py, holdings.py
│   │   ├── orderstatus.py, openposition.py, funds.py, margin.py
│   │   ├── symbol.py, search.py, expiry.py, intervals.py, history.py
│   │   ├── quotes.py, multiquotes.py, depth.py
│   │   ├── optionsymbol.py, optionchain.py, syntheticfuture.py
│   │   ├── optionsorder.py, optionsmultiorder.py, optiongreeks.py
│   │   ├── oitracker.py, maxpain.py, ivchart.py, ivsmile.py,
│   │   │   volsurface.py, straddle.py, gex.py
│   │   └── analyzer.py, ping.py
│   ├── services/                    # Business logic
│   │   ├── order_service.py         # TradingMode dispatch lives here
│   │   ├── orderbook_service.py, tradebook_service.py,
│   │   │   positions_service.py, holdings_service.py,
│   │   │   funds_service.py, margin_service.py, history_service.py
│   │   ├── quotes_service.py, depth_service.py, market_data_service.py
│   │   ├── option_chain_service.py, option_symbol_service.py,
│   │   │   option_greeks_service.py, options_multiorder_service.py,
│   │   │   place_options_order_service.py, synthetic_future_service.py
│   │   ├── max_pain_service.py, oi_tracker_service.py,
│   │   │   iv_chart_service.py, iv_smile_service.py,
│   │   │   vol_surface_service.py, straddle_chart_service.py,
│   │   │   gex_service.py
│   │   ├── market_data_cache.py     # Singleton in-process tick cache
│   │   ├── master_contract_status.py
│   │   ├── trading_mode_service.py  # live|sandbox cache + dispatch_by_mode
│   │   ├── sandbox_service.py       # Async wrapper over backend/sandbox/*
│   │   ├── strategy_builder_service.py  # Live snapshot: spot+legs+greeks+totals
│   │   └── strategy_chart_service.py    # Historical combined-premium series
│   ├── sandbox/                     # Simulated trading engine
│   │   ├── _db.py                   # Sync sessionmaker for engine threads
│   │   ├── config.py, defaults.py   # sandbox_config seed + accessors
│   │   ├── order_manager.py         # CRUD on sandbox_orders/trades
│   │   ├── order_validation.py      # Margin / lot / product checks
│   │   ├── execution_engine.py      # Tick subscriber + 5s polling fallback
│   │   ├── fund_manager.py          # Cash, margin block/release
│   │   ├── position_manager.py, holdings_manager.py
│   │   ├── mtm_updater.py           # Periodic MTM recompute
│   │   ├── squareoff.py             # Per-bucket auto squareoff
│   │   ├── t1_settle.py             # CNC -> holdings on T+1
│   │   ├── pnl_snapshot.py          # EOD P&L row
│   │   ├── daily_reset.py, weekly_reset.py
│   │   ├── catch_up.py              # On-restart settlement / squareoff
│   │   ├── scheduler.py             # IST-aware single daemon
│   │   ├── quote_helper.py          # Pulls LTP from MarketDataCache
│   │   └── symbol_info.py
│   ├── broker/                      # Plug-and-play broker plugins
│   │   ├── upstox/{api,mapping,streaming,database}/  + plugin.json
│   │   └── zerodha/{api,mapping,streaming,database}/ + plugin.json
│   ├── websocket_proxy/             # Unified WS proxy server
│   │   ├── server.py                # Client handling, ZMQ listener,
│   │   │                            #   forwards every tick to MarketDataCache
│   │   ├── base_adapter.py          # Abstract broker adapter
│   │   └── auth.py                  # Standalone API-key verification
│   ├── utils/
│   │   ├── plugin_loader.py
│   │   ├── constants.py
│   │   ├── httpx_client.py          # Shared async HTTPX client
│   │   ├── logging.py               # Centralized logging + redaction + DB sink
│   │   ├── request_context.py       # request_id contextvar
│   │   ├── api_log_writer.py        # Async-safe DB writer for api_logs
│   │   ├── redis_client.py          # Shared async Redis client + helpers
│   │   ├── symtoken_cache.py        # Master-contract mirror in Redis
│   │   └── schema_migrations.py     # Idempotent column-add migrations
│   └── test/                        # Pytest suite
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Router + lazy-loaded tool pages
│   │   ├── pages/                   # Dashboard, OrderBook, ..., Logs, Sandbox,
│   │   │   ├── Sandbox.tsx, SandboxMyPnL.tsx
│   │   │   ├── Logs.tsx             # api_logs / error_logs viewer
│   │   │   ├── Search.tsx           # OpenAlgo-style symbol search
│   │   │   ├── WebSocketTest.tsx    # Dev tool for tick stream + cache reads
│   │   │   ├── Tools.tsx            # Tool index
│   │   │   └── tools/               # Plotly-based analytics pages
│   │   │       ├── OptionChain.tsx, OITracker.tsx, MaxPain.tsx
│   │   │       ├── OptionGreeks.tsx, IVSmile.tsx, VolSurface.tsx
│   │   │       ├── StraddleChart.tsx, GEXDashboard.tsx
│   │   │       ├── StrategyBuilder.tsx           # 5-tab multi-leg designer
│   │   │       └── StrategyPortfolio.tsx         # Saved strategies + live P&L
│   │   ├── components/
│   │   │   ├── auth/, layout/, trading/
│   │   │   ├── charts/{Plot,Plot3D}.tsx        # Plotly wrappers
│   │   │   ├── ErrorBoundary.tsx               # Wraps chart panels
│   │   │   ├── strategy-builder/   # PayoffChart, GreeksPanel, PnLTab,
│   │   │   │                       # StrategyChartTab, WhatIfPanel,
│   │   │   │                       # LegRow, LivePriceCell, etc.
│   │   │   ├── strategy-portfolio/ # StrategyCard, CloseStrategyDialog
│   │   │   └── ui/                  # shadcn primitives
│   │   ├── contexts/
│   │   │   ├── AuthContext.tsx, ThemeContext.tsx
│   │   │   └── TradingModeContext.tsx           # Live/sandbox toggle + tint
│   │   ├── hooks/
│   │   │   ├── useMarketData.ts, useOptionChainLive.ts
│   │   │   ├── useChainContext.ts, useStrategySnapshot.ts
│   │   │   └── usePageVisibility.ts
│   │   ├── api/                     # Typed fetch wrappers per feature
│   │   └── lib/
│   │       ├── utils.ts
│   │       ├── black76.ts                       # Pure-TS Black-76 + payoff math
│   │       ├── probabilityOfProfit.ts           # Lognormal POP integrator
│   │       └── strategyTemplates.ts             # 14-template registry
├── collections/                     # Bruno API collection
├── docs/
├── logs/                            # Runtime: openbull.log, openbull-error.log
└── .env                             # Configuration
```

## Design Patterns

### 1. Strategy Pattern (Broker Plugin System)

Every service dynamically loads the correct broker module at runtime:

```python
module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
res, data, order_id = module.place_order_api(order_data, auth_token)
```

Swap `broker_name` between `"upstox"`, `"zerodha"`, `"angel"`, `"dhan"`, or `"fyers"` and the same service hits a different broker API with zero code changes. See [broker-integration.md](./broker-integration.md) for the full broker-plugin spec.

### 2. Abstract Base Class (Streaming Adapters)

`BaseBrokerAdapter` defines the contract:

```python
class BaseBrokerAdapter(ABC):
    def setup_zmq() -> int          # Concrete: ZMQ PUB socket
    def publish(topic, data)         # Concrete: JSON on ZMQ
    def connect()                    # Abstract: broker-specific
    def subscribe(symbols, mode)     # Abstract: broker-specific
    def unsubscribe(symbols, mode)   # Abstract: broker-specific
    def disconnect()                 # Abstract: broker-specific
```

Five concrete adapters ship today: `UpstoxAdapter` (protobuf v3), `ZerodhaAdapter` (KiteTicker binary), `AngelAdapter` (SmartStream binary), `DhanAdapter` (Dhan binary), `FyersAdapter` (HSM binary). All publish the same normalised JSON tick payload to the ZMQ bus regardless of the upstream protocol.

### 3. Pub/Sub (ZeroMQ Market Data Bus)

```
Broker WS Feed -> Adapter (PUB) -> ZMQ -> Server (SUB) -> Client WS
                                            |
                                            +-> MarketDataCache (singleton)
                                                     |
                                                     +-> sandbox engine (CRITICAL)
                                                     +-> RMS hooks (CRITICAL)
                                                     +-> dashboards (LOW)
```

ZeroMQ decouples the blocking broker WebSocket thread from the async proxy server. Topics use the format `{EXCHANGE}_{SYMBOL}_{MODE}`. The proxy now also feeds every tick into the in-process cache so internal consumers do not need to open a WS to read live prices.

### 4. Factory Method (Adapter Creation)

```python
def _create_adapter(broker_name, auth_token, config):
    if broker_name == "upstox":  return UpstoxAdapter(...)
    elif broker_name == "zerodha": return ZerodhaAdapter(...)
```

### 5. Adapter Pattern (Data Transformation)

Each broker's `mapping/` folder translates between OpenBull's standard format and the broker's native format:

- `transform_data.py`: order fields (product CNC->D, pricetype MARKET->MARKET)
- `order_data.py`: response mapping (instrument_token -> symbol)
- `margin_data.py`: margin request/response normalization

### 6. Repository Pattern (Auth Dependencies)

`dependencies.py` encapsulates all DB queries for auth behind clean interfaces:

- `get_api_user(request, db)` -> `(user_id, auth_token, broker_name, config)`
- `get_current_user(request, db)` -> `User`
- `get_broker_context(user, db)` -> `BrokerContext`

All three short-circuit through Redis on a hit, and tag `request.state.user_id` so the API-log middleware knows the request is authenticated.

### 7. Singleton (MarketDataCache)

`backend.services.market_data_cache.MarketDataCache` is a process-wide singleton constructed via the classic `__new__` + lock pattern. It owns:

- a per-symbol dict keyed `EXCHANGE:SYMBOL` with separate ltp / quote / depth slots,
- a priority subscriber registry (`CRITICAL`, `HIGH`, `NORMAL`, `LOW`) dispatched in order on every tick,
- a sanity validator that rejects non-positive LTPs and warns on >20% jumps,
- a background daemon that flips status to `STALE` after 30 s without ticks and trips an RMS gate (`is_trade_management_safe`).

Module-level helpers (`get_ltp`, `get_quote`, `process_market_data`) hide the singleton behind a flat surface that mirrors openalgo's public API.

### 8. Cache-aside (Redis)

Every Redis read in `backend/dependencies.py` and `backend/utils/symtoken_cache.py` follows GET -> miss -> populate-from-DB -> SET-with-TTL. Writes invalidate; nothing relies on Redis maxmemory eviction for correctness.

### 9. Trading-Mode Dispatch

`backend.services.trading_mode_service.dispatch_by_mode(live_fn, sandbox_fn, ...)` reads the cached mode (or sync-loads it from `app_settings`) and routes to the matching coroutine. The order/funds/positions services either call this helper or short-circuit via `get_trading_mode_sync()`. Live mode hits the broker's REST API; sandbox mode lands inside `backend/sandbox/*`.

### 10. Sandbox Simulation Engine

Two fill triggers, one fill function:

1. **Tick-driven** -- `execution_engine` registers a CRITICAL subscriber on `MarketDataCache`. Every tick checks pending orders for that symbol.
2. **Polling fallback** -- a 5 s daemon scans pending orders against the cache snapshot so LIMIT/SL orders progress when the broker feed is quiet.

A separate IST-aware scheduler thread fires:

- per-bucket auto squareoff (NSE/NFO/BSE/BFO 15:15, CDS 17:00, MCX 23:30 by default),
- T+1 CNC->holdings settlement and EOD P&L snapshot at 23:55,
- daily `today_realized_pnl` zeroing at 00:00 and an expired-F&O-position close,
- optional weekly reset of the simulated book.

A startup `catch_up` step replays anything the app missed while it was down.

## Data Flow: PlaceOrder (Live Mode)

```
POST /api/v1/placeorder {apikey, symbol, exchange, action, ...}
  -> api/place_order.py::api_place_order
    -> dependencies.get_api_user
        -> Redis api_key:valid|invalid? -> hit returns user_id
        -> miss: Argon2 verify against api_keys, set TTL 15min/5min
        -> Redis api_ctx:<user_id>? -> hit returns (auth_token, broker, config)
        -> miss: read broker_auth + broker_config, decrypt, set TTL 1h
        -> request.state.user_id set (auth-gated logging will fire)
    -> services/order_service.py::place_order_with_auth
      -> trading_mode_service.get_trading_mode_sync() -> "live"
      -> validate_order_data (constants check)
      -> importlib("backend.broker.upstox.api.order_api")
        -> mapping/order_data.py: symbol -> instrument_token
        -> mapping/transform_data.py: OpenBull -> Upstox payload
        -> httpx POST https://api.upstox.com/v2/order/place
      <- (response, data, order_id)
    <- {"status": "success", "orderid": "..."}
  <- JSONResponse  (ApiLogMiddleware persists row in api_logs with mode="live")
```

## Data Flow: PlaceOrder (Sandbox Mode)

```
POST /api/v1/placeorder {apikey, symbol, exchange, action, ...}
  -> api/place_order.py::api_place_order
    -> dependencies.get_api_user (same path, Redis cache-aside)
    -> services/order_service.py::place_order_with_auth
      -> trading_mode_service.get_trading_mode_sync() -> "sandbox"
      -> services/sandbox_service.place_order(user_id, payload)
        -> backend.sandbox.order_validation: lot size, product, margin
        -> backend.sandbox.fund_manager: block margin from sandbox_funds
        -> backend.sandbox.order_manager.create_order
            (writes sandbox_orders row, status="open" or "trigger_pending")
      <- {"status": "success", "orderid": "<YYMMDD-microsecond>"}
  <- JSONResponse  (api_logs row tagged mode="sandbox")

Later, asynchronously:
  Broker tick on the same symbol arrives via ZMQ
    -> websocket_proxy.server -> MarketDataCache.process_market_data
      -> CRITICAL subscriber: backend.sandbox.execution_engine._on_tick
        -> for each pending order on (exchange, symbol):
          -> _fill_price(order, ltp) -> matched price or None
          -> if matched: order_manager.fill -> sandbox_trades insert,
             position_manager update, fund_manager release/charge,
             status="complete"
```

The broker is never contacted for sandbox orders. The same symbol tick stream powers both live dashboards and the simulated fill loop.

## Data Flow: WebSocket Streaming

```
Client -> ws://localhost:8765
  -> {"action":"authenticate","api_key":"..."}
    -> websocket_proxy/auth.py: verify key -> (user_id, auth_token, broker)
    -> server.py: create UpstoxAdapter, setup ZMQ PUB, connect WS
  <- {"type":"auth","status":"success","broker":"upstox"}

  -> {"action":"subscribe","symbols":[...],"mode":"Quote"}
    -> adapter.subscribe: resolve tokens (symtoken cache) -> send sub to broker WS
    -> broker WS sends protobuf / binary tick
    -> adapter._process_*: decode -> normalize -> ZMQ PUB
    -> server._zmq_listener: ZMQ SUB recv
        -> MarketDataCache.process_market_data(tick)   # cache + RMS fanout
        -> route to subscribed clients
  <- {"type":"market_data","symbol":"NIFTY","exchange":"NSE_INDEX","data":{...}}
```

Every tick is captured by the cache regardless of whether any external WS client is currently subscribed. That means HTTP endpoints like `/api/websocket/market-data/{exchange}/{symbol}` and the sandbox engine see the latest LTP without opening a second feed.

## Data Flow: Analytics Tool (Vol Surface)

```
User navigates to /tools/volsurface  (lazy-loaded chunk)
  -> Frontend resolves expiries via /api/v1/expiry
  -> Frontend POSTs /api/v1/multiquotes with the full strike grid
    -> services/quotes_service: pulls from MarketDataCache where fresh,
       falls back to broker quotes endpoint for cold strikes
  -> Frontend POSTs /api/v1/volsurface {underlying, expiries}
    -> services/vol_surface_service:
        - gather option quotes for the strike x expiry grid
        - convert IST timestamps -> seconds-to-expiry (DST-free, Asia/Kolkata)
        - off-hours fallback: use last close + cached IV when bid/ask collapse
        - call option_greeks_service to back out IV per (K, T) via Newton
        - return JSON {strikes, expiries, iv_grid, surface_meta}
  -> Frontend Plot3D (charts/Plot3D.tsx) renders Plotly mesh3d with manual
     aspect-ratio override so the surface stays visible at any data range
```

Every analytics page follows the same shape: code-split React route -> typed API client (`frontend/src/api/*.ts`) -> service layer that joins broker data + Greeks math -> JSON to a Plotly component. IST timestamp handling lives in the services so the frontend can stay timezone-agnostic.

## Security Architecture

| Layer            | Mechanism                                                                                     |
|------------------|-----------------------------------------------------------------------------------------------|
| Passwords        | Argon2id + per-instance pepper                                                                |
| Broker secrets   | Fernet encryption at rest in `broker_auth` / `broker_config`                                  |
| Web sessions     | JWT in httpOnly cookies, IST-aware expiry, server-side `active_sessions` revocation table     |
| OAuth callback   | Redirect host derived from the request hostname so the access_token cookie survives the bounce (fixed the "first login fails, second succeeds" bug between localhost and 127.0.0.1) |
| Broker resume    | On session resume the broker token is revalidated against the broker; revoked tokens force re-login |
| API auth         | API key Argon2-verified on miss; cached as `api_key:valid:<sha256>` (15min) / `api_key:invalid:<sha256>` (5min) |
| Broker context   | `broker_ctx:<user_id>` and `api_ctx:<user_id>` cached in Redis for 1h, invalidated on logout / key rotation / OAuth callback |
| WS auth          | Per-client API-key verification before any subscribe                                          |
| Transport        | TLS certificate verification on all broker connections                                        |
| Headers          | CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy. nginx and the FastAPI middleware both set them; install scripts dedupe so they only render once. |
| Rate limits      | SlowAPI per-endpoint                                                                          |
| WS limits        | max_connections=10, max_message_size=64KB, max_symbols=1000                                   |
| Logging          | Sensitive-data redaction (api_key, password, access/refresh tokens, Bearer/token schemes) on every log line; 64KB body cap with deep redaction in `api_logs`; binary bodies recorded as size only |

## Configuration

All via `.env` (Pydantic Settings auto-loads):

```
# Core secrets
APP_SECRET_KEY                     # JWT signing
ENCRYPTION_PEPPER                  # Fernet/Argon2 pepper

# Database / Redis
DATABASE_URL                       # asyncpg DSN
REDIS_URL                          # redis://127.0.0.1:6379/0

# Server
BACKEND_HOST / BACKEND_PORT
FRONTEND_URL
CORS_ORIGINS                       # Comma-separated
COOKIE_SECURE                      # True in HTTPS prod, False on local 127.0.0.1
SESSION_EXPIRY_TIME                # IST cutover time, e.g. "03:00"

# WebSocket proxy + ZMQ
WEBSOCKET_HOST / WEBSOCKET_PORT / WEBSOCKET_URL
ZMQ_HOST / ZMQ_PORT
MAX_SYMBOLS_PER_WEBSOCKET
MAX_WEBSOCKET_CONNECTIONS
ENABLE_CONNECTION_POOLING

# Brokers
VALID_BROKERS                      # "upstox,zerodha"

# Logging
LOG_LEVEL / LOG_TO_FILE / LOG_DIR / LOG_COLORS
LOG_FILE_MAX_MB                    # Per-file rotation cap (default 10)
LOG_FILE_BACKUP_COUNT              # Rotated backups kept (default 9)
ERROR_LOG_DB_MAX_ROWS              # error_logs table cap (default 50000)
API_LOG_DB_MAX_ROWS                # api_logs table cap (default 100000)

# Rate limits
LOGIN_RATE_LIMIT_MIN / LOGIN_RATE_LIMIT_HOUR
API_RATE_LIMIT / ORDER_RATE_LIMIT
```

Sandbox defaults (starting capital, leverage, squareoff cut-offs, weekly reset) are stored in the `sandbox_config` table -- seeded at startup, edited from `/sandbox` in the UI.

## Caching Layer (Redis)

All keys are namespaced under the `openbull:` prefix so the instance can be shared with other apps. The cache is strictly cache-aside; writes happen at the service layer and TTLs are set explicitly.

| Key                              | Value                                          | TTL      | Invalidated by                                  |
|----------------------------------|------------------------------------------------|----------|-------------------------------------------------|
| `api_key:valid:<sha256>`         | `user_id`                                      | 15 min   | API key rotation, logout                        |
| `api_key:invalid:<sha256>`       | `1` (negative cache)                           | 5 min    | API key rotation                                |
| `broker_ctx:<user_id>`           | `{broker_name, auth_token, broker_config}`     | 1 h      | Logout, OAuth callback, broker config update    |
| `api_ctx:<user_id>`              | same shape, used by external API path          | 1 h      | Same as `broker_ctx`                            |
| `symtoken:tok2sym` etc. (5 hashes) | Token <-> symbol <-> brsymbol mappings       | none     | Master-contract download (rewrites all 5 keys)  |
| `symtoken:ready`                 | `{count, ts}` sentinel                         | none     | Master-contract download                        |
| `md:<exchange>:<symbol>`         | Latest LTP / quote / depth snapshot            | 60 s     | Overwritten on every tick                       |

`utils/redis_client.py` exposes `cache_get_json`, `cache_set_json`, `cache_delete`, `cache_delete_pattern`, and pipelined `hash_hmset_pipelined`. On Redis outages every helper logs and returns the safe-empty value -- the request still completes against PostgreSQL.

The `symtoken` mirror is what lets the app boot fast: the master-contract table holds ~120k rows, and rebuilding the in-memory lookup dicts from Postgres takes seconds; rehydrating from the Redis hashes takes milliseconds.

## Logging & Audit

`backend.utils.logging.setup_logging()` is the single configuration entry point. It installs:

- **Console handler** -- INFO+ to stdout with optional ANSI colours.
- **Rotating file handlers** -- `logs/openbull.log` (all levels) and `logs/openbull-error.log` (WARNING+), each capped at `LOG_FILE_MAX_MB * (LOG_FILE_BACKUP_COUNT + 1)`.
- **`SensitiveDataFilter`** -- regex redaction of api keys, passwords, access/refresh tokens, secrets, Authorization headers, and `Bearer`/`token` schemes. Runs before any handler emits.
- **`RequestIdFilter`** -- stamps the current `request_id_var` onto every record so logs across modules can be correlated for one request.
- **`DBErrorLogHandler`** -- a queueing handler that drains WARNING+ records on a daemon thread into the `error_logs` table; the worker trims the table to `ERROR_LOG_DB_MAX_ROWS` after each batch.

The `RequestLoggingMiddleware` is the outermost middleware. It accepts an inbound `X-Request-ID` header (capped at 64 chars) or generates one, sets the contextvar, and emits a single access line `METHOD PATH -> STATUS in Xms` after the response is built. The same id is echoed on the response.

`ApiLogMiddleware` runs inside the request-id middleware. It captures the request and response bodies (truncated to 64 KB per side, JSON deep-redacted, binary bodies recorded as size only), and only enqueues a row when `request.state.user_id` is set -- i.e. an auth dependency succeeded. Unauthenticated noise (attacker floods, expired cookies, invalid API keys) never touches the table. Each row carries `request_id`, `auth_method` (`session` or `api_key`), `mode` (`live` or `sandbox`), latency, status, and the redacted bodies. A daemon writer drains the queue and trims the table to `API_LOG_DB_MAX_ROWS`.

The `/logs` page is the auth-gated viewer for both tables. It supports filtering by status, method, mode, and free-text path; full request/response bodies open in a modal. The `/web/logs` API itself is excluded from `ApiLogMiddleware`'s capture list so the viewer cannot flood its own table.

## Trading Modes (Live / Sandbox)

A single row in `app_settings` (`key='trading_mode'`) toggles the entire app between live and sandbox. The flag is cached in-process for 10 s; mode-change writes invalidate the cache so the next dispatch sees the new value.

`backend.services.trading_mode_service` exposes:

- `get_trading_mode()` -- async, reads cache then `app_settings`.
- `get_trading_mode_sync()` -- sync counterpart for service code that runs outside an event loop (e.g. the broker order path); falls back to a sync DB read via the sandbox sessionmaker.
- `set_trading_mode(db, mode)` -- write-through; invalidates the cache.
- `dispatch_by_mode(live_fn, sandbox_fn, *args)` -- helper used by every order/funds/positions service.

In sandbox mode all order, position, holdings, and funds reads/writes flow through `backend.services.sandbox_service`, which is a thin async facade around `backend/sandbox/*`:

- **Order lifecycle** -- `order_validation` (lot, product, margin) -> `fund_manager.block` -> `order_manager.create_order`. Pending orders live in `sandbox_orders`; fills append to `sandbox_trades` and update `sandbox_positions`.
- **Margin lifecycle** -- intraday MIS uses leveraged margin; CNC uses cash; `fund_manager` blocks on order creation, releases on cancel, charges on fill, and credits realised P&L on close. Mirrors openalgo's phase-2c contract for parity with their reference suite.
- **Scheduled squareoff** -- the IST scheduler fires per-bucket squareoffs (NSE/NFO/BSE/BFO, CDS, MCX) at the configured cut-off, force-closing any remaining MIS positions at the cached LTP.
- **T+1 settlement** -- at 23:55 IST, CNC buys settle into `sandbox_holdings` and the day's P&L is rolled into a `pnl_snapshot` row.
- **Daily P&L** -- `today_realized_pnl` on positions and funds zeroes at 00:00 IST while accumulated `realized_pnl` is preserved.
- **Catch-up on startup** -- `run_catch_up_tasks()` runs before the engine and scheduler start, replaying squareoffs, T+1 settlement, today-P&L reset, and expired-F&O closures missed while the app was down.
- **MTM updater** -- a separate daemon recomputes mark-to-market on open positions every few seconds using cached LTPs.

The frontend reflects mode through `TradingModeContext`: a header switch flips the flag, the layout tints when sandbox is active, and the dedicated `/sandbox` config page edits sandbox-specific settings. `SandboxMyPnL` surfaces the daily / cumulative P&L snapshot.

## Analytics & Charting Tools

The `frontend/src/pages/tools/` directory hosts eight Plotly-backed analytics pages. Each page is registered in `App.tsx` as a `React.lazy` import so the ~600 KB Plotly bundle is fetched only when the user navigates to a chart. A shared `Suspense` fallback covers the chunk load.

| Page              | Backend service                  | API route                          |
|-------------------|----------------------------------|------------------------------------|
| Option Chain      | `option_chain_service`           | `/api/v1/optionchain` (+ live WS)  |
| OI Tracker        | `oi_tracker_service`             | `/api/v1/oitracker`                |
| Max Pain          | `max_pain_service`               | `/api/v1/maxpain`                  |
| Option Greeks     | `option_greeks_service`          | `/api/v1/optiongreeks`             |
| IV Smile          | `iv_smile_service`               | `/api/v1/ivsmile`                  |
| Vol Surface       | `vol_surface_service`            | `/api/v1/volsurface`               |
| Straddle Chart    | `straddle_chart_service`         | `/api/v1/straddle`                 |
| GEX Dashboard     | `gex_service`                    | `/api/v1/gex`                      |
| Strategy Builder  | `strategy_builder_service` + `strategy_chart_service` | `/web/strategybuilder/{snapshot,chart}` (session) |
| Strategy Portfolio| (CRUD via `routers/strategies`)  | `/web/strategies/*` (session)      |

All analytics services share these conventions:

- **IST timestamp handling** -- every time-based axis is computed in `Asia/Kolkata`. DST-free fixed offset, no pytz.
- **Off-hours fallback** -- when the bid/ask collapses (weekends, off-market), services fall back to the last close + a smoothed IV so the chart still renders rather than emitting `NaN`.
- **Tolerant payload parsing** -- quote consumers do `r.get("data", r)` so flat multi-quote shapes still work alongside the nested `{data: ...}` form.
- **Live tick reuse** -- where possible, services pull LTPs from `MarketDataCache` instead of re-hitting the broker; cold strikes fall back to a multi-quotes call.

Order placement is integrated directly: the Option Chain page can open a one-click `PlaceOrderDialog` which routes through the same `/api/v1/placeorder` path -- so it respects the live/sandbox toggle automatically.

The Option Chain page also opens its own WS subscription to the proxy (`useOptionChainLive`) for live LTP / bid / ask on every visible strike. MCX support resolves the near-month FUT contract automatically as the underlying for the chain.

### Strategy Builder + Portfolio

Two paired pages implementing the multi-leg-strategy lifecycle: design → save → live monitor → close. The UI was iteratively brought to parity with openalgo's reference design and now exceeds it on several axes (T+0 sim correctness, modal-driven leg edits, lightweight-charts adoption).

**Strategy Builder (`/tools/strategybuilder`)** is a six-tab page that owns the entire builder state in one component. Children are presentation-only with `value + onChange` props so the URL `?load=<id>` round-trip stays trivial.

The page header carries:

- `UnderlyingPicker` + `ExpiryPicker` — exchange / underlying / expiry selection
- `LoadStrategyPicker` — in-page dropdown listing the user's active saved strategies for the current trading mode; the cache invalidates on save so a fresh save lands one click away
- `Save` / `Execute Basket` / `Clear` buttons (also mirrored in `PositionsPanel` footer for the Payoff tab)

Below the header sits `SymbolHeader` — a horizontal chip strip with `Spot`, `Futures` (synthetic via put-call parity at ATM, no extra broker call), `ATM IV` (averaged from the snapshot's ATM-strike legs), `DTE` (parsed off the picked expiry with 15:30-IST roll), `Lot Size`. A live-status pill on the right pulses green when chain + snapshot are both fresh.

Then a card hosting the **30-template Strategy Library** — `TemplateGrid` with mini SVG payoff icons, direction tabs (Bullish / Bearish / Neutral) with live counts, and a search box. Clicking a card opens **`TemplateConfigDialog`**: a preview modal that shows resolved legs (chain-sourced strike dropdowns, editable lots / entry, net debit/credit summary). The user tweaks and confirms → legs land in the builder. Calendar / diagonal templates correctly resolve `expiryOffset` against the broker's expiry list (a long-standing bug fixed in this iteration).

The six tabs:

- **Legs** — `AddPositionCard` (manual leg builder with explicit Segment / Expiry / Strike / Type / Side / Lot Qty + dedicated ADD BUY / ADD SELL buttons) on top, then a list of `LegRow`s. Each leg has a chain-sourced strike dropdown with a moneyness chip (ATM / ITMn / OTMn) and an entry-price field that auto-fills from chain LTP on strike or option-type change. Pencil icon opens **`EditLegDialog`** — full per-leg modal with Action pills, Type pills, Expiry select, Strike dropdown with moneyness, Lot Qty stepper, Entry Price (auto-LTP), resolved-symbol preview, and Save / Delete actions.
- **Greeks** — per-leg row plus a sticky aggregate row, fed by the snapshot endpoint. **Plain-English labels** (Delta / Gamma / Theta / Vega — never the Greek glyphs; openbull is a retail product).
- **Payoff** — two-pane layout. Left: `PositionsPanel` with per-leg checkbox toggle (drop a leg from the curve without deleting it), 2-column stats grid (Max Profit / Max Loss / POP / RR Ratio / Total P&L / Net Credit / Est. Premium / **Margin Req.** sourced live from `/api/v1/margin`), breakevens row with chip badges, and Save + Execute Basket buttons in the footer. Right: `PayoffChart` with At-Expiry + T+0 curves, ±1σ/±2σ bands sized off the shortest-DTE leg, breakeven verticals, "Unlimited" annotations driven by `asymptoticSlopes()`, and a "POP N.N%" corner badge. The **`WhatIfPanel`** (Spot ±10% / IV ±10pp / Days forward) sits below — its `ivShiftPct` and `daysForward` flow into the marker dot AND the dashed T+0 curve, so dragging the IV slider re-prices every leg in real time, not just the marker.
- **Strategy Chart** — historical combined-premium time series on `lightweight-charts` (migrated from Plotly for the perf win on dense intraday series). P&L vs Premium toggle, per-leg dotted lines, optional underlying overlay on the right axis, IST tick formatting via `+05:30` unix shift, openalgo-parity columns surfaced as badges (`Net credit · ₹X/sh`).
- **Multi-Strike OI** — per-leg historical Open Interest series on `lightweight-charts`, underlying close on the second axis. New endpoint: `POST /web/strategybuilder/multi-strike-oi`. `has_oi=false` legs (broker doesn't ship historical OI) are badged and skipped.
- **P&L** — tab-scoped `useMarketData("Quote")` subscription. Per-leg row with flash-on-tick `LivePriceCell` for live LTP and signed P&L. Stale-tick warning when no symbol has updated in 30s+.

Saved strategies hit `POST /web/strategies`; basket execute fires `POST /api/v1/basketorder` with absolute symbols (NOT `optionsmultiorder`, which would re-resolve from offset+ATM at execute time and risk a different strike). The `BasketOrderDialog` carries per-leg controls — Use checkbox, editable Lots, editable tick-snapped Price (disabled for MARKET / SL-M), inline per-row result indicators (✓ orderid on success, × message on error).

**Strategy Portfolio (`/tools/strategyportfolio`)** lists every saved strategy with live aggregate P&L per card. 2-column grid on lg+ viewports, segmented pill filter for status, polished empty state.

- **One shared WebSocket** across the page — collects unique `(symbol, exchange)` tuples from every visible *active* strategy's open legs and passes them to a single `useMarketData` call. A symbol used in three strategies streams once, not three times.
- **Filters** by mode (live/sandbox/all), status (active/closed/expired/all), and underlying-substring; defaults follow the global trading-mode toggle.
- **Active strategies** show unrealized P&L (LTP − entry); **closed/expired** show realized P&L (exit_price − entry, frozen).
- **Optimistic UI** on close + delete via `queryClient.setQueriesData` so rows re-render instantly while a background invalidate reconciles.
- **Close dialog** reuses the shared `liveLtpMap` for exit-price defaults.

**Persistence model** lives in `backend/models/strategies.py` — one `strategies` table with `user_id` FK, JSONB `legs` column, composite `(user_id, mode, status)` index. JSON keeps the schema loose so future leg metadata can land without per-feature migrations.

**Math separation** — Black-76 is implemented twice and stays in lockstep:
- `backend/services/option_greeks_service.py` (pure-Python `math.erf`) — source of truth for snapshot pricing.
- `frontend/src/lib/black76.ts` — frontend mirror used by the What-if sliders for instant feedback. Same model, same units (theta/day, vega/1%, rho/1%) so simulator and snapshot Greeks agree at zero-shift.

`frontend/src/lib/probabilityOfProfit.ts` runs lognormal CDF integration over profit regions identified on the at-expiry curve.

**Strategy Chart formula parity** — `backend/services/strategy_chart_service.py` emits both openbull's signed-rupee `value` (P&L scale, BUY=+1) and openalgo's per-share `net_premium` / `combined_premium` (SELL=+1, with absolute-value alongside). Conventions are equivalent given the sign-flip; the docstring carries a worked example. The Strategy Chart UI uses the openbull column for P&L view and the openalgo `tag` (`credit`/`debit`/`flat`) for the badge.

**Multi-Strike OI service** — `backend/services/multi_strike_oi_service.py` mirrors the `strategy_chart_service` pattern but extracts the broker-reported `oi` field instead of `close`. Per-leg history is fetched in parallel (deduped by symbol+exchange), trading window cap matches the chart endpoint. `has_oi` flag set when any candle has nonzero OI.

`ErrorBoundary` wraps each chart panel so a render error doesn't unmount the whole page — local Retry button instead.
