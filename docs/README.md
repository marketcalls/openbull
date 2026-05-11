# OpenBull Documentation

| Field | Value |
|---|---|
| **Last updated** | 2026-05-11 |
| **Doc-set version** | Aligned with code at commit `29dfd36`. Note: the Strategy Module has advanced from phase 3 (when this doc set was last refreshed) to phase 6 (engine + crash recovery + tick feed). Runtime documentation for the module is queued — caveats remain in [PRODUCT.md](./PRODUCT.md), [ARCHITECTURE.md](./design/ARCHITECTURE.md), [SERVICES.md](./design/SERVICES.md). |
| **Owner** | Platform team |
| **Change history** | [CHANGELOG.md](./CHANGELOG.md) |

Welcome. This is the index for everything under `docs/`. Pick the doc closest to what you're trying to do.

---

## Start here

| Doc | When to read it |
|---|---|
| [`PRODUCT.md`](./PRODUCT.md) | "What is OpenBull?" — capabilities, user personas, user journeys, architecture at a glance. Read this first if you're evaluating the platform. |
| [Top-level README](../README.md) | Quick start: clone, configure, run. Five minutes from `git clone` to a working backend + frontend. |
| [`TUTORIAL.md`](./TUTORIAL.md) | Step-by-step walkthrough — generate an API key, switch to sandbox, place your first order, stream ticks, close the position. ~15 minutes. |

---

## For developers (work inside OpenBull)

| Doc | Topic |
|---|---|
| [`design/ARCHITECTURE.md`](./design/ARCHITECTURE.md) | System design — lifespan, middleware order, routers, services, sandbox engine, ZeroMQ bus, MarketDataCache, Redis cache-aside, logging stack, trading-mode dispatch, event bus, analytics tools, Strategy Builder. |
| [`design/SERVICES.md`](./design/SERVICES.md) | Per-service reference for `backend/services/*`. Function signatures, file:line locations, request/response examples, return-tuple convention. |
| [`design/broker-integration.md`](./design/broker-integration.md) | How to add a sixth broker plugin. Required modules, plugin manifest, master-contract download contract, streaming-adapter ZMQ topics, common pitfalls. |
| [`design/symbol-format.md`](./design/symbol-format.md) | OpenAlgo-compatible symbology — equity, futures, options, indices. Master-contract schema. Symbol round-trip rules. |
| [`design/order-constants.md`](./design/order-constants.md) | Canonical enums — exchanges, product types, price types, actions. Source of truth: `backend/utils/constants.py`. |
| [`design/websockets-format.md`](./design/websockets-format.md) | WebSocket wire protocol — authenticate, subscribe / unsubscribe, mode hierarchy (DEPTH ⊇ QUOTE ⊇ LTP), tick payloads, limits, reconnection. |

---

## For integrators (build against OpenBull's API)

| Doc | Topic |
|---|---|
| [`api/README.md`](./api/README.md) | External API reference index — auth, base URL, response envelope, rate limits, full endpoint catalogue. |

### REST endpoints by group

| Group | Endpoints |
|---|---|
| **Order management** | [placeorder](./api/order-management/placeorder.md) · [placesmartorder](./api/order-management/placesmartorder.md) · [basketorder](./api/order-management/basketorder.md) · [splitorder](./api/order-management/splitorder.md) · [optionsorder](./api/order-management/optionsorder.md) · [optionsmultiorder](./api/order-management/optionsmultiorder.md) · [modifyorder](./api/order-management/modifyorder.md) · [cancelorder](./api/order-management/cancelorder.md) · [cancelallorder](./api/order-management/cancelallorder.md) · [closeposition](./api/order-management/closeposition.md) |
| **Order info** | [orderstatus](./api/order-information/orderstatus.md) · [openposition](./api/order-information/openposition.md) |
| **Account** | [funds](./api/account-services/funds.md) · [margin](./api/account-services/margin.md) · [orderbook](./api/account-services/orderbook.md) · [tradebook](./api/account-services/tradebook.md) · [positionbook](./api/account-services/positionbook.md) · [holdings](./api/account-services/holdings.md) |
| **Market data** | [quotes](./api/market-data/quotes.md) · [multiquotes](./api/market-data/multiquotes.md) · [depth](./api/market-data/depth.md) · [history](./api/market-data/history.md) · [intervals](./api/market-data/intervals.md) |
| **Symbols** | [symbol](./api/symbol-services/symbol.md) · [search](./api/symbol-services/search.md) · [expiry](./api/symbol-services/expiry.md) |
| **Options** | [optionsymbol](./api/options-services/optionsymbol.md) · [optionchain](./api/options-services/optionchain.md) · [optiongreeks](./api/options-services/optiongreeks.md) · [syntheticfuture](./api/options-services/syntheticfuture.md) |
| **Analytics** | [oitracker](./api/analytics-tools/oitracker.md) · [maxpain](./api/analytics-tools/maxpain.md) · [ivchart](./api/analytics-tools/ivchart.md) · [ivsmile](./api/analytics-tools/ivsmile.md) · [volsurface](./api/analytics-tools/volsurface.md) · [straddle](./api/analytics-tools/straddle.md) · [gex](./api/analytics-tools/gex.md) |

