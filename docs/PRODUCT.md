# OpenBull — Product Overview

| Field | Value |
|---|---|
| **Last updated** | 2026-05-11 |
| **Owner** | Platform team (rajandran) |
| **Status** | Stable — covers all shipped features |
| **Source of truth** | Backend code under `backend/`, frontend under `frontend/src/` |
| **Change history** | [docs/CHANGELOG.md](./CHANGELOG.md) |

> A self-hosted options trading platform for Indian markets. Multi-user, multi-broker, OpenAlgo-compatible REST API, full options-analytics suite, multi-leg strategy designer, and a tick-driven sandbox simulator.

---

## 1. What OpenBull Is

OpenBull is an options-first trading platform that you run on your own infrastructure. It sits between a trader's tools — UIs, scripts, AI agents, third-party integrations — and the actual Indian broker APIs (Upstox, Zerodha, Angel One, Dhan, Fyers).

It does three jobs:

1. **Unifies five broker APIs** behind one consistent REST + WebSocket surface that mirrors the OpenAlgo standard. The same client code works regardless of which broker the logged-in user is connected to.
2. **Adds an options-analytics layer** on top — option chain, IV smile, vol surface, GEX, OI tracker, max-pain, straddle chart, Greeks, plus a multi-leg Strategy Builder with live P&L, payoff curves, and basket execution.
3. **Provides a sandbox simulator** that mirrors the production order surface exactly, so the same UI and the same API drive either real broker orders or simulated fills against live ticks.

OpenBull is not a SaaS. There is no central cloud component. You own your data, your broker tokens, and your trading history.

---

## 2. Who It's For

| Persona | What OpenBull gives them |
|---|---|
| **Algo traders** | A typed REST API that abstracts broker differences. Write your strategy once; switch brokers without touching client code. |
| **Options traders** | An end-to-end options workflow: chain → Greeks → strategy design → margin check → basket execution → portfolio monitoring. |
| **Tool builders** | OpenAlgo-compatible JSON shapes so existing TradingView/Amibroker/Excel/SDK integrations keep working. |
| **AI / LLM agents** | A clean, predictable JSON surface plus per-request rate-limited and audited execution. |
| **Self-hosters who care about data ownership** | Everything — credentials, orders, P&L history, audit logs — stays on your machine. AGPL-3.0. |

---

## 3. Capability Matrix

### 3.1 Broker coverage (5 plugins, all production-grade)

| Broker | Auth flow | Streaming protocol | Master contract | REST |
|---|---|---|---|---|
| **Upstox** | OAuth (`auth_code`) | Protobuf v3 over WSS | Auto-download | Full |
| **Zerodha** | Kite Connect OAuth (`request_token`) | KiteTicker binary | Auto-download | Full |
| **Angel One** | Credentials + TOTP | SmartStream binary | Auto-download | Full |
| **Dhan** | Static access token | Dhan binary | Auto-download | Full |
| **Fyers** | OAuth (`auth_code`) | HSM binary (fyers v3) | Auto-download | Full |

Each broker is a self-contained plugin under `backend/broker/{name}/` — auth, orders, funds, data, margin, master-contract download, and a WebSocket adapter. Adding a sixth broker is one folder plus a `plugin.json`. See [docs/design/broker-integration.md](design/broker-integration.md).

### 3.2 External API (`/api/v1/*`)

API-key authenticated. Identical shape across all five brokers. Built for OpenAlgo SDK / script compatibility.

| Group | Endpoints |
|---|---|
| **Order management** | `placeorder`, `placesmartorder`, `basketorder`, `splitorder`, `optionsorder`, `optionsmultiorder`, `modifyorder`, `cancelorder`, `cancelallorder`, `closeposition` |
| **Order info** | `orderbook`, `tradebook`, `positions`, `holdings`, `orderstatus`, `openposition` |
| **Account** | `funds`, `margin` (pre-trade calculator with hedge benefit) |
| **Market data** | `quotes`, `multiquotes`, `depth`, `history`, `intervals` |
| **Symbols** | `symbol`, `search`, `expiry`, `optionsymbol` |
| **Options analytics** | `optionchain`, `optiongreeks`, `syntheticfuture`, `oitracker`, `maxpain`, `ivchart`, `ivsmile`, `volsurface`, `straddle`, `gex` |
| **Utility** | `ping`, `analyzer` |

