# OpenBull (Option Trading Platform)

Options trading platform for Indian brokers. Single-user application with plug-and-play broker integration, supporting Upstox and Zerodha. OpenAlgo-compatible API format for cross-compatibility with existing trading tools.

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy (async), PostgreSQL
- **Frontend:** React 19, Vite, TypeScript, ShadcnUI, Tailwind CSS, TanStack Query
- **Streaming:** ZeroMQ PUB/SUB, websocket-client, protobuf (Upstox v3)
- **Security:** Argon2 password hashing, Fernet encryption, JWT (httpOnly cookies), CSP, CORS, rate limiting
- **Package Manager:** uv (Python), npm (Node)

## Prerequisites

- Python 3.12+
- Node.js 20+
- PostgreSQL 15+
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

### 1. Create the database

```bash
psql -U postgres -c "CREATE DATABASE openbull"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and generate unique secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Set the output as `APP_SECRET_KEY` and generate another for `ENCRYPTION_PEPPER`.

### 3. Install and run the backend

```bash
uv sync
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

The database tables are created automatically on first startup. The WebSocket proxy starts on port 8765.

### 4. Install and run the frontend

```bash
cd frontend
npm install
npm run dev
```

### 5. Open the app

Visit **http://localhost:5173**

- First visit: create admin account at `/setup`
- Login, then configure broker credentials at `/broker/config`
- Select broker and complete OAuth at `/broker/select`
- Dashboard shows live funds after successful broker login

## API Endpoints (31 endpoints)

Full documentation: [docs/api/README.md](docs/api/README.md)

### Order Management
| POST | Path | Description |
|------|------|-------------|
| | `/api/v1/placeorder` | Place order |
| | `/api/v1/placesmartorder` | Position-aware smart order |
| | `/api/v1/basketorder` | Multiple orders concurrently |
| | `/api/v1/splitorder` | Split large order into chunks |
| | `/api/v1/optionsorder` | Options order via offset (ATM/ITM/OTM) |
| | `/api/v1/optionsmultiorder` | Multi-leg options strategy |
| | `/api/v1/modifyorder` | Modify existing order |
| | `/api/v1/cancelorder` | Cancel specific order |
| | `/api/v1/cancelallorder` | Cancel all open orders |
| | `/api/v1/closeposition` | Close all positions |

### Account & Order Info
| POST | Path | Description |
|------|------|-------------|
| | `/api/v1/funds` | Account funds/margin |
| | `/api/v1/margin` | Pre-trade margin calculator |
| | `/api/v1/orderbook` | All orders for the day |
| | `/api/v1/tradebook` | All trades for the day |
| | `/api/v1/positions` | Current positions |
| | `/api/v1/positionbook` | Positions (OpenAlgo alias) |
| | `/api/v1/holdings` | Portfolio holdings |
| | `/api/v1/orderstatus` | Status of specific order |
| | `/api/v1/openposition` | Net qty for symbol |

### Market Data
| POST | Path | Description |
|------|------|-------------|
| | `/api/v1/quotes` | LTP/OHLC quotes |
| | `/api/v1/multiquotes` | Multi-symbol quotes |
| | `/api/v1/depth` | 5-level market depth |
| | `/api/v1/history` | Historical OHLCV candles |
| | `/api/v1/intervals` | Supported intervals |

### Symbol & Options
| POST | Path | Description |
|------|------|-------------|
| | `/api/v1/symbol` | Full symbol info |
| | `/api/v1/search` | Symbol search |
| | `/api/v1/expiry` | Expiry dates (options/futures) |
| | `/api/v1/optionsymbol` | Resolve option symbol from offset |
| | `/api/v1/optionchain` | Option chain with live CE+PE quotes |
| | `/api/v1/optiongreeks` | Black-76 Greeks and IV |
| | `/api/v1/syntheticfuture` | Synthetic future price + basis |

### WebSocket Streaming (port 8765)
| Mode | Data |
|------|------|
| LTP | Last traded price, 50ms throttle |
| Quote | OHLCV + OI + volume |
| Depth | 5-level bid/ask market depth |

## Project Structure

