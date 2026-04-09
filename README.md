# OpenBull

Options trading platform for Indian brokers. Single-user application with plug-and-play broker integration, starting with Upstox and Zerodha.

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy (async), PostgreSQL
- **Frontend:** React, Vite, TypeScript, ShadcnUI, Tailwind CSS, TanStack Query
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
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The database tables are created automatically on first startup.

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

## Project Structure

```
openbull/
├── backend/
│   ├── main.py              # FastAPI app, middleware, lifespan
│   ├── config.py             # Pydantic Settings (.env)
│   ├── database.py           # SQLAlchemy async engine
│   ├── security.py           # Argon2, Fernet, JWT
│   ├── dependencies.py       # FastAPI DI (auth, DB sessions)
│   ├── models/               # SQLAlchemy ORM models
│   ├── schemas/              # Pydantic request/response models
│   ├── routers/              # Web routes (JWT auth)
│   ├── api/                  # External API (/api/v1, apikey auth)
│   ├── services/             # Business logic layer
│   ├── broker/               # Plug-and-play broker plugins
│   │   ├── upstox/           # Upstox integration
│   │   └── zerodha/          # Zerodha integration
│   └── utils/                # Helpers, constants, plugin loader
├── frontend/
│   └── src/
│       ├── pages/            # Route pages (Dashboard, Orders, etc.)
│       ├── components/       # Layout, UI, auth guards
│       ├── api/              # Axios API service functions
│       ├── contexts/         # Auth, Theme providers
│       └── types/            # TypeScript type definitions
├── alembic/                  # Database migrations
├── .env.example              # Environment template
└── pyproject.toml            # Python dependencies
```

## Broker Plugin System

Each broker lives in `backend/broker/{name}/` with:

```
broker/{name}/
├── plugin.json               # Metadata, supported exchanges
├── api/
│   ├── auth_api.py           # OAuth token exchange
│   ├── order_api.py          # Place/modify/cancel orders
│   └── funds.py              # Account funds/margin
├── mapping/
│   ├── transform_data.py     # OpenBull <-> broker format
│   └── order_data.py         # Order/position data mapping
└── database/
    └── master_contract_db.py # Symbol download and normalization
```

To add a new broker: create the directory, implement the modules, add a `plugin.json`, and list it in `VALID_BROKERS` in `.env`.

## API Endpoints

### Web Routes (JWT cookie auth, consumed by frontend)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/setup` | First-time admin creation |
| POST | `/auth/login` | Login (returns JWT cookie) |
| POST | `/auth/logout` | Logout |
| GET | `/auth/me` | Current user info |
| GET | `/web/broker/list` | Available brokers |
| PUT | `/web/broker/credentials` | Save broker API credentials |
| GET | `/auth/broker-redirect?broker=` | Get OAuth URL |
| GET | `/web/dashboard` | Funds data |
| GET | `/web/orderbook` | Order book |
| GET | `/web/tradebook` | Trade book |
| GET | `/web/positions` | Positions |
| GET | `/web/holdings` | Holdings |
| GET/POST | `/web/apikey` | Get/generate API key |

### External API (apikey in request body or X-API-KEY header)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/placeorder` | Place order |
| POST | `/api/v1/funds` | Account funds |
| POST | `/api/v1/orderbook` | Order book |
| POST | `/api/v1/tradebook` | Trade book |
| POST | `/api/v1/positions` | Positions |
| POST | `/api/v1/holdings` | Holdings |
| POST | `/api/v1/ping` | Health check |

## Supported Exchanges

NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCDEX, NSE_INDEX, BSE_INDEX, MCX_INDEX

## Order Constants

- **Product Types:** CNC (Cash & Carry), NRML (Normal), MIS (Intraday)
- **Price Types:** MARKET, LIMIT, SL, SL-M
- **Actions:** BUY, SELL

## Environment Variables

| Variable | Description |
|----------|-------------|
| `APP_SECRET_KEY` | JWT signing key (generate with `secrets.token_hex(32)`) |
| `ENCRYPTION_PEPPER` | Fernet/Argon2 pepper (generate with `secrets.token_hex(32)`) |
| `DATABASE_URL` | PostgreSQL connection string |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated) |
| `VALID_BROKERS` | Enabled brokers (comma-separated) |
| `SESSION_EXPIRY_TIME` | Daily session expiry in IST (default: `03:00`) |

See `.env.example` for all options.

## Production Build

```bash
cd frontend && npm run build && cd ..
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves the built frontend from `frontend/dist/` automatically.

## License

AGPL-3.0