Every order path passes through a single `dispatch_by_mode` helper: if the global trading mode is `sandbox`, the same endpoint routes to the simulator instead of the broker. SDK consumers don't have to know — `/api/v1/placeorder` behaves identically; only the fills are simulated.

### 3.3 Web API (`/web/*`)

Cookie-authenticated. Powers the SPA. Not part of the OpenAlgo-compatible surface; integrations should target `/api/v1`.

| Group | Endpoints |
|---|---|
| Auth & setup | `auth/check-setup`, `auth/setup`, `auth/login`, `auth/logout`, `auth/me`, `auth/broker-redirect`, per-broker OAuth callbacks |
| Strategies | `strategies` (CRUD), `strategybuilder/snapshot`, `strategybuilder/chart`, `strategybuilder/multi-strike-oi` |
| Trading mode | `trading-mode` (GET/POST — global Live ↔ Sandbox toggle) |
| Sandbox | `sandbox/config`, `sandbox/reset`, `sandbox/summary`, `sandbox/mypnl`, `sandbox/squareoff-now`, `sandbox/settle-now` |
| Logs | `api-logs`, `error-logs` |
| Symbols | `symbols/search`, `symbols/underlyings`, `symbols/status` |

### 3.4 WebSocket streaming (port 8765)

Single endpoint, broker-agnostic. JSON wire protocol. Subscribe-once-fanout-many design — a symbol used by three browser tabs streams from the broker once.

| Mode | Wire string | Payload |
|---|---|---|
| LTP | `"LTP"` | Last price + previous-close, change, change%, ltq, ltt (50 ms per-symbol throttle) |
| Quote | `"QUOTE"` | LTP + OHLC, volume, OI, ATP, total buy/sell quantity |
| Depth | `"DEPTH"` (or `"FULL"`) | Quote + 5-level bid/ask book |

Mode hierarchy: `DEPTH ⊇ QUOTE ⊇ LTP` — subscribing to DEPTH delivers all three. Full protocol: [docs/design/websockets-format.md](design/websockets-format.md).

### 3.5 Analytics tools (8 in-app pages + REST mirrors)

Every analytics tool is both a web page (`/tools/*`) and an external API endpoint. The web pages are Plotly-backed and code-split — Plotly's ~600 KB bundle is fetched only on tool navigation.

| Tool | Page | API | Purpose |
|---|---|---|---|
| Option Chain | `/tools/optionchain` | `/api/v1/optionchain` | Full CE+PE chain with live LTP/IV/OI per strike, in-line order entry. |
| OI Tracker | `/tools/oitracker` | `/api/v1/oitracker` | Per-strike OI deltas across the chain (timestamped). |
| Max Pain | `/tools/maxpain` | `/api/v1/maxpain` | Max-pain strike per expiry. |
| Option Greeks | `/tools/greeks` | `/api/v1/optiongreeks` | Historical IV + Delta/Gamma/Theta/Vega series. |
| IV Smile | `/tools/ivsmile` | `/api/v1/ivsmile` | Per-strike CE/PE IV at a chosen expiry, with ATM IV badge. |
| Vol Surface | `/tools/volsurface` | `/api/v1/volsurface` | 3-D IV surface across strikes × expiries (Plotly mesh3d). |
| Straddle Chart | `/tools/straddle` | `/api/v1/straddle` | Historical ATM straddle premium + synthetic future overlay. |
| GEX Dashboard | `/tools/gex` | `/api/v1/gex` | Gamma exposure profile, zero-gamma index (ZGI), GEX flip. |

Conventions: IST timestamps everywhere; CE = red, PE = green; off-hours fallback to last close + cached IV when bid/ask collapse.

### 3.6 Strategy Builder + Portfolio (flagship)

