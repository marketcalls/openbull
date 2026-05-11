# Documentation Changelog

This is the change history for the documentation set under `docs/`. It tracks revisions to documents themselves, not feature changes — for the latter, read `git log` on `backend/`/`frontend/`.

The most recent entries are at the top. Older entries are pruned to a rolling 12 months once they age out.

---

## 2026-05-11 — `/playground` Interactive API Tester

**Scope**: new in-app surface for testing every REST + WebSocket endpoint that OpenBull exposes. Ported from openalgo's `/playground` to openbull's React + FastAPI stack; pixel-identical layout, openbull branding, mode wired to `useTradingMode()`.

**New documentation**
- `docs/design/websockets-format.md` — new "Application-level Ping" section documenting the `{action: "ping", _pingId, timestamp}` ↔ `{type: "pong", _pingId, timestamp}` round-trip used by the Playground for latency measurement. This is distinct from the WS protocol's frame-level ping which the browser handles internally.

**Feature additions**
- `backend/routers/playground.py` — three cookie-authed helper endpoints: `GET /web/playground/{api-key,endpoints,host}` ([details in API ref](api/README.md)).
- `backend/websocket_proxy/server.py` — `ping` action handler so the Playground's Connection Panel can compute round-trip latency.
- `collections/openbull/IN_stock/*.bru` — 14 new Bruno endpoint files (8 HTTP + 6 WebSocket): CloseAllPositions, PlaceSmartOrder, OITracker, MaxPain, IVChart, IVSmile, VolSurface, Straddle, GEX, WS_Authenticate, WS_Subscribe_{LTP,Quote,Depth}, WS_Unsubscribe, WS_Ping.
- Frontend: `/playground` route (top-level, full-screen), Playground page, four WebSocket panel components, JSON editor (CodeMirror 6), three Base-UI primitive wrappers (Switch, Select, ScrollArea).

**Conventions baked into the new examples**
- NFO weekly expiries fall on Tuesday — sample expiry is `12MAY26` across analytics endpoints; VolSurface uses `[12MAY26, 19MAY26]` (consecutive Tuesdays).
- BFO weekly expiries fall on Thursday — call out separately when adding SENSEX / BANKEX examples.
- Index examples use `NIFTY` / `BANKNIFTY` on `NSE_INDEX`.
- Cash equity examples use `RELIANCE` / `NHPC` / `TCS` / `INFY` / `SBIN` / `AXISBANK` on `NSE`.
- For equities, default to `CNC` (delivery) — `MIS` is intraday and inappropriate as a default in docs.
- Lot sizes are sourced from the broker's master contract via `backend/services/symbol_service.py::get_option_underlyings`, not hardcoded.

---

## 2026-05-11 — Code-vs-Docs Audit Pass

**Scope**: full validation of all API endpoint docs, SERVICES.md line numbers, and supporting design docs against current code in `backend/`. Four parallel audit agents; every drift verified against code before fix.

**HIGH-severity drifts fixed**
- `api/order-management/cancelallorder.md` — rewrote: `strategy` field is accepted-but-ignored, not a filter; response shape corrected to `{status, data: {canceled[], failed[]}}`.
- `api/order-management/closeposition.md` — rewrote: same `strategy` clarification; response shape now matches broker-plugin return.
- `api/order-information/openposition.md` — rewrote: response carries only `{data: {quantity}}`; documented that brokers can't filter positions by strategy.
- `api/symbol-services/symbol.md` — rewrote: corrected response field list (`brsymbol`/`brexchange`/`instrumenttype`; `expiry` is `DD-MMM-YY`, not `YYYY-MM-DD`).

**MEDIUM-severity drifts fixed**
- `api/options-services/optionsymbol.md` — rewrote: flat response (no `data` wrapper); doesn't echo `underlying`/`offset`/`option_type`.
- `api/options-services/optionchain.md` — corrected default `strike_count` to `10` (was claimed "all"); documented `"all"` opt-in.
- `api/symbol-services/search.md` — documented optional `exchange` filter; corrected response to 11 fields per row.
- `api/order-management/optionsorder.md` — added split-response shape; corrected ATM resolution note (uses spot LTP, not synthetic future).
- `api/order-management/basketorder.md` — documented BUY-before-SELL ordering enforcement and concurrency model.
- `api/websocket-streaming/{ltp,quote,depth}.md` — full rewrites; previous version documented a wrong protocol (`instruments` key, missing authenticate step).

