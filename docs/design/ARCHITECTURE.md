# OpenBull Architecture

## System Overview

```
                          React Frontend (port 5173)
                                  |
                          FastAPI Backend (port 8000)
                         /        |         \
                   Web Routes  /api/v1   WebSocket Proxy (port 8765)
                   (JWT auth)  (API key)  (API key auth)
                        |         |              |
                   PostgreSQL  Broker Plugin   ZeroMQ PUB/SUB
                                System              |
                                  |           Broker Adapter
                              importlib        (background thread)
                                  |              |
                         broker/{name}/api    broker/{name}/streaming
                                  |              |
                        Upstox REST API    Upstox/Zerodha WS Feed
                        Zerodha Kite API
```

## Directory Structure

```
openbull/
├── backend/
│   ├── main.py                    # FastAPI app, middleware, lifespan
│   ├── config.py                  # Pydantic Settings (.env)
│   ├── database.py                # SQLAlchemy async engine
│   ├── security.py                # Argon2, Fernet, JWT
│   ├── dependencies.py            # FastAPI DI (auth, DB sessions)
│   ├── models/                    # SQLAlchemy ORM models
│   ├── schemas/                   # Pydantic request/response models
│   ├── routers/                   # Web routes (JWT cookie auth)
│   ├── api/                       # External API (/api/v1, apikey auth)
│   ├── services/                  # Business logic layer
│   ├── broker/                    # Plug-and-play broker plugins
│   │   ├── upstox/
│   │   │   ├── api/               # REST: auth, orders, funds, data, margin
│   │   │   ├── mapping/           # Transform: OpenBull <-> Upstox
│   │   │   ├── streaming/         # WebSocket: protobuf adapter
│   │   │   └── database/          # Master contract download
│   │   └── zerodha/               # Same structure
│   ├── websocket_proxy/           # Unified WS proxy server
│   │   ├── server.py              # Client handling, ZMQ listener
│   │   ├── base_adapter.py        # Abstract broker adapter base
│   │   └── auth.py                # Standalone API key verification
│   └── utils/                     # Helpers, constants, plugin loader
├── frontend/                      # React SPA
├── collections/                   # Bruno API collection
├── docs/                          # Documentation
├── alembic/                       # Database migrations
└── .env                           # Configuration
```

## Design Patterns

### 1. Strategy Pattern (Broker Plugin System)

Every service dynamically loads the correct broker module at runtime:

```python
module = importlib.import_module(f"backend.broker.{broker_name}.api.order_api")
res, data, order_id = module.place_order_api(order_data, auth_token)
```

Swap `broker_name` from `"upstox"` to `"zerodha"` and the same service hits a different broker API with zero code changes.

### 2. Abstract Base Class (Streaming Adapters)

`BaseBrokerAdapter` defines the contract:

```python
class BaseBrokerAdapter(ABC):
    def setup_zmq() -> int        # Concrete: ZMQ PUB socket
    def publish(topic, data)       # Concrete: JSON on ZMQ
    def connect()                  # Abstract: broker-specific
    def subscribe(symbols, mode)   # Abstract: broker-specific
    def unsubscribe(symbols, mode) # Abstract: broker-specific
    def disconnect()               # Abstract: broker-specific
```

`UpstoxAdapter` decodes protobuf. `ZerodhaAdapter` parses binary structs. Both publish the same normalized JSON.

### 3. Pub/Sub (ZeroMQ Market Data Bus)

```
Broker WS Feed → Adapter (PUB) → ZMQ → Server (SUB) → Client WS
```

ZeroMQ decouples the blocking broker WebSocket thread from the async proxy server. Topics use the format `{EXCHANGE}_{SYMBOL}_{MODE}`.

### 4. Factory Method (Adapter Creation)

```python
def _create_adapter(broker_name, auth_token, config):
    if broker_name == "upstox": return UpstoxAdapter(...)
    elif broker_name == "zerodha": return ZerodhaAdapter(...)
```

### 5. Adapter Pattern (Data Transformation)

Each broker's `mapping/` folder translates between OpenBull's standard format and the broker's native format:
- `transform_data.py`: order fields (product CNC→D, pricetype MARKET→MARKET)
- `order_data.py`: response mapping (instrument_token → symbol)
- `margin_data.py`: margin request/response normalization

### 6. Repository Pattern (Auth Dependencies)

`dependencies.py` encapsulates all DB queries for auth behind clean interfaces:
- `get_api_user(request, db)` → `(user_id, auth_token, broker_name, config)`
- `get_current_user(request, db)` → `User`
- `get_broker_context(user, db)` → `BrokerContext`

## Data Flow: PlaceOrder

```
POST /api/v1/placeorder {apikey, symbol, exchange, action, ...}
  → api/place_order.py::api_place_order
    → dependencies.get_api_user → verify API key → resolve broker auth
    → services/order_service.py::place_order
      → validate_order_data (constants check)
      → place_order_with_auth
        → importlib("backend.broker.upstox.api.order_api")
          → order_api.place_order_api
            → mapping/order_data.py: symbol → instrument_token
            → mapping/transform_data.py: OpenBull → Upstox payload
            → httpx POST https://api.upstox.com/v2/order/place
          ← (response, data, order_id)
      ← {"status": "success", "orderid": "..."}
  ← JSONResponse
```

## Data Flow: WebSocket Streaming

```
Client → ws://localhost:8765
  → {"action": "authenticate", "api_key": "..."}
    → websocket_proxy/auth.py: verify key → (user_id, auth_token, broker)
    → server.py: create UpstoxAdapter, setup ZMQ PUB, connect WS
  ← {"type": "auth", "status": "success", "broker": "upstox"}

  → {"action": "subscribe", "symbols": [...], "mode": "Quote"}
    → adapter.subscribe: resolve tokens → send sub to Upstox WS
    → Upstox WS sends protobuf tick
    → adapter._process_protobuf: decode → normalize → ZMQ PUB
    → server._zmq_listener: ZMQ SUB recv → route to subscribed clients
  ← {"type": "market_data", "symbol": "NIFTY", "exchange": "NSE_INDEX", "data": {...}}
```

## Security Architecture

| Layer | Mechanism |
|-------|-----------|
| Passwords | Argon2id + pepper |
| Broker secrets | Fernet encryption at rest |
| Web sessions | JWT in httpOnly cookies, IST-aware expiry |
| API auth | API key verified via Argon2 + TTL cache (15 min) |
| WS auth | Per-client API key verification before subscribe |
| Transport | TLS certificate verification on all broker connections |
| Headers | CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff |
| Rate limits | SlowAPI per-endpoint limiting |
| WS limits | max_connections=10, max_message_size=64KB, max_symbols=1000 |

## Configuration

All via `.env` (Pydantic Settings auto-loads):

```
APP_SECRET_KEY          # JWT signing
ENCRYPTION_PEPPER       # Fernet/Argon2 pepper
DATABASE_URL            # PostgreSQL async
BACKEND_HOST/PORT       # FastAPI server
WEBSOCKET_HOST/PORT/URL # WS proxy
ZMQ_HOST/PORT           # ZeroMQ bus
VALID_BROKERS           # Enabled brokers
MAX_SYMBOLS_PER_WEBSOCKET
MAX_WEBSOCKET_CONNECTIONS
```