Paired pages implementing the full lifecycle of a multi-leg options strategy: **design → save → live monitor → close**.

#### `/tools/strategybuilder` — six-tab designer

- **Symbol header** — chip strip: Spot, Futures (synthetic via put-call parity at ATM, never a separate broker call), ATM IV, DTE (with 15:30-IST roll), Lot Size, plus a live-status pill.
- **30 strategy templates** — card gallery with mini SVG payoff icons; direction tabs (Bullish / Bearish / Neutral) with live counts; click a card to open a preview dialog with resolved strikes, editable lots/entry, net debit/credit summary.
- **Add a Position** — manual leg builder with Segment / Expiry / Strike (with ATM / ITMn / OTMn moneyness chip) / Type / Side / Lot-Qty stepper, dedicated ADD BUY and ADD SELL buttons.
- **Tabs:**
  - **Legs** — leg list, in-line strike dropdowns, pencil opens a full per-leg modal with auto-LTP refill.
  - **Greeks** — per-leg + aggregate. Plain-English labels (Delta / Gamma / Theta / Vega — never the Greek glyphs; this is a retail product).
  - **Payoff** — two-pane: `PositionsPanel` on the left (per-leg checkbox toggle, 2-column stats grid for Max Profit / Max Loss / POP / Risk-Reward / Total P&L / Net Credit / Est. Premium / **Margin Req.** from `/api/v1/margin`, breakeven chips). On the right, the payoff chart with At-Expiry + T+0 curves, ±1σ/±2σ bands sized off the shortest-DTE leg, breakeven verticals, spot vertical, "Unlimited" annotations, POP badge.
  - **Strategy Chart** — historical combined-premium time series on `lightweight-charts`. P&L vs Premium toggle, per-leg lines, optional underlying overlay on the right axis.
  - **Multi-Strike OI** — per-leg historical OI overlaid; underlying close on a secondary axis.
  - **P&L** — WS-streamed live MTM, flash-on-tick cells, stale-tick warning at 30 s.
- **What-if simulator** — Spot ±10%, IV ±10pp, Days-forward sliders drive both the magenta marker *and* the dashed T+0 curve (every leg re-priced under the shifted IV / time, not just the marker). Pure-frontend Black-76 — instant feedback, no broker round-trips.
- **Save / load** — `/web/strategies/*` (per-user, JSONB legs, tagged with trading mode). Saved strategies show up immediately in an in-page dropdown — no need to bounce to the Portfolio page.
- **Basket execute** — `/api/v1/basketorder` with absolute symbols (not offset-resolved at execute time, which would risk a different strike). Dialog shows per-leg rows with Use checkbox, editable Lots, editable tick-snapped Price, BUY-before-SELL ordering, inline per-row result indicators.

#### `/tools/strategyportfolio` — saved-strategy monitor

- Mode / status / underlying filters; 2-column card grid on lg+ viewports.
- **One shared WebSocket** across the entire page — a symbol used by three strategies streams once.
- Live aggregate P&L per card; close-at-exit dialog with editable per-leg exit prices; hard delete with optimistic UI.

### 3.7 Interactive API Playground (`/playground`)

A Postman/Stripe-style API tester baked into the platform — every REST and WebSocket endpoint discoverable from a sidebar, request bodies pre-filled with sensible defaults from the Bruno collection, responses syntax-highlighted with status, latency, and payload-size readouts.

- **Sidebar of endpoints** — six collapsible categories (account, orders, data, analytics, utilities, websocket) populated from `collections/openbull/IN_stock/*.bru`. Search across name and path.
- **Multi-tab REST tester** — each endpoint opens in its own tab with a modified-dot indicator. CodeMirror JSON editor with bracket matching and auto-indent on the request side; syntax-highlighted response on the right with status pill, latency (ms), payload size, line numbers, cURL export, copy.
- **WebSocket mode** — same page, REST/WebSocket toggle in the topbar. ConnectionPanel with status pill, Connect/Disconnect/Ping, last + average latency, auto-reconnect. MessageComposer with categorised template gallery (Authenticate / Subscribe / Depth / Unsubscribe / Utility) and Ctrl/Cmd+Enter to send. MessageLog with timestamp + direction badges, expandable JSON, search filter, JSON export.
- **Mode-aware** — the topbar Live ↔ Sandbox toggle is bound to the global `useTradingMode()` context, so a `/api/v1/placeorder` you fire from the Playground routes through the same `dispatch_by_mode` as the live UI. Test in sandbox; flip to live when you're ready.
- **API key pre-injected** — cookie auth picks up the caller's API key from the backend and pre-fills `apikey` fields in request bodies and the WebSocket authenticate template. Show/hide and copy controls in the sidebar footer.