**SERVICES.md line-number drifts fixed** — 6 functions had stale file:line references after recent refactors:
- `place_basket_order` 79 → 106
- `calculate_greeks` 260 → 272
- `get_option_greeks` 338 → 350
- `get_max_pain_data` 63 → 66
- `get_strategy_snapshot` 97 → 100
- `get_strategy_chart_data` 67 → 98

**New documentation**
- `api/analytics-tools/{oitracker,maxpain,ivchart,ivsmile,volsurface,straddle,gex}.md` — 7 per-endpoint API docs for analytics endpoints that previously had only one-line summaries.
- `docs/design/RUNBOOK.md` — operations runbook (troubleshooting, monitoring, debugging, recovery).
- `docs/TUTORIAL.md` — getting-started walkthrough for new SDK integrators.
- `docs/CHANGELOG.md` — this file.

**Index / cross-link updates**
- `docs/README.md` — new "Analytics" row in REST endpoints table; runbook link added under Operators.
- `docs/api/README.md` — Analytics Tools table now links to individual docs; OpenAPI/Swagger UI discovery section added.

**False positives ignored** (reported by one audit agent, refuted by source-reading)
- "Angel/Dhan/Fyers brokers don't exist" — verified: all five broker plugins ship with full `api/`, `mapping/`, `streaming/`, `database/`, `plugin.json`.
- "`placesmartorder` route doesn't exist" — verified: route at `backend/api/place_order.py:80`.

---

## 2026-05-08 — Initial Doc Refresh

**Scope**: top-level narrative docs (PRODUCT, README, ARCHITECTURE), supporting design docs, all API endpoint docs.

**Full rewrites**
- `docs/PRODUCT.md` — replaced stale "single-user / 31 endpoints / 2 brokers / 4 analytics" framing with current "multi-user / 35+ endpoints / 5 brokers / 8 analytics tools / Strategy Builder + Portfolio / Sandbox simulator" capability surface. 12 sections covering capability matrix, user journeys, security model, deployment modes, differentiators, in-flight roadmap.
- `docs/design/symbol-format.md` — stripped broken gitbook syntax (`{% embed %}`, `{% columns %}`), refreshed 2024 → 2026 examples, removed dead OpenAlgo "Agent Instructions" footer + broken image link, added `MCX_INDEX` + `NCDEX`.
- `docs/design/order-constants.md` — added index exchanges (`*_INDEX` codes flagged as read-only), validation source pointer to `backend/utils/constants.py`.

**Surgical updates**
- `README.md` — backend tree now lists `strategy/`, `events/`, `subscribers/`; models list adds `audit.py`/`strategy_module.py`; endpoint count corrected to 35+; Documentation section points at new `docs/README.md` index.
- `docs/design/ARCHITECTURE.md` — directory tree now shows all 5 brokers + new dirs + new models + new strategy_module router; fixed template count (14 → 30); CDS squareoff time (17:00 → 16:45); added Event Bus & Audit Trail and Strategy Module (in-flight) sections.
- `docs/design/SERVICES.md` — TOC adds `MultiStrikeOI`; caveat distinguishing in-flight Strategy Module from existing Strategy Builder.
- `docs/design/broker-integration.md` — fixed wrong file reference (`proxy_server.py` → `server.py`, `_BROKER_ADAPTERS` map → `_create_adapter()` factory).

**New documentation**
- `docs/README.md` — audience-grouped index hub (Start here / Developers / Integrators / Operators / In-flight) with REST endpoint catalogue and doc-update conventions.

**Sweeps**
- Exchange lists across 10 endpoint docs — added `NCDEX` to order endpoints, added `NSE_INDEX`/`BSE_INDEX`/`MCX_INDEX` to market-data + symbol endpoints.
- NIFTY `lotsize` examples 65 → 75 across 3 docs (current NSE F&O lot size).

---

## How to update this changelog

When you edit a doc:

1. Determine severity:
   - **HIGH** — doc claimed something the code never did / didn't do anymore (would mislead an integrator).
   - **MEDIUM** — drift that doesn't break clients but misleads readers.
   - **LOW** — cosmetic, formatting, or example refresh.
2. Add an entry under the most recent dated section. If the last dated section is more than 7 days old, start a new dated section at the top.
3. Group by HIGH/MEDIUM/NEW/SWEEP. Keep entries one-line where possible.
4. Don't track LOW-severity entries here — `git log` is sufficient for those.

When you create a doc:

1. Add the doc to `docs/README.md` under the right audience section.
2. Add a one-line entry to this changelog under the current dated section.
3. If the doc fills a previously-listed gap area (see "Gaps" section of the most recent freshness audit), note that.
