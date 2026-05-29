# OpenBull ↔ OpenAlgo Parity Audit

**Date:** 2026-05-29
**Author:** rajandran (audit run via Claude Code)
**Target repo:** `D:\FullStack Options\Day21\openbull`
**Reference repo:** `D:\FullStack Options\Day21\openalgo`
**Tracker:** [`openalgo-parity-tracker.csv`](./openalgo-parity-tracker.csv)

---

## 1. Purpose

OpenAlgo has moved ahead of OpenBull in several areas the user flagged:

1. `/positionbook` (Positions) is **real-time** in OpenAlgo, **not** in OpenBull.
2. `/holdings` is **real-time** in OpenAlgo, **not** in OpenBull.
3. Broker-level features changed across the **5 shared brokers** (Angel, Dhan, Fyers,
   Upstox, Zerodha) — websocket refactors, Dhan holdings, GTT, etc.
4. Option Greeks: OpenAlgo uses **`opengreeks`**; OpenBull does not.

This report records what needs to change and feeds the CSV tracker. OpenBull supports
only the 5 brokers above (OpenAlgo ships 30+), so the broker audit is scoped to those 5.

> **Confidence note.** File/line references come from an automated cross-repo sweep
> (read-only sub-agents). Treat exact line numbers as *pointers to verify*, not gospel —
> confirm at the file before editing. Findings flagged **"OpenBull already ahead"** are
> places where OpenBull is the *more correct* implementation and need **no action**.

---

## 2. Executive summary

| Area | Verdict | Headline gap |
|------|---------|--------------|
| Realtime Positions | **Gap (High)** | No `useLivePrice` hook; page is REST-poll only |
| Realtime Holdings | **Gap (High)** | Same — no WS-driven LTP/P&L recompute |
| Dhan holdings | **Gap (High)** | `map_portfolio_data` is pass-through → stale P&L |
| Option Greeks (opengreeks) | **Gap (High)** | OpenBull uses hand-rolled Black-76, not `opengreeks` |
| Multi-Greeks batch | **Gap (Med)** | No `get_multi_option_greeks` in OpenBull |
| Upstox streaming resilience | **Gap (High)** | No stall/health detection (90s silent-stall) |
| Zerodha GTT | **Gap (Med)** | No `gtt_api.py` / `gtt_data.py` |
| Zerodha streaming | **Gap (Med)** | Single-file adapter; missing auth-fail short-circuit + incremental subscribe |
| Upstox data feeds | **Gap (Med)** | No `GLOBAL_INDICATOR` LTP handling |
| Dhan / GTT / misc | Mixed | See tracker |
| Fyers master-contract & data | **OpenBull ahead** | OpenBull already fixed name/expiry/index/429 — no action |
| Angel architecture | **OpenBull ahead / parity** | Shared cache, async DB, health checks already present |

**Correction to a stated premise.** OpenBull's `backend/services/option_greeks_service.py`
does **not** import `py_vollib` — it is a **hand-rolled pure-math Black-76** (the
`py_vollib` mentions are only in comments/docstrings). So the real parity move is
"replace the pure-math implementation with `opengreeks`", not "remove py_vollib".

---

## 3. Findings by category

### 3.1 Real-time Positions & Holdings (RT-*)

**How OpenAlgo does it.** The pages render `enhancedPositions` / `enhancedHoldings`
produced by the `useLivePrice` hook (`frontend/src/hooks/useLivePrice.ts`). That hook:

- subscribes to each row's symbol via `useMarketData` (WebSocket, LTP mode);
- on every tick recomputes **LTP, P&L and P&L%** client-side
  (`(ltp − avgPrice) × qty` + realized);
- falls back to **MultiQuotes REST polling (~30 s)** when the socket is down;
- **pauses** subscriptions when the tab is hidden (`usePageVisibility`);
- shows a **Live / Paused** badge.

**OpenBull today.** `Positions.tsx` / `Holdings.tsx` use `useQuery` REST polling only
(15 s / 30 s). The streaming primitives already exist in OpenBull
(`useMarketData.ts`, `usePageVisibility.ts`, `useOptionChainLive.ts` — used by the
option chain) but there is **no `useLivePrice` hook** and the portfolio pages never
subscribe. Data is stale between polls; no client-side P&L recompute.

**Fix.** Port `useLivePrice.ts` (and the small `useLiveQuote.ts`) from OpenAlgo, then
wire `enhancedPositions` / `enhancedHoldings` into the two pages, add the Live badge,
and the MultiQuotes fallback. Items **RT-01 … RT-06**.

### 3.2 Dhan holdings (DHAN-*)

OpenAlgo's `broker/dhan/mapping/order_data.py:map_portfolio_data` batch-fetches LTP via
multiquotes, resolves the real exchange (NSE/BSE) from `securityId`, and enriches each
row (`_oa_symbol`, `_exchange`, `_ltp`) so `calculate_portfolio_statistics` and
`transform_holdings_data` report **live** holding value and P&L.

OpenBull's equivalent is a **pass-through** — it computes value/P&L off `avgCostPrice`,
so holdings show **P&L ≈ 0 / stale value** until the frontend fills quotes in. This is
the "Dhan holdings" issue. Also missing: `test_auth_token` (token validation via
`/v2/fundlimit`). Items **DHAN-01 … DHAN-04**.