### 3.8 Sandbox simulated trading

`backend/sandbox/` — tick-driven simulation engine with full lifecycle parity to live trading.

- **Per-user simulated capital** seeded from `sandbox_config`; intraday MIS uses leveraged margin, CNC uses cash.
- **Margin lifecycle** — booked at placement, transferred to position on fill, released pro-rata on close. Mirrors OpenAlgo's phase-2c contract for SDK parity.
- **Tick-driven fills** — `execution_engine` registers a CRITICAL subscriber on `MarketDataCache`; every tick checks pending orders for that symbol. A 5-second polling fallback keeps LIMIT/SL orders advancing when the feed is quiet.
- **Scheduled squareoff** — per-bucket (NSE/NFO/BSE/BFO at 15:15 IST, CDS at 16:45, MCX at 23:30), configurable in `/sandbox`.
- **T+1 settlement** — CNC fills move into `sandbox_holdings` at end of day; daily P&L snapshot persisted.
- **Catch-up on restart** — `run_catch_up_tasks()` replays squareoffs, T+1 settlement, `today_realized_pnl` reset, and expired-F&O closures missed while the app was down — *before* the engine starts ticking, so the first tick sees a consistent book.
- **MTM updater** — separate daemon recomputes mark-to-market on open positions every few seconds.

The frontend reflects sandbox via `TradingModeContext`: a topbar switch flips the mode, the layout tints amber when sandbox is active, and `/sandbox/mypnl` surfaces the per-user P&L history.

### 3.9 Symbol search (`/search`)

Tokenized async search across the full master-contract table (~120k rows). Multi-token AND matching, exchange filters, instrument-type filters. The same search component powers the underlying-picker combobox in every options tool.

### 3.10 Logging & audit

- **Request-id stamping** — every log line and every response header is correlated to a `X-Request-ID` via a contextvar.
- **Sensitive-data redaction** — automatic regex stripping of `apikey` / `access_token` / `api_secret` / `Authorization` / `Bearer` payloads on every log record, including request/response bodies in `api_logs`.
- **Rotating file handlers** — `openbull.log` (all levels) and `openbull-error.log` (WARNING+), each capped at `LOG_FILE_MAX_MB × (LOG_FILE_BACKUP_COUNT + 1)` (100 MB per file by default).
- **DB-backed sinks** — `api_logs` (auth-gated, request-bodied) and `error_logs` (WARNING+). Both bounded to a configurable row cap so attacker floods can't blow up storage.
- **`/logs` viewer** — auth-gated in-app browser with filtering by trading mode, status, method, time range, and free-text path; full request/response bodies open in a modal.

---

## 4. User Journeys

### 4.1 First-time setup

1. Visit `http://127.0.0.1:5173` (dev) or your domain (prod).
2. Create the admin account at `/setup`.
3. Log in. The dashboard refuses to load until a broker is connected — by design.
4. `/broker/config` → fill in API key + secret for at least one broker. The credentials are Fernet-encrypted at rest before they touch the database.
5. `/broker/select` → pick the broker; complete OAuth in the popup. The callback redirects back using the *request* hostname (not a hard-coded one) so cookies survive whether the user is on `localhost` or `127.0.0.1`.
6. Dashboard now loads with live funds. The topbar shows the Live ↔ Sandbox toggle.

### 4.2 Daily login