```
openbull/
├── backend/
│   ├── main.py                # FastAPI app, middleware, lifespan
│   ├── config.py              # Pydantic Settings (.env)
│   ├── database.py            # SQLAlchemy async engine
│   ├── security.py            # Argon2, Fernet, JWT
│   ├── dependencies.py        # FastAPI DI (auth, DB sessions)
│   ├── models/                # SQLAlchemy ORM models
│   ├── schemas/               # Pydantic request/response models
│   ├── routers/               # Web routes (JWT cookie auth)
│   ├── api/                   # External API (/api/v1, 31 endpoints)
│   ├── services/              # Business logic layer (23 services)
│   ├── broker/                # Plug-and-play broker plugins
│   │   ├── upstox/            # REST + protobuf streaming
│   │   └── zerodha/           # REST + KiteTicker binary streaming
│   ├── websocket_proxy/       # Unified WS proxy (ZeroMQ architecture)
│   └── utils/                 # Helpers, constants, plugin loader
├── frontend/                  # React SPA
├── collections/               # Bruno API collection (30 requests)
├── docs/                      # Documentation
│   ├── api/                   # 33 API endpoint docs
│   ├── design/                # Architecture, services docs
│   └── PRODUCT.md             # Product overview
├── alembic/                   # Database migrations
└── .env.example               # Environment template
```

## Broker Plugin System

Each broker lives in `backend/broker/{name}/` with:

```
broker/{name}/
├── plugin.json                # Metadata, supported exchanges
├── api/
│   ├── auth_api.py            # OAuth token exchange
│   ├── order_api.py           # Place/modify/cancel orders
│   ├── funds.py               # Account funds/margin
│   ├── data.py                # Quotes, depth, history
│   └── margin_api.py          # Margin calculator
├── mapping/
│   ├── transform_data.py      # OpenBull <-> broker format
│   ├── order_data.py          # Order/position data mapping
│   └── margin_data.py         # Margin request/response mapping
├── streaming/
│   └── {broker}_adapter.py    # WebSocket streaming adapter
└── database/
    └── master_contract_db.py  # Symbol download and normalization
```

To add a new broker: create the directory, implement the modules, add a `plugin.json`, and list it in `VALID_BROKERS` in `.env`.

## WebSocket Architecture

```
Client WS (port 8765) <-> WS Proxy Server (asyncio)
    <-> ZeroMQ SUB <-> PUB <-> Broker Adapter (thread)
        <-> Upstox protobuf / Zerodha binary WS
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `APP_SECRET_KEY` | JWT signing key |
| `ENCRYPTION_PEPPER` | Fernet/Argon2 pepper |
| `DATABASE_URL` | PostgreSQL async connection string |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) |
| `VALID_BROKERS` | Enabled brokers (comma-separated) |
| `SESSION_EXPIRY_TIME` | Daily session expiry in IST (default: `03:00`) |
| `WEBSOCKET_HOST` | WS proxy bind host (default: `127.0.0.1`) |
| `WEBSOCKET_PORT` | WS proxy port (default: `8765`) |
| `WEBSOCKET_URL` | External WS URL (default: `ws://127.0.0.1:8765`) |
| `ZMQ_HOST` / `ZMQ_PORT` | ZeroMQ bus (default: `127.0.0.1:5555`) |
| `MAX_SYMBOLS_PER_WEBSOCKET` | Symbols per WS connection (default: `1000`) |

See `.env.example` for all options.

## Bruno API Collection

Import in Bruno: `collections/openbull/` — 30 pre-built requests with tested payloads covering all endpoints.

## Production Build

```bash
cd frontend && npm run build && cd ..
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves the built frontend from `frontend/dist/` automatically. WebSocket proxy starts alongside on port 8765.

## Documentation

- [API Reference](docs/api/README.md) — 33 endpoint docs with tested request/response samples
- [Architecture](docs/design/ARCHITECTURE.md) — System design, data flows, patterns
- [Services](docs/design/SERVICES.md) — Business logic layer documentation
- [Product Overview](docs/PRODUCT.md) — Capabilities and feature summary

## License

AGPL-3.0