### 3.3 Option Greeks → opengreeks (GRK-*)

- **GRK-01 (High):** Replace OpenBull's pure-math Black-76 in
  `backend/services/option_greeks_service.py` with `opengreeks.black76`
  (Rust core, NumPy-only dep, bit-identical to py_vollib, ~50–180× faster). Mirror
  OpenAlgo: lazy import, `check_opengreeks_availability()`, add `opengreeks` to
  `requirements`. **Watch the unit conventions** — OpenAlgo notes opengreeks Black-76
  returns daily theta and per-1% vega already (no extra `/365` or `/100`), whereas
  OpenBull's hand-rolled code divides theta by 365 and vega by 100. Verify before the
  swap so numbers don't shift.
- **GRK-02 (Med):** Port `get_multi_option_greeks` (batch: one spot fetch per
  underlying + `get_multiquotes` for options) — absent in OpenBull.
- **GRK-03 (Low):** Crypto-exchange branch in `get_underlying_exchange` (only if
  OpenBull targets crypto).
- **GRK-04 (Med):** `iv_chart_service.py` (the `/tools/greeks` "Option Greeks
  (Historical)" tool) imports the **internal** helpers `_implied_vol` / `_greeks`
  directly. Easiest path: keep those two helpers as thin `opengreeks`-backed wrappers so
  this file (and any other internal consumer) needs no change.

**Scope is small because `/tools` is centralized.** Every Greek/IV tool —
Strategy Builder, GEX, IV Smile, Vol Surface, snapshot Greeks, Straddles/Strangles
chain — delegates to `option_greeks_service.calculate_greeks` /
`parse_option_symbol`; the **only** file with real Black-76 math is
`option_greeks_service.py`. So migrating that one engine (plus the two internal helpers
for GRK-04) upgrades the entire `/tools` suite at once, *provided the public return
shape and units (`theta/365`, `vega/100`) are preserved.*

### 3.4 Streaming resilience & refactor (STR-*)

- **STR-01 (High) Upstox:** OpenAlgo's `upstox_client.py` has 90 s silent-stall
  detection (health check every 30 s) + a reconnect budget. OpenBull's
  `upstox_adapter.py` reconnects but has **no stall detection** — a silent feed never
  recovers. Port the health-check loop.
- **STR-02 (Med) Zerodha:** OpenAlgo splits streaming into
  `zerodha_adapter` + `zerodha_websocket` + `zerodha_mapping` with an **auth-failure
  short-circuit** and **incremental batch subscription**. OpenBull has a single
  `zerodha_adapter.py` without those. Port the resilience behaviors (the file split
  itself is optional/maintainability).
- **No action:** Angel and Fyers OpenBull adapters **already** implement health-check /
  data-stall detection — parity or ahead.

### 3.5 GTT / Forever orders (GTT-*)

- **GTT-01 (Med) Zerodha:** Port `gtt_api.py` + `gtt_data.py` (SINGLE/OCO, market-price
  protection). Absent in OpenBull.
- **GTT-02 (Low) Dhan:** Port Dhan Forever Order (GTT) module. Absent in OpenBull.

(Scope-dependent — only if GTT is on the OpenBull roadmap.)

### 3.6 Market-data parity (DATA-*)

- **DATA-01 (Med) Upstox:** No `GLOBAL_INDICATOR` LTP path for feed symbols
  (USDINR / BRENTOIL / WTIOIL) — these will fail in OpenBull's `data.py`.
- **DATA-02 (Low) Zerodha:** History `oi` not cast to int (cosmetic/typing).

---

## 4. Where OpenBull is already ahead (no action)

These came up in the sweep but are **not** gaps — OpenBull is the better implementation:

- **Fyers master contract:** OpenBull sets `name = Underlying symbol` for F&O (fixes the
  "Symbol Details" underlying-picker explosion), converts epoch expiry → `DD-MMM-YY`,
  and normalizes NSE/BSE index symbols. OpenAlgo retains the older behavior.
- **Fyers data layer:** OpenBull has 429-retry, full `get_market_depth` with OI, and an
  OI-bucket threshold policy.
- **Angel / cross-broker:** OpenBull uses a shared symbol cache and async master-contract
  download (avoids event-loop conflicts); health checks already present.
- **Plugin manifests:** OpenBull's `plugin.json` carries `oauth_type` /
  `auth_url_template` for the broker-select UI.

Keep these — do **not** "sync back" to OpenAlgo's older versions.

---

## 5. Recommended sequencing

1. **RT-03 → RT-01/RT-02** (port `useLivePrice`, wire both pages) — biggest user-visible win.
2. **DHAN-01/02** (live holdings P&L) — pairs naturally with the realtime work.
3. **GRK-01** (opengreeks swap) — isolated, low-risk after the unit-convention check.
4. **STR-01** (Upstox stall detection) — reliability.
5. **GRK-02, DATA-01, STR-02** — medium.
6. **GTT-01/02, RT-04/05/06, DHAN-03/04, DATA-02, GRK-03** — as scoped.

---

## 6. Deliverables

- This report: `docs/audit/openalgo-parity-audit.md`
- Issue tracker: `docs/audit/openalgo-parity-tracker.csv` (import into Sheets/Excel;
  columns: ID, Category, Broker, Area, Title, Severity, Status, OpenAlgo Behavior,
  OpenBull Current, Suggested Fix, Reference Files).