1. Sessions expire at `SESSION_EXPIRY_TIME` IST (default `03:00`) — so a daily cron-style relogin is one screen.
2. On login, the broker token is *revalidated* against the broker. Revoked tokens force a re-OAuth instead of letting the user trade with a dead session.
3. JWT lands in an httpOnly cookie; server-side `active_sessions` tracks the JTI so logout revokes immediately, not at token expiry.

### 4.3 SDK / external-script flow

1. `/apikey` → generate an API key. It's shown once; the database stores only its Argon2id hash.
2. The key authenticates `/api/v1/*` either in the JSON body (`"apikey": "..."`) or as a header (`X-API-KEY: ...`).
3. First request: Argon2-verify the key, cache the hash → user-id mapping in Redis for 15 min (negative cache 5 min). Subsequent requests are a Redis `GET`.
4. The broker context (`auth_token`, broker name, encrypted config) is similarly cached as `api_ctx:<user_id>` for 1 h. Logout / OAuth callback / key rotation invalidate it explicitly — nothing relies on TTL eviction for correctness.

### 4.4 Designing and executing a strategy

1. `/tools/strategybuilder` → pick exchange, underlying, expiry.
2. Either click a template card (Bull Call Spread, Iron Condor, …) or build legs manually with the ADD BUY / ADD SELL buttons.
3. **Greeks** tab to sanity-check the position; **Payoff** tab to see the curve, breakevens, max profit/loss, POP, and the broker's margin requirement (with hedge benefit).
4. **What-if** sliders for spot / IV / days forward — the T+0 curve and marker re-price every leg in real time.
5. *Save* → the strategy is tagged with the current trading mode (live or sandbox) so the Portfolio page doesn't mix them.
6. *Execute Basket* → `BasketOrderDialog` opens; per-leg Use / Lots / Price / Pricetype / Product controls. BUY orders go first (broker-side margin efficiency for credit spreads).
7. After execution, switch to `/tools/strategyportfolio` to monitor live aggregate P&L. One WebSocket for the whole page, not one per strategy.

### 4.5 Sandbox-first workflow

1. Toggle Live → Sandbox in the topbar. The whole UI tints amber.
2. Place orders exactly as you would in live mode — same UI, same endpoints. Margin is blocked against your simulated capital.
3. Orders fill against real broker ticks (the sandbox engine subscribes to `MarketDataCache`, not its own feed). LIMIT and SL orders progress on each matching tick.
4. At 15:15 IST, any open MIS positions auto-squareoff at the cached LTP. CNC fills become T+1 holdings at end of day.
5. Reset capital, change leverage, or move squareoff times from `/sandbox`. `/sandbox/mypnl` shows your daily P&L history.

---

## 5. Architecture at a Glance

```
                   React SPA (Vite, port 5173 dev)
                            ↓ HTTPS / WS
        ┌───────────────────┴────────────────────┐
        │     FastAPI app (port 8000)            │
        │  ┌──────────────────────────────────┐  │
        │  │ Middleware: CORS → security      │  │
        │  │   → ApiLogMiddleware             │  │
        │  │   → RequestLoggingMiddleware     │  │
        │  └──────────────────────────────────┘  │
        │           ↓                            │
        │  /web/*  (JWT cookie)   /api/v1/*  (API key)
        │           ↓                            │
        │     Services layer  ← TradingMode dispatch ⇄ Sandbox engine
        │           ↓                            │
        │     Broker plugin (importlib-resolved)
        │           ↓ HTTPS                      │
        │     Upstox / Zerodha / Angel / Dhan / Fyers REST
        └────────────────────────────────────────┘

   WebSocket proxy (port 8765, JSON)            PostgreSQL (asyncpg)
        ↑                                          + ORM models
   ZeroMQ PUB/SUB bus                              users, sessions
        ↑                                           api_keys
   Broker adapter thread                            broker_auth/config
        ↑                                           symtoken (master)
   Broker WS (protobuf / binary)                    sandbox_*
                                                    strategies
                                                    app_settings (mode)
                                                    api_logs / error_logs

   MarketDataCache singleton ← every tick ← ZMQ
        ↓
   - Sandbox execution engine (CRITICAL subscriber)
   - Analytics services (LTP lookups, no extra broker calls)
   - /api/websocket/market-data/* HTTP endpoints

   Redis (cache-aside)
   - api_key:valid|invalid:<sha256>    (15 min / 5 min)
   - broker_ctx:<user_id>              (1 h)
   - api_ctx:<user_id>                 (1 h)
   - symtoken:* hashes                 (master-contract mirror)
   - md:<exchange>:<symbol>            (60 s tick snapshot mirror)
```

