# OpenBull (Options Trading Platform)

OpenBull is a self-hosted options trading platform for Indian markets. Multi-user, multi-broker, with a typed external API that mirrors OpenAlgo so existing trading tools work unchanged. Ships with a full options-analytics suite (option chain, IV smile, vol surface, GEX, OI tracker, …), a multi-leg **Strategy Builder + Portfolio** with live Greeks and historical charting, a tick-driven **Sandbox** simulated trading engine, live WebSocket market data, and a Redis-backed cache layer.

## Highlights

- **Five broker plugins** fully wired end-to-end — Upstox, Zerodha, Angel One (SmartAPI), Dhan, Fyers. Each carries auth, orders, funds, history, depth, margin, and a streaming adapter feeding the unified WS proxy. Plug-and-play architecture: a new broker = one folder + a `plugin.json`.
- **Multi-user**, per-user broker credentials, JWT cookie auth, broker-token revalidation on session resume. OAuth callback redirects use the request hostname so cookies survive the round-trip even when the user mixes `127.0.0.1` and `localhost`.
- **Live / Sandbox trading-mode toggle** — global setting; every order/info path dispatches through `dispatch_by_mode` so the same UI drives real or simulated orders. Sandbox engine simulates fills via live ticks (or a 5s polling fallback), EOD rollover, T+1 settlement, scheduled squareoff, daily P&L snapshots.
- **Eight Plotly-backed analytics tools** + the **Strategy Builder + Portfolio** pair (multi-leg designer with **30 strategy templates** rendered as mini SVG payoff icons, live Greeks, At-Expiry / T+0 payoff curves with sigma bands and breakeven markers, Probability of Profit, real-time what-if sliders that drive both the marker dot and the T+0 curve, **Multi-Strike OI tab**, historical combined-premium chart on `lightweight-charts`, WS-streamed live P&L tab, **per-leg basket execute** with tick-snap pricing, save/reload, close-at-exit).
- **Unified WebSocket proxy** on port 8765 — single ZeroMQ pub/sub bus fanning broker ticks (Upstox protobuf, Zerodha binary, **Fyers HSM binary**, Dhan, Angel) out to authenticated clients with mode hierarchy (DEPTH includes QUOTE includes LTP) and per-symbol throttling.
- **Process-wide MarketDataCache** singleton — every tick hits one in-process map; sandbox MTM updater, RMS, and analytics tools read from it instead of re-hitting brokers.
- **Redis cache layer** — API key, broker context, and master-contract symtoken cached with TTLs and invalidation on logout / OAuth.
- **Centralized logging** — request-id stamping, sensitive-data redaction, bounded rotating files, DB-backed `error_logs` and `api_logs` tables with worker-trimmed row caps. `/logs` viewer in-app.
- **Light / Dark / Sandbox theming** — full light & dark modes (preference persists in `localStorage`), plus a fixed slate-indigo palette that activates whenever the global trading mode is sandbox so "not live" reads instantly.
- **Production install scripts** — `install/install.sh`, `install/update.sh`, `install/perftuning.sh` — Cloudflare-aware, A-grade nginx security headers, certbot-friendly.

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy 2.x async, PostgreSQL, asyncpg, Alembic
- **Cache:** Redis (async, with in-process fallback)
- **Streaming:** ZeroMQ PUB/SUB, websocket-client, protobuf (Upstox v3), Zerodha KiteTicker binary
- **Frontend:** React 19, Vite, TypeScript (strict), TanStack Query, Tailwind CSS, shadcn/ui, Base UI primitives, Plotly (cartesian + 3D), `lightweight-charts`, Sonner toasts
- **Security:** Argon2id password hashing + pepper, Fernet encryption at rest for broker secrets, JWT in httpOnly cookies, server-side session revocation via JTI, CSP / X-Frame-Options / nosniff headers, SlowAPI rate limiting
- **Package managers:** [uv](https://docs.astral.sh/uv/) (Python), npm (Node)

## Prerequisites

- Python 3.12+
- Node.js 20+
- PostgreSQL 15+
- Redis 7+ (any reachable instance — local, WSL, Docker, managed; configured via `REDIS_URL`)

## Quick Start

### 1. Create the database

```bash
psql -U postgres -c "CREATE DATABASE openbull"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Generate two unique secrets and paste them into `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Set the output as `APP_SECRET_KEY`; generate another for `ENCRYPTION_PEPPER`. Set `REDIS_URL` to your Redis instance (default `redis://127.0.0.1:6379/0`).

### 3. Install and run the backend

```bash
uv sync
uv run migrate_all.py            # create tables, run schema-migrations, alembic upgrade head
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

`migrate_all.py` is idempotent — safe to re-run on every deploy. The WebSocket proxy starts alongside on port 8765.

### 4. Install and run the frontend

```bash
cd frontend
npm install
npm run dev
```

### 5. Open the app

Visit **http://127.0.0.1:5173** (the Vite server binds to IPv4 loopback only).

- First visit: create the admin account at `/setup`.
- Login, configure broker credentials at `/broker/config`.
- Select a broker and complete OAuth at `/broker/select`.
- Dashboard shows live funds; the topbar toggles Live ↔ Sandbox.

## Production deployment

`install/install.sh` is a Cloudflare-aware Ubuntu installer that sets up Postgres, Redis, nginx with A-grade security headers, systemd units, certbot, and the swap file (helpful on small VMs). Re-run `install/update.sh` to pull, run `migrate_all.py`, build the frontend, and reload services. `install/perftuning.sh` applies sensible Postgres/Redis kernel + ulimit tuning.

```bash
sudo ./install/install.sh
# follow the certbot prompts; nginx proxies /auth, /web, /upstox, /zerodha, /ws to the backend
```

## API endpoints (33+)

Full per-endpoint docs: [docs/api/README.md](docs/api/README.md). Bruno collection: `collections/openbull/`.

### External API — `/api/v1/*` (API-key auth)

| Group | Endpoints |
|------|-----------|
| Order management | `placeorder`, `placesmartorder`, `basketorder`, `splitorder`, `optionsorder`, `optionsmultiorder`, `modifyorder`, `cancelorder`, `cancelallorder`, `closeposition` |
| Account & info | `funds`, `margin`, `orderbook`, `tradebook`, `positions` (`positionbook`), `holdings`, `orderstatus`, `openposition` |
| Market data | `quotes`, `multiquotes`, `depth`, `history`, `intervals` |
| Symbols & options | `symbol`, `search`, `expiry`, `optionsymbol`, `optionchain`, `syntheticfuture`, `optiongreeks` |
| Analytics tools | `oitracker`, `maxpain`, `ivchart`, `ivsmile`, `volsurface`, `straddle`, `gex` |
| Utility | `ping`, `analyzer` |

### Web API — `/web/*` (session cookie auth)

| Group | Endpoints |
|------|-----------|
| Auth / setup | `auth/check-setup`, `auth/setup`, `auth/login`, `auth/logout`, `auth/me`, `auth/broker-redirect`, broker callbacks |
| Strategies | `strategies` (CRUD), `strategybuilder/snapshot`, `strategybuilder/chart`, `strategybuilder/multi-strike-oi` |
| Trading mode | `trading-mode` (GET/POST) |
| Sandbox | `sandbox/config`, `sandbox/reset`, `sandbox/summary`, `sandbox/mypnl`, `sandbox/squareoff-now`, `sandbox/settle-now` |
| Logs | `api-logs`, `error-logs` |
| Symbols | `symbols/search`, `symbols/underlyings`, `symbols/status` (master-contract download status) |

### WebSocket Streaming (port 8765)

```json
{"action": "authenticate", "api_key": "..."}
{"action": "subscribe", "symbols": [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}], "mode": "Quote"}
```

| Mode | Wire string | Data |
|------|-------------|------|
| LTP | `"LTP"` | Last traded price + change/cp/ltq, 50ms throttle |
| Quote | `"QUOTE"` | OHLCV + OI + volume + bid/ask totals |
| Depth | `"DEPTH"` / `"FULL"` | 5-level bid/ask market depth |

Mode hierarchy: subscribing to DEPTH includes QUOTE includes LTP automatically. Limits: 10 concurrent client connections, 64 KB max message size, 1000 symbols per subscribe call. Full protocol: [docs/design/websockets-format.md](docs/design/websockets-format.md).

## Features

### Strategy Builder + Strategy Portfolio (`/tools/strategybuilder` + `/tools/strategyportfolio`)

The flagship feature pair. UI ported piece-by-piece from openalgo's reference design (and in some places now ahead of it).

**Symbol header** — chip strip across the top: Spot, **Futures** (synthetic via put-call parity, never a separate broker call), ATM IV, DTE (with end-of-day IST rollover), Lot Size, plus a live-status pill.

**30 strategy templates** rendered as a card gallery with **mini SVG payoff icons** — direction tabs (Bullish / Bearish / Neutral) with live counts, search box. Clicking a card opens **TemplateConfigDialog**: a preview modal listing the resolved legs (chain-sourced strike dropdowns, editable lots / entry, net debit/credit summary) so the user can tweak before applying. Calendar / diagonal templates correctly resolve `expiryOffset` against the broker's expiry list.

**Add a Position** card — manual leg builder with explicit Segment / Expiry / Strike (with ATM / ITMn / OTMn moneyness chip) / Type / Side / Lot Qty stepper + dedicated ADD BUY and ADD SELL buttons. Resolves the OpenAlgo symbol live and shows the chain LTP at the top right.

**Six tabs**:
- **Legs** — manual builder + leg list with chain-sourced strike dropdown + moneyness chip; pencil opens `EditLegDialog` (full per-leg modal with auto-LTP refill on strike/type change), trash deletes.
- **Greeks** — per-leg + aggregate Greeks, plain-English labels (Delta / Gamma / Theta / Vega — never Δ Γ Θ V; openbull is a retail product).
- **Payoff** — two-pane: `PositionsPanel` on the left (per-leg checkbox toggle, 2-column stats grid for Max Profit / Max Loss / POP / RR / Total P&L / Net Credit / Est. Premium / **Margin Req.** sourced from `/api/v1/margin`, breakevens row with chip badges, Save + Execute Basket actions in the footer); At-Expiry + T+0 curves + sigma bands + breakeven markers + spot vertical + "Unlimited" annotations + POP badge on the right.
- **Strategy Chart** — historical combined-premium time series on `lightweight-charts`, P&L vs Premium toggle, per-leg lines, underlying overlay on a secondary y-axis, openalgo-parity columns (`net_premium`, `combined_premium`, `tag` credit/debit/flat).
- **Multi-Strike OI** — per-leg Open Interest history overlaid on `lightweight-charts`, underlying close on the second axis, IST timestamps.
- **P&L** — WS-streamed live MTM with flash cells.

**What-if simulator** — Spot ±10%, IV ±10pp, Days forward sliders drive both the magenta marker AND the dashed T+0 curve on the payoff chart (T+0 re-prices every leg under the shifted IV / time, not just the marker). Local-only Black-76 — instant feedback, no broker round-trips.

**Save / load** via `/web/strategies/*` (per-user, JSONB legs, tagged with trading mode). Saved strategies are immediately visible in an in-page **Load Strategy** picker (no need to bounce to the Portfolio page); the cache invalidates on save so a fresh save is always one click away.

**Basket execute** via `/api/v1/basketorder` — `BasketOrderDialog` shows per-leg rows with Use checkbox, editable Lots, editable tick-snapped Price (disabled for MARKET / SL-M), Pricetype + Product as global controls, BUY-before-SELL ordering enforced server-side, inline per-row result indicators (✓ orderid on success, × message on error). Sandbox-aware.

**Strategy Portfolio**: list with mode / status / underlying filters (status as a segmented pill group), 2-column card grid on lg+ viewports, expandable cards, **one shared WebSocket** across the page (a symbol used in three strategies streams once), live aggregate P&L per card, close-at-exit dialog with editable per-leg exit prices, hard delete.

### Analytics Tools (`/tools`)

| Page | Backend service | API |
|---|---|---|
| Option Chain | `option_chain_service` | `/api/v1/optionchain` (+ live WS) |
| OI Tracker | `oi_tracker_service` | `/api/v1/oitracker` |
| Max Pain | `max_pain_service` | `/api/v1/maxpain` |
| Option Greeks (historical) | `option_greeks_service` | `/api/v1/optiongreeks` |
| IV Smile | `iv_smile_service` | `/api/v1/ivsmile` |
| Vol Surface (3D) | `vol_surface_service` | `/api/v1/volsurface` |
| Straddle Chart | `straddle_chart_service` | `/api/v1/straddle` |
| GEX Dashboard | `gex_service` | `/api/v1/gex` |

All analytics pages are `React.lazy` so Plotly's heavy bundle is fetched only on navigation. IST timestamp handling is consistent across every chart. CE = RED (`#ef4444`) and PE = GREEN (`#22c55e`) by convention — high CE OI is bearish.

### Sandbox Simulated Trading

A tick-driven simulation engine (`backend/sandbox/`) with full lifecycle parity to live trading. Every order/info service routes through `dispatch_by_mode` so the same UI / API drives either real or simulated orders.

- **Per-user simulated capital + margin lifecycle** — margin booked at order placement, transferred to position on fill, released pro-rata on reduce/close.
- **Tick-driven fills** with a 5-second polling fallback when the WS feed is quiet.
- **Scheduled squareoff** (per-bucket: NSE/NFO/BSE/BFO at 15:15, CDS at 16:45, MCX at 23:30 — configurable in `/sandbox`).
- **T+1 settlement** — CNC fills move to a holdings table on EOD; daily P&L snapshots persisted.
- **Catch-up on restart** — stale MIS positions, T+1 settlement, today_realized_pnl reset, expired F&O contracts all reconciled before the engine starts ticking.

`/sandbox` config page lets admins tune starting capital, leverage, squareoff times. `/sandbox/mypnl` shows the per-user daily P&L history.

### Symbol Search (`/search`)

OpenAlgo-style tokenized async search across the full master-contract table. Multi-token AND matching, exchange filters, instrument-type filters. The same search powers the Underlying combobox in every options tool.

### Live / Sandbox Toggle

Topbar switch. Persists in `app_settings` (single global flag), with a 10s in-memory cache to avoid a DB hit per request. The frontend tints the theme amber when sandbox is active and shows a banner above the page content. Strategies saved while sandbox is on are tagged accordingly so the Portfolio's "live" filter doesn't bleed.

### MarketDataCache + WebSocket Test (`/websocket/test`)

Process-wide `MarketDataCache` singleton receives every tick from the WS proxy and exposes them to internal consumers (sandbox MTM updater, ticker pages). The `/websocket/test` dev page reads cached ticks for arbitrary symbols and shows the connection state — useful when diagnosing broker-side issues without opening a fresh subscription.

### Logging & Audit

- **Request-id stamping** — every log line is correlated to a request via a contextvar; copies into the response header.
- **Sensitive-data redaction** — automatic stripping of `access_token`, `api_secret`, etc. from log payloads.
- **Bounded rotating files** — `openbull.log` and `openbull-error.log` capped at 10×10 MB each (200 MB total on disk).
- **DB-backed `error_logs` and `api_logs` tables** with worker-trimmed row caps so attacker floods can't blow up the table.
- **`/logs` viewer** in-app — filter by trading mode, status, time range; auth-gated.

## Project Structure

Full directory tree: [docs/design/ARCHITECTURE.md](docs/design/ARCHITECTURE.md). Top-level summary:

```
openbull/
├── alembic/                    # Migrations (idempotent under migrate_all.py)
├── install/                    # install.sh, update.sh, perftuning.sh
├── backend/
│   ├── main.py                 # FastAPI app, lifespan, middleware order
│   ├── config.py               # Pydantic Settings
│   ├── database.py             # SQLAlchemy async engine + Base
│   ├── security.py             # Argon2, Fernet, JWT
│   ├── dependencies.py         # FastAPI DI + Redis cache-aside
│   ├── middleware*.py          # Request-id, access log, DB-backed api_logs
│   ├── models/                 # SQLAlchemy ORM (users, broker_*, sandbox_*, strategies, ...)
│   ├── schemas/                # Pydantic request/response
│   ├── routers/                # Web routes (cookie auth)
│   ├── api/                    # External API (/api/v1, key auth)
│   ├── services/               # Business logic — order, options, analytics,
│   │                           #   strategy_builder, strategy_chart,
│   │                           #   multi_strike_oi, sandbox, trading_mode,
│   │                           #   market_data_cache, margin
│   ├── sandbox/                # Simulated trading engine
│   ├── broker/                 # 5 broker plugins
│   │   ├── upstox/, zerodha/, angel/, dhan/, fyers/
│   ├── websocket_proxy/        # Unified WS proxy (ZeroMQ architecture)
│   └── utils/                  # logging, redis_client, symtoken_cache, plugin_loader
├── frontend/
│   └── src/
│       ├── pages/              # incl. tools/StrategyBuilder, tools/StrategyPortfolio
│       ├── components/         # incl. strategy-builder/, strategy-portfolio/
│       ├── hooks/              # useMarketData, useChainContext, useStrategySnapshot
│       ├── api/                # Typed wrappers per feature
│       └── lib/                # black76, probabilityOfProfit, strategyTemplates
├── collections/                # Bruno API collection
├── docs/                       # api/, design/ (ARCHITECTURE, SERVICES, websockets, symbol-format)
└── .env.example
```

## Broker Plugin System

Each broker lives in `backend/broker/{name}/`:

```
broker/{name}/
├── plugin.json                 # name, display_name, supported_exchanges, oauth_type
├── api/
│   ├── auth_api.py             # OAuth token exchange
│   ├── order_api.py            # place / modify / cancel
│   ├── funds.py                # account funds + margin
│   ├── data.py                 # quotes / depth / history; SUPPORTED_INTERVALS map
│   └── margin_api.py           # pre-trade margin calculator
├── mapping/
│   ├── transform_data.py       # OpenBull <-> broker order fields
│   ├── order_data.py           # response mapping (token <-> symbol)
│   └── margin_data.py          # margin request/response normalization
├── streaming/
│   └── {broker}_adapter.py     # WebSocket → ZMQ adapter
└── database/
    └── master_contract_db.py   # Symbol download + symtoken bulk insert
```

To add a broker: create the directory, implement the modules, drop a `plugin.json`, and add the name to `VALID_BROKERS` in `.env`. The plugin loader picks it up on next startup.

## WebSocket Architecture

```
Client WS (port 8765)
    ⇅ JSON                                                    
WS Proxy (asyncio + websockets)
    ⇅ ZeroMQ SUB <—— PUB ⇅
                          Broker Adapter (background thread)
                          ⇅ broker-native protocol
                          Upstox protobuf v3 / Zerodha binary
                                ⇣
                          MarketDataCache singleton (every tick)
```

`MarketDataCache` is the single source of live data for internal consumers. The proxy supports DEPTH ⊇ QUOTE ⊇ LTP hierarchy so subscribing to DEPTH delivers all three message types automatically.

## Environment Variables

Selected — see `.env.example` for the full list.

| Variable | Description | Default |
|---|---|---|
| `APP_SECRET_KEY` | JWT signing key (required) | — |
| `ENCRYPTION_PEPPER` | Fernet/Argon2 pepper (required) | — |
| `DATABASE_URL` | PostgreSQL async DSN | `postgresql+asyncpg://postgres:123456@localhost/openbull` |
| `REDIS_URL` | Redis (cache + symtoken mirror) | `redis://127.0.0.1:6379/0` |
| `FRONTEND_URL` | OAuth-redirect base (host substituted dynamically per request) | `http://127.0.0.1:5173` |
| `CORS_ORIGINS` | Allowed origins, comma-separated | `http://127.0.0.1:5173,http://localhost:5173` |
| `VALID_BROKERS` | Enabled broker plugins | `upstox,zerodha` |
| `COOKIE_SECURE` | Set true in production over HTTPS | `false` |
| `SESSION_EXPIRY_TIME` | Daily session expiry IST | `03:00` |
| `WEBSOCKET_HOST` / `WEBSOCKET_PORT` | WS proxy bind | `127.0.0.1:8765` |
| `WEBSOCKET_URL` | External WS URL (for SDK consumers) | `ws://127.0.0.1:8765` |
| `ZMQ_HOST` / `ZMQ_PORT` | ZeroMQ bus | `127.0.0.1:5555` |
| `MAX_SYMBOLS_PER_WEBSOCKET` | Symbols per WS connection | `1000` |
| `MAX_WEBSOCKET_CONNECTIONS` | Concurrent WS clients | `3` |
| `LOG_FILE_MAX_MB` / `LOG_FILE_BACKUP_COUNT` | Rotating log size + retention | `10` / `9` |
| `ERROR_LOG_DB_MAX_ROWS` / `API_LOG_DB_MAX_ROWS` | DB-backed log table caps | `50000` / `100000` |

## Production Build

```bash
cd frontend && npm run build && cd ..
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves `frontend/dist/` automatically. The WebSocket proxy starts alongside on port 8765. Behind nginx, proxy `/auth`, `/web`, `/upstox`, `/zerodha`, `/api`, `/health`, `/ws` to the backend (`install/install.sh` does this for you).

## Documentation

- [API Reference](docs/api/README.md) — per-endpoint docs with tested request/response samples
- [Architecture](docs/design/ARCHITECTURE.md) — system design, data flows, design patterns, caching layer, logging & audit, trading modes, analytics tools, Strategy Builder
- [Services](docs/design/SERVICES.md) — business logic layer, ~30 services
- [WebSocket Protocol](docs/design/websockets-format.md) — wire format, modes, limits
- [Symbol Format](docs/design/symbol-format.md) — OpenAlgo-compatible symbology
- [Order Constants](docs/design/order-constants.md) — valid exchanges, products, pricetypes, actions
- [Product Overview](docs/PRODUCT.md) — feature-level summary

## License

AGPL-3.0