### WebSocket streaming

| Mode | Doc |
|---|---|
| LTP | [websocket-streaming/ltp.md](./api/websocket-streaming/ltp.md) |
| Quote | [websocket-streaming/quote.md](./api/websocket-streaming/quote.md) |
| Depth | [websocket-streaming/depth.md](./api/websocket-streaming/depth.md) |
| Full protocol | [design/websockets-format.md](./design/websockets-format.md) |

---

## For operators (deploy and run OpenBull)

| Doc | Topic |
|---|---|
| [`design/RUNBOOK.md`](./design/RUNBOOK.md) | **Operations playbook** — troubleshooting, monitoring, common failure modes, debugging recipes, backup/recovery, ops cheatsheet. The first place to look when something breaks. |
| [Top-level README § Production deployment](../README.md#production-deployment) | `install/install.sh`, `install/update.sh`, `install/perftuning.sh` — Cloudflare-aware Ubuntu installer with nginx, systemd, certbot. |
| [`design/ARCHITECTURE.md` § Security Architecture](./design/ARCHITECTURE.md#security-architecture) | Threat model, layered controls (passwords, broker secrets, sessions, OAuth callback, API keys, WS auth, transport, headers, rate limits, log redaction). |
| [`design/ARCHITECTURE.md` § Caching Layer](./design/ARCHITECTURE.md#caching-layer-redis) | Redis key catalogue, TTLs, invalidation rules, master-contract mirror. |
| [`design/ARCHITECTURE.md` § Logging & Audit](./design/ARCHITECTURE.md#logging--audit) | Request-id correlation, sensitive-data redaction, rotating files, DB-backed `api_logs` / `error_logs`, `/logs` viewer. |
| [`design/ARCHITECTURE.md` § Trading Modes](./design/ARCHITECTURE.md#trading-modes-live--sandbox) | Live ↔ Sandbox toggle, sandbox engine, scheduled squareoff, T+1 settlement, catch-up on restart. |

---

## In-flight work (read for context, not yet usable)

| Doc | Status |
|---|---|
| [`plan/strategy-module.md`](./plan/strategy-module.md) | Implementation plan for the server-side multi-leg Strategy Module with risk management, scheduled entry/exit, and webhook triggers. **Phases 1–3 merged** (schema, CRUD, symbol resolver, helper endpoints, strike picker). Execution engine and order-dispatch layers are work in progress. The runtime user/operator documentation will be added here once the engine ships. Distinct from the already-shipped Strategy Builder + Portfolio pair. |

---

## Conventions Used in These Docs

- **File:line references** — anywhere you see `backend/services/order_service.py:109`, the line number is the verified location at the time of writing. Drift is possible; line numbers are advisory, the file path is canonical.
- **Symbol examples** — use OpenAlgo format throughout: `RELIANCE`, `NIFTY28APR26FUT`, `NIFTY28APR2624250CE`. See [symbol-format.md](./design/symbol-format.md) for the full spec.
- **Exchange / product / price-type codes** — must come from the canonical enums in [order-constants.md](./design/order-constants.md). The validation source of truth is `backend/utils/constants.py`.
- **Response envelope** — every `/api/v1` endpoint returns `{"status": "success", ...}` or `{"status": "error", "message": "..."}`. HTTP status codes follow REST norms (200, 400, 401, 404, 429, 500).
- **IST timestamps** — all time-based axes and `as_of` fields are computed in `Asia/Kolkata` with a fixed `+05:30` offset. No DST, no pytz.

---

## How to Update These Docs

1. Update the code first; the doc follows.
2. When changing a service function signature, update its row in [`SERVICES.md`](./design/SERVICES.md) and any API doc that calls it.
3. When changing a constant in `backend/utils/constants.py`, update [`order-constants.md`](./design/order-constants.md), the api/README exchange table, and any per-endpoint exchange lists.
4. When adding a new broker, follow [`broker-integration.md`](./design/broker-integration.md) and bump the broker count in [`PRODUCT.md`](./PRODUCT.md) and the top-level README.
5. When shipping a phase of the Strategy Module, replace the "in-flight" stanza above with concrete documentation, and remove the matching caveats in [`SERVICES.md`](./design/SERVICES.md) and [`ARCHITECTURE.md`](./design/ARCHITECTURE.md).
6. **Every non-trivial doc edit gets a line in [CHANGELOG.md](./CHANGELOG.md).** Group by severity (HIGH = drift-fix, MEDIUM = stale-fact-fix, NEW = new doc). Bump the `Last updated` field at the top of the affected doc.

---

## License

OpenBull is licensed under AGPL-3.0. See [License.md](../License.md).