Detailed walkthrough: [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md).

---

## 6. Tech Stack

| Layer | Tech |
|---|---|
| **Backend** | FastAPI · Python 3.12+ · SQLAlchemy 2.x async · asyncpg · Pydantic Settings · SlowAPI |
| **Database** | PostgreSQL 15+ · Alembic + idempotent in-place schema migrations |
| **Cache** | Redis 7+ (async client, in-process fallback on outage) |
| **Streaming** | ZeroMQ PUB/SUB · `websockets` (async server) · `websocket-client` (broker side) · protobuf (Upstox v3) · custom binary parsers (Zerodha, Angel, Dhan, Fyers) |
| **Security** | Argon2id (passwords + API-key hashes) · Fernet (broker secrets at rest) · JWT in httpOnly cookies · server-side session revocation by JTI · CSP / X-Frame-Options / X-Content-Type-Options · per-endpoint SlowAPI rate limits |
| **Frontend** | React 19 · Vite · TypeScript (strict) · TanStack Query · Tailwind CSS · shadcn/ui · Base UI primitives · Plotly (cartesian + 3D) · `lightweight-charts` · Sonner toasts |
| **Math (frontend mirror)** | Pure-TS Black-76, lognormal POP, payoff/breakeven solver — kept in sync with the backend's `option_greeks_service` so What-if simulator and snapshot Greeks agree at zero-shift |
| **Package managers** | [uv](https://docs.astral.sh/uv/) (Python) · npm (Node) |

---

## 7. Security Model

| Surface | Mechanism |
|---|---|
| Passwords | Argon2id + per-instance `ENCRYPTION_PEPPER` |
| Broker secrets at rest | Fernet-encrypted (`api_key`, `api_secret`, `auth_token`, refresh tokens) |
| API keys at rest | Argon2id hash; raw key shown once at generation only |
| Web sessions | JWT in httpOnly cookie · IST-aware daily expiry · server-side `active_sessions` table revocable by JTI |
| OAuth callback | Redirect host derived from the request, not configuration — survives `localhost` ↔ `127.0.0.1` cookie split |
| Broker token resume | Re-validated against the broker on every session resume; revoked tokens force re-OAuth |
| API key auth | Argon2-verified on Redis miss; positive cache 15 min, negative cache 5 min |
| WebSocket auth | API key verified before any subscribe |
| Transport | TLS certificate verification on every outbound broker call |
| Headers | CSP, X-Frame-Options `DENY`, X-Content-Type-Options `nosniff`, Referrer-Policy, Permissions-Policy. nginx and FastAPI both set them; install scripts dedupe so they render once. |
| Rate limits | SlowAPI per-endpoint: 10/s order management, 50/s general API, 5/min + 25/h on `/web/auth/login`, 30/min on heavy chart endpoints |
| WebSocket limits | max 10 concurrent clients, 64 KB max message size, 1000 symbols per subscribe call |
| Logging | Sensitive-data redaction on every record; 64 KB body cap + deep redaction in `api_logs`; binary bodies recorded as size only |

Threat model and per-layer details: [docs/design/ARCHITECTURE.md § Security Architecture](design/ARCHITECTURE.md#security-architecture).

---

## 8. Supported Exchanges

| Code | Description |
|---|---|
| `NSE` | National Stock Exchange (cash equities) |
| `BSE` | Bombay Stock Exchange (cash equities) |
| `NFO` | NSE Futures & Options |
| `BFO` | BSE Futures & Options |
| `CDS` | NSE Currency Derivatives |
| `BCD` | BSE Currency Derivatives |
| `MCX` | Multi Commodity Exchange |
| `NCDEX` | National Commodity & Derivatives Exchange |
| `NSE_INDEX` | NSE Index quote feed (NIFTY, BANKNIFTY, …) |
| `BSE_INDEX` | BSE Index quote feed (SENSEX, BANKEX, …) |
| `MCX_INDEX` | MCX commodity index feed |

Validation source of truth: `backend/utils/constants.py`.

Symbol format: OpenAlgo-compatible (`RELIANCE`, `NIFTY28APR2624250CE`, `NIFTY28APR26FUT`). Full spec: [docs/design/symbol-format.md](design/symbol-format.md).

---

## 9. Deployment Modes

| Mode | Typical use | How to run |
|---|---|---|
| **Local dev** | Single trader on a laptop | `uv run uvicorn backend.main:app --reload` + `npm run dev` |
| **LAN single-server** | Trader + small team | One-shot `install/install.sh` on Ubuntu — Postgres, Redis, nginx with A-grade headers, certbot, systemd units, optional swap file |
| **Production** | Always-on, public-facing | Same install script + your domain. `install/update.sh` for pull-migrate-rebuild-reload cycles; `install/perftuning.sh` for Postgres/Redis kernel + ulimit tuning. |

The frontend builds to static `frontend/dist/` and is served by FastAPI in production — no separate node process needed. The WebSocket proxy starts alongside on port 8765 inside the same Python process. Behind nginx, proxy `/auth`, `/web`, `/api`, `/upstox`, `/zerodha`, `/health`, `/ws` to the backend.

Full setup: [README.md § Quick Start](../README.md#quick-start).

---

## 10. Differentiators

What makes OpenBull specifically interesting versus rolling your own or using a vendor SaaS:

1. **Self-hosted, single-binary deployment.** No vendor lock-in, no cloud egress, no third-party reading your fills.
2. **OpenAlgo wire compatibility.** Existing scripts and SDKs work; the migration cost is zero.
3. **Sandbox is *behaviorally identical* to live.** Same API, same UI, same dispatch path — only the fill source differs. You can develop and test against the sandbox with full confidence the live cutover won't surprise you.
4. **Live + simulated coexist in one app.** A topbar flick switches the whole surface; no second deployment, no separate URL.
5. **Options-first.** Strategy Builder + Portfolio is not bolted on — it shares the same chain service, the same Greeks math, the same margin endpoint as the rest of the platform.
6. **Honest math.** Black-76 lives in both Python (source of truth) and TypeScript (instant What-if feedback). The two implementations are tested to agree at zero-shift.
7. **Plug-and-play brokers.** Five plugins shipped. Adding a sixth is one folder.
8. **Production-grade ops baked in.** Sensitive-data redaction, bounded log tables, request-id correlation, rate limits, idempotent migrations, catch-up-on-restart — these aren't roadmap items, they're already there.

---

## 11. Roadmap (in flight)

A separate **Strategy Module** is under active development — server-side multi-leg strategies with risk management, automated entry/exit, and webhook triggers. The plan is in [docs/plan/strategy-module.md](plan/strategy-module.md); phases 1–3 (schema, CRUD, symbol resolver, helper endpoints, strike picker) are merged. Documentation for the user/operator surface will land when the engine and order-dispatch layers are merged.

---

## 12. Where to Read Next

| If you want to… | Read |
|---|---|
| Run OpenBull on your machine | [README.md](../README.md) |
| Understand how the system fits together | [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md) |
| Integrate via REST | [docs/api/README.md](api/README.md) |
| Stream live ticks | [docs/design/websockets-format.md](design/websockets-format.md) |
| Read or build services | [docs/design/SERVICES.md](design/SERVICES.md) |
| Add a new broker | [docs/design/broker-integration.md](design/broker-integration.md) |
| Look up a symbol convention | [docs/design/symbol-format.md](design/symbol-format.md) |
| Check valid exchange / product / pricetype codes | [docs/design/order-constants.md](design/order-constants.md) |
