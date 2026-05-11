# Strategy Module — Implementation Plan

**Status:** v1 shipped — all 10 build-order phases on `main`
**Owner:** rajandran
**Last updated:** 2026-05-11

---

## 1. Purpose

Build a brand-new `/strategy` module in OpenBull for managing multi-leg options strategies with end-to-end risk management. Reference UX: AlgoTest "New Strategy" wizard. Trigger surfaces: in-app UI, scheduler (cron), and TradingView webhook. Execution surfaces: live broker order flow and sandbox (paper) mode. Live runtime is observable over WebSocket; everything else is REST.

This is greenfield — it must not modify the existing `strategy_builder_service.py`, `strategy_chart_service.py`, `routers/strategybuilder.py`, or `routers/strategies.py`. Those continue to serve the legacy strategy-builder feature.

---

## 2. Scope

### In scope (v1)

- Strategy CRUD with leg builder (Options, Futures, Cash segments) across NSE / BSE / MCX
- MCX commodities discovered dynamically from `symtoken` (no hardcoded list); each commodity's expiry cycle (monthly/quarterly/weekly) is resolved at run-start by reading available expiries from the master contract
- For MCX option legs, the "underlying" is always the nearest non-expired FUT contract of the same base symbol — already handled by the existing `option_symbol_service._find_near_month_futures`
- Strike resolution: ATM-by-strike (with offsets) AND direct strike picker
- Per-leg risk: Stop Loss, Target Profit, Trail SL (X-trigger / Y-step)
- Strategy-level risk: Overall SL, Overall Target, Lock-profit (Lock & Lock+Trail), Trail-SL-to-entry
- Run modes: live and sandbox, selectable per run
- Scheduler: cron-based start with weekday filter; default 09:15 Mon–Fri
- TradingView webhook: shared-secret JSON, start/stop actions, mode override per call
- Strategy-scoped orderbook, tradebook, positions
- Live MTM and per-leg state over WebSocket
- Crash-safe state awareness: DB-canonical, Redis-hot, broker-reconciled recovery

### Out of scope (v1)

- AlgoTest's "advanced strategy creation" (custom condition trees, multi-stage entries)
- Backtesting
- Crypto / Delta Exchange wiring (UI shell stays disabled)
- Multi-broker fan-out for a single strategy
- Strategy templates, marketplace, sharing
- Simple Momentum entry filter (UI stub only — not evaluated by engine in v1)

### Constraints / non-functional

- Must not modify legacy strategy code
- Must reuse existing `option_symbol_service` for ATM resolution
- Must reuse existing `sandbox_service` for sandbox order flow
- Must reuse existing ZMQ tick fan-out (`websocket_proxy`) for live LTP
- Must respect existing `VALID_EXCHANGES` whitelist (only tradable exchanges in legs)
- Tick → engine → action latency target: under 200 ms p95
- WS broadcast throttle: at most 10 messages/sec/strategy

---

## 3. UX outline (mirrors AlgoTest screenshots)

### 3.1 Routes

| Route | Purpose |
|---|---|
| `/strategy` | List view: cards/table of all user strategies |
| `/strategy/new` | Wizard for creating a strategy |
| `/strategy/:id` | Detail view (live + tabs for orders/trades/positions/etc.) |
| `/strategy/:id/edit` | Edit (only when stopped) |

The list view shows per-strategy summary with three explicit P&L columns — **Realized P&L**, **Unrealized P&L**, **Total P&L** (live for running strategies, last-run snapshot for stopped). Plus status badge, mode badge, broker, last started_at (IST), and quick-action buttons (Start / Stop / Close All) per row.

### 3.2 Wizard layout

**Top tabs (universe filter — drives downstream pickers):**
- Weekly & Monthly Expiries: NIFTY, SENSEX
- Monthly Only Expiry: MIDCPNIFTY, BANKNIFTY, FINNIFTY, BANKEX
- Stocks – Cash / F&O: NIFTY 500 stocks (queries `symtoken` for `instrumenttype IN ('EQ','FUT','CE','PE')` filtered by F&O availability)
- Commodities (MCX): list **dynamically discovered** from `symtoken` — no hardcoded commodity list. Source query:
  ```sql
  SELECT DISTINCT base_symbol(symbol) AS underlying
  FROM symtoken
  WHERE exchange = 'MCX'
    AND instrumenttype = 'FUT'
    AND expiry IS NOT NULL
    AND to_date(expiry, 'DD-Mon-YY') >= CURRENT_DATE
  ORDER BY underlying
  ```
  where `base_symbol()` strips the trailing `{DDMMMYY}FUT` from the OpenAlgo symbol. This auto-includes whatever the broker carries (CRUDEOIL, NATURALGAS, GOLD, SILVER, COPPER, ZINC, ALUMINIUM, NICKEL, LEAD, MENTHAOIL, COTTON, etc.) without any code change when MCX adds/removes contracts.
- Delta Exchange: BTCUSD, ETHUSD (disabled in v1, shows "coming soon" badge)

**Section A: Index and Timings**
- Index dropdown (searchable, filtered by tab)
- Strategy Type toggle: Intraday / Positional
  - Intraday → Entry Time (default 09:35), Exit Time (default 15:15)
  - Positional → info banner only

**Section B: Leg Builder (repeatable card)**
- Segment toggle: Futures / Options (Stocks tab adds Cash)
- Expiry select: choices vary by universe tab —
  - Weekly & Monthly tab: `Weekly` / `Monthly`
  - Monthly Only tab: `Monthly` only
  - Stocks tab: `Monthly` only
  - **MCX tab**: `Current` / `Next` — rank-based, since each commodity has its own cycle (CRUDEOIL is monthly, SILVER/GOLD have multi-month gaps, weekly options exist for some). Engine resolves these to a real `DDMMMYY` per commodity at run-start by sorting non-expired expiries from `symtoken`.
- Lots (int)
- Position toggle: B / S
- Option Type: Call / Put (only when segment=Options)
- Strike Mode toggle: ATM / Direct Strike
  - ATM → Strike Criteria dropdown (ATM, ITM1, ITM2, ITM3, OTM1, OTM2, OTM3)
  - Direct Strike → searchable strike picker (queries `/api/v1/strategy/strikes`)
- Per-leg risk:
  - Target Profit (collapsed by default, "+" expands; pts input)
  - Stop Loss (pts input)
  - Trail SL: X (trigger move) / Y (trail step), both in pts
  - Simple Momentum (collapsed, stub for v1)

**Section C: Overall Strategy Settings**
- Overall SL (MTM ₹)
- Overall Target (MTM ₹)
- Lock Profit toggle with two modes:
  - **Lock**: static floor — when MTM ≥ if_profit_reaches, fix floor at lock_profit; exit if MTM ≤ floor
  - **Lock and Trail**: same trigger; floor ratchets up as MTM ≥ peak (peak − trail_step)
- Trail SL to entry price toggle (description: when any leg's SL fires, every other open leg's effective SL → its entry_avg; Overall SL is bypassed)

**Section D: Scheduler & Webhook (new — not in AlgoTest screenshots)**
- Scheduler: weekday checkboxes (default Mon–Fri), Start Time (default 09:15), optional Auto-Stop Time
- Webhook URL preview (read-only, copy button)
- Webhook secret (read-only, rotate button)
- Curl example block

**Footer:** "Save and Continue" → POSTs config, then redirects to `/strategy/:id`.

### 3.3 Detail page tabs

Implementation patterns mirror the existing `/websocket/test` page (`frontend/src/pages/WebSocketTest.tsx`) for the WS handling — same `WsStatus` state machine (`idle | connecting | connected | authenticating | authenticated | error | closed`), same Card+Badge+colored-dot status header, same auth flow (`{action: "authenticate", api_key}` after socket open), green/red coloring conventions for P&L. Add an **exponential-backoff auto-reconnect** the test page doesn't have (1s, 2s, 4s, … capped at 30s), since strategy runs are long-lived.

Strategy-scoped REST endpoints share envelopes with the global ones (Section 6.3), so we **reuse the existing OrderbookTable, TradebookTable, PositionsTable components** unchanged — only the data source URL differs.

- **Live** — single page, three regions:
  - **Header card**: status badge (running/paused/stopped/errored), mode badge (live/sandbox), broker badge, control buttons:
    - **Start** (when stopped) — opens a small dialog asking for `mode: live | sandbox`
    - **Pause** (when running, future v2 — disabled in v1)
    - **Close All** — strategy-level square-off; closes every open leg at MARKET, marks the run stopped with `stop_reason="manual"`. Confirmation modal before firing.
    - **Stop** — same as Close All in v1; alias for clarity in the UI when there are no open legs
    - `tick_source_degraded` warning chip when active
  - **Strategy panel**: three large numbers side-by-side — **Realized P&L** (locked-in from closed legs), **Unrealized P&L** (floating from open legs), **Total P&L** (the sum, evaluated against Overall SL/Target). Below them: peak, trough, distance to Overall SL, distance to Overall Target, lock-profit state (armed? current floor? mode badge), trail-to-entry indicator. Updates from WS `delta` messages.
  - **Legs table**: one row per leg. Columns: leg #, symbol, position (B/S), qty, entry avg, LTP, leg MTM (live), **effective SL** (with arrow showing how far it has trailed from configured SL), **effective target**, **trail status** (`armed @ favorable_peak`), distance to SL/target in pts, `tick_source` chip per leg (`ws`/`polling`/`stale`), and a **Close** button per row.
    - The per-row **Close** button calls `POST /api/v1/strategy/{id}/legs/{leg_id}/close` to exit just that leg at MARKET. Disabled when leg `status` is `closed`, `pending`, or `errored`. Confirmation modal before firing.
    - Closing a single leg does NOT stop the strategy — the run continues with the remaining open legs. The closed leg's P&L moves from unrealized → realized; remaining legs keep evaluating SL/target normally. If `trail_sl_to_entry=true`, manually closing a leg does NOT trigger the trail-to-entry rule (only an SL-driven exit does — manual closes are operator overrides, not signals).
  - **Events panel** (sidebar / footer): scrolling feed of every `event` WS message — IST timestamp, severity-colored row, `kind` badge (`leg_sl_hit`, `lock_profit_triggered`, `leg_close_manual`, `close_all_manual`, …), human message, expandable to show raw `payload`. Same conventions as the test page's frame log.
- **Orders** — `OrderbookTable` (existing component) backed by `/api/v1/strategy/{id}/orderbook`. Run-id filter dropdown.
- **Trades** — `TradebookTable` (existing component) backed by `/api/v1/strategy/{id}/tradebook`.
- **Positions** — `PositionsTable` (existing component) backed by `/api/v1/strategy/{id}/positions` plus a strategy-aggregate MTM card on top.
- **Events** — full audit log table backed by `/api/v1/strategy/{id}/events`. Columns: **Time (IST)**, Run, Severity, Kind, Leg, Message, Payload (expandable). Filterable by `kind`, `severity`, `run_id`, time range. Every row corresponds to one `strategy_event` DB row — this is the canonical "what happened, when" record.
- **Risk** — read-only summary card of all configured SL/TP/Lock/Trail settings.
- **Webhook** — URL preview, secret reveal+rotate, curl example, table of recent `webhook_event` rows (received_at in IST, action, mode, result).
- **History** — `strategy_run` rows with started_at/stopped_at (IST), stop_reason, mode, broker, realized P&L; row click drills into that run's Orders/Trades/Events filtered to `run_id=`.

Every timestamp on every tab renders in IST with the `IST` suffix. Frontend never receives or displays a UTC value — the API/WS already converts (Section 4.4).

---

## 4. Data model

### 4.1 PostgreSQL (canonical, durable)

#### `strategy`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | int FK | |
| name | text | |
| universe_tab | text | UI hint: `weekly_monthly` / `monthly_only` / `stocks_fno` / `mcx` / `delta` |
| underlying | text | e.g. `NIFTY` |
| underlying_exchange | text | e.g. `NSE_INDEX` |
| strategy_type | text | `intraday` / `positional` |
| entry_time | time | nullable; intraday only |
| exit_time | time | nullable; intraday only |
| product | text | NRML / MIS / CNC — strategy-level default; v1 has no per-leg override |
| pricetype | text | MARKET / LIMIT — strategy-level default for entry orders; per-leg override available at exit time only |
| legs | jsonb | array (see 4.1.1) — each leg owns its own `expiry` (`weekly`/`monthly`/`current`/`next`), so there is no strategy-level expiry_kind |
| overall_sl_mtm | numeric | nullable; evaluated against `mtm_total` (realized + unrealized) |
| overall_target_mtm | numeric | nullable; evaluated against `mtm_total` |
| lock_profit | jsonb | nullable: `{mode, if_profit_reaches, lock_profit, trail_step}` |
| trail_sl_to_entry | bool | default false |
| scheduler | jsonb | `{enabled, days, start_time, auto_stop_time, default_mode}` — `default_mode` ∈ `live`/`sandbox` is what the scheduler-triggered run starts in |
| live_enabled | bool | default false; explicit per-strategy opt-in for `mode=live` runs (Section 14.3). Flipping requires re-auth |
| webhook_token_hash | text unique indexed | **SHA-256** of the webhook token (token = the per-strategy secret slug embedded in the webhook URL). Plaintext shown **once** on create/rotate; never readable thereafter. SHA-256 (not argon2) because the token has 256+ bits of entropy by construction — slow KDFs only matter for low-entropy secrets like passwords |
| webhook_ip_allowlist | jsonb | nullable; CIDR list. Empty/null = any IP (Section 14.4) |
| daily_loss_limit_inr | numeric | nullable; per-strategy cap. User-level cap lives on the user table (out of scope here) |
| status | text | `stopped` / `running` / `paused` / `errored` |
| current_run_id | uuid | nullable; set when running |
| created_at | timestamptz | UTC stored; rendered IST |
| updated_at | timestamptz | UTC stored; rendered IST |

##### 4.1.1 Leg jsonb schema

```jsonc
{
  "id": 1,
  "segment": "options" | "futures" | "cash",
  "expiry": "weekly" | "monthly" | "current" | "next",   // current/next used for MCX
  "lots": 1,
  "position": "B" | "S",
  "option_type": "CE" | "PE",          // segment=options only
  "strike_mode": "atm" | "strike",     // segment=options only
  "atm_offset": "ATM" | "ATM+1" | "ITM2" | "OTM3" | ...,   // when strike_mode=atm
  "strike_value": 24000,               // when strike_mode=strike
  "target_pts": null,
  "sl_pts": null,
  "trail": {"x": 0, "y": 0},           // trigger move / trail step in pts
  "momentum": null                     // v1 stub, evaluator reserved
}
```

#### `strategy_run`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| strategy_id | uuid FK | |
| mode | text | `live` / `sandbox` |
| broker | text | snapshotted at start time |
| started_at | timestamptz | |
| stopped_at | timestamptz | nullable |
| stop_reason | text | `manual` / `scheduler` / `overall_sl` / `overall_target` / `lock_profit` / `eod` / `expiry` / `daily_loss_limit` / `tick_stale` / `recovery_failed` / `error` |
| pnl_realized | numeric | **final** realized P&L for the run (set on stop). All legs closed by then so unrealized is always 0; only the realized total is persisted. Live `pnl_realized` and `pnl_unrealized` while running come from Redis state and `strategy_run_checkpoint`, not this column. |
| pnl_peak | numeric | session high of `mtm_total` |
| pnl_trough | numeric | session low of `mtm_total` |
| trigger_source | text | `manual` / `webhook` / `scheduler` |
| webhook_event_id | uuid | nullable, FK to `webhook_event` |

#### `strategy_order`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_id | uuid FK | |
| leg_id | int | which leg in the strategy this order belongs to |
| kind | text | `entry` / `exit_sl` / `exit_target` / `exit_trail` / `exit_overall_sl` / `exit_overall_target` / `exit_lock_profit` / `exit_eod` / `exit_expiry` / `exit_daily_loss_limit` / `exit_close_all` / `exit_leg_manual` / `exit_recovery` |
| broker_order_id | text | broker reference; sandbox uses a synthetic id |
| symbol | text | OpenAlgo standard symbol |
| exchange | text | |
| action | text | BUY / SELL |
| qty | int | |
| pricetype | text | |
| price | numeric | |
| trigger_price | numeric | |
| status | text | `pending` / `open` / `complete` / `cancelled` / `rejected` |
| placed_at | timestamptz | |
| filled_at | timestamptz | nullable |
| avg_fill_price | numeric | nullable |
| filled_qty | int | nullable |
| reject_reason | text | nullable |

#### `strategy_run_checkpoint`
Lightweight snapshot for crash recovery; written every ~30s while running.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| run_id | uuid FK | |
| ts | timestamptz | UTC stored; rendered IST at API |
| pnl_realized | numeric | locked-in P&L from closed legs at this checkpoint |
| pnl_unrealized | numeric | floating P&L from open legs at this checkpoint |
| pnl_total | numeric | `pnl_realized + pnl_unrealized` (denormalized for fast chart queries) |
| pnl_peak | numeric | session high of `pnl_total` |
| pnl_trough | numeric | session low of `pnl_total` |
| lock_floor | numeric | nullable |
| trail_to_entry_active | bool | |
| leg_state | jsonb | per-leg `{id, status, entry_avg, qty_filled, effective_sl, effective_target, ltp, mtm}` |

#### `webhook_event`
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| strategy_id | uuid FK | nullable if the URL token didn't match any strategy |
| action | text | `start` / `stop` / `unknown` |
| mode | text | nullable; provided for `start` |
| payload | jsonb | request body — token never appears here (token is in URL, redacted before save) |
| ip | inet | |
| user_agent | text | |
| received_at | timestamptz | UTC stored; rendered in IST at API |
| result | text | `ok` / `rejected_token` / `rejected_ip` / `rate_limited` / `rejected_dedupe` / `rejected_cooling_off` / `rejected_invalid_action` / `rejected_live_disabled` / `rejected_engine_error` |
| error | text | nullable |

#### `strategy_event` — risk-event audit trail

Every state-changing event the engine produces is persisted here AND broadcast over WS. This is the canonical "what happened, when, why" log shown in the Live tab and the dedicated Events tab.

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_id | uuid FK | |
| strategy_id | uuid FK | denormalized for query convenience |
| user_id | int | denormalized for forensic queries that don't join `strategy` (Section 14.6) |
| ts | timestamptz | UTC stored; **always rendered in IST at API/WS layer** |
| kind | text | enum, see below |
| severity | text | `info` / `warn` / `critical` |
| leg_id | int | nullable — present for leg-scoped events |
| message | text | human-readable: "SL hit on leg 1 (NIFTY28MAY2624000CE) at ₹78.50, exiting at MARKET" |
| payload | jsonb | structured details: prices, pts moved, mtm at the time, etc. |

**`kind` enum:**
- Lifecycle: `run_started`, `run_paused`, `run_resumed`, `run_stopped`, `close_all_manual`
- Entry/exit: `leg_entry_placed`, `leg_entry_filled`, `leg_entry_rejected`, `leg_exit_placed`, `leg_exit_filled`, `leg_exit_rejected`, `leg_close_manual`
- Per-leg risk: `leg_sl_hit`, `leg_target_hit`, `leg_trail_armed`, `leg_trail_advanced`
- Strategy risk: `overall_sl_hit`, `overall_target_hit`, `lock_profit_armed`, `lock_profit_floor_advanced`, `lock_profit_triggered`, `trail_to_entry_activated`, `eod_squareoff`, `expiry_squareoff`
- Source events: `tick_source_switched_to_polling`, `tick_source_switched_to_ws`, `tick_source_stale`
- Operational: `recovery_succeeded`, `recovery_failed`

Indexed on `(strategy_id, ts DESC)` for fast Events-tab queries.

### 4.2 Redis (hot, derivable)

| Key | Purpose | TTL |
|---|---|---|
| `strategy:run:{run_id}:state` | live mutable state: `{pnl_realized, pnl_unrealized, pnl_total, pnl_peak, pnl_trough, lock_armed, lock_floor, trail_to_entry_active, legs: {leg_id: {ltp, entry_avg, qty, status, effective_sl, effective_target, trail_active, favorable_peak, mtm, tick_source}}}` | none (cleared on stop) |
| `strategy:run:{run_id}:lock` | engine ownership lock (single worker per run) | 30s; renewed each tick |
| `strategy:webhook:dedupe:{strategy_id}:{action}` | idempotency — drop duplicates | 60s |
| `strategy:tickfeed:{symbol}:{exchange}` | latest LTP + source + last_seen_ts (used by RiskTickFeed; see Section 5.6) | 60s |
| `strategy:tickfeed:source:{symbol}:{exchange}` | current source: `ws` / `polling` / `stale` | 60s |
| `strategy:tickfeed:poll_due:{symbol}:{exchange}` | scheduling flag for next poll cycle | 5s |
| `strategy:user:{user_id}:running` | set of run_ids belonging to a user | none |

**Never in Redis:** strategy config, leg definitions, order audit trail, run history. Those are PG-only.

### 4.3 Source-of-truth split — quick rule

> If losing it would create regulatory or financial pain → PostgreSQL.
> If losing it just means "rebuild on next tick or next broker query" → Redis.

### 4.4 Time and timezone — IST everywhere on the surface

**Storage** (PostgreSQL): every timestamp column is `timestamptz`. PG stores UTC internally but is timezone-aware — never use naive `timestamp` without tz. This is the right way and is invisible to anyone querying.

**Transport** (REST + WebSocket): every timestamp field in API responses and WS payloads is rendered in IST as ISO 8601 with explicit `+05:30` offset, e.g. `"2026-05-08T16:25:42.123+05:30"`. This is unambiguous, parseable by `new Date(...)` in browsers and `datetime.fromisoformat(...)` in Python.

For high-frequency tick deltas where bytes matter, payloads also include `ts_ms_utc` (Unix epoch milliseconds, UTC) for client-side ordering and math. The display field stays as the IST ISO string.

**Cron / scheduler**: APScheduler is initialized with `timezone="Asia/Kolkata"`. All cron triggers (default 09:15) interpret times as IST. Stored `entry_time` / `exit_time` / `scheduler.start_time` are bare time-of-day strings (`"09:15"`) interpreted as IST.

**Logs** (server-side): standard format already configured. We additionally include `[IST 16:25:42.123]` in human-readable log messages for risk-event lines so operators reading `openbull.log` don't have to convert mentally.

**Frontend display**: every timestamp is rendered using
```javascript
new Date(istIso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })
```
with a trailing `IST` suffix on time-only fields (e.g. `16:25:42 IST`). Times never appear without the timezone label — prevents copy/paste confusion in screenshots, audit logs, screencasts.

**Holiday calendar / NSE market hours**: out of scope for v1. Scheduler fires on whatever weekdays the user picks; it doesn't know about NSE holidays. v2 concern.

---

## 5. Backend architecture

### 5.1 Package layout

```
backend/
  strategy/                      # all new
    __init__.py
    models.py                    # SQLAlchemy ORM
    schemas.py                   # Pydantic (request/response)
    repository.py                # DB ops (CRUD)
    symbol_resolver.py           # ATM (delegates to option_symbol_service) + direct-strike resolver
    engine.py                    # tick loop, risk evaluator, order dispatcher
    state.py                     # Redis-backed runtime state read/write
    checkpoint.py                # periodic DB checkpoint writer
    recovery.py                  # boot-time recovery: reconcile from DB + broker
    scheduler.py                 # APScheduler cron wiring
    webhook_handler.py           # TV webhook validator + dispatcher
    sandbox_router.py            # routes orders to sandbox_service when mode=sandbox
    constants.py                 # leg/strategy enums, defaults
  routers/
    strategy.py                  # REST API (auth required)
    strategy_webhook.py          # POST /webhook/strategy/{id} (no API key; secret in body)
  websocket/
    strategy_ws.py               # WS /ws/strategy/{id}
```

### 5.2 Engine lifecycle

```
start_run(strategy_id, mode, source) →
  acquire Redis lock for the run
  resolve all legs (symbol_resolver) — fail fast if any leg can't resolve
  place all entry orders (BUY-before-SELL via existing options_multiorder pattern)
  poll broker for fills until each leg is filled or rejected
  initialize Redis state with leg entry_avg
  subscribe to ZMQ ticks for each leg's symbol
  start tick loop (one async task per run)
  start checkpoint loop (30s interval)
  return run_id

tick_loop(run_id):
  on each tick:
    update Redis leg.ltp, leg.mtm
    per-leg: SL hit? Target hit? Trail trigger? → enqueue exit order
    cross-cut: any leg SL fired AND trail_sl_to_entry → set effective_sl=entry_avg for all open legs
    aggregate strategy MTM
    update peak / trough
    Lock-profit:
      Lock mode: if MTM ≥ if_profit_reaches, set floor = lock_profit; exit if MTM ≤ floor
      Lock+Trail: same trigger, then floor = max(initial_lock_profit, peak − trail_step) on each new peak
    Overall SL / Target check (skipped if trail_sl_to_entry has fired)
    if any exit triggered → dispatch via order pipeline
    push WS update (throttled)

stop_run(run_id, reason):
  release tick subscription
  square off any open legs (limit retry, then market)
  finalize broker reconciliation
  write final pnl_realized to strategy_run
  release Redis lock, clear hot state (keep tick keys to TTL)
  cancel scheduler-bound exit-time jobs
  notify WS subscribers of terminal snapshot
```

### 5.3 Order dispatch unification

Single entry point: `engine._place_order(leg, kind, action, qty, mode)`.

- mode=`live` → `backend.broker.{name}.api.order_api.place_order_api(...)` (uses existing path)
- mode=`sandbox` → `backend.services.sandbox_service.place_sandbox_order(...)` (existing)

Both return a normalized `{order_id, status}`. Engine writes to `strategy_order` immediately on placement (DB-first), then updates Redis on fill confirmation.

### 5.4 Crash-safe recovery (state-awareness contract)

> If the backend is killed at any moment, on the next boot the engine reconstructs every running strategy without human intervention.

Recovery flow on FastAPI startup (`backend/strategy/recovery.py`):

```
1. SELECT * FROM strategy_run WHERE stopped_at IS NULL
2. For each run:
   a. Load strategy + legs
   b. SELECT * FROM strategy_order WHERE run_id=? — find all orders, in-flight + filled
   c. Call broker.get_orderbook + broker.get_positions — reconcile real fills
        (broker is the ultimate source of truth for fills)
   d. Update strategy_order rows where DB-recorded status drifted from broker truth
   e. Load latest strategy_run_checkpoint — restore peak / trough / lock_floor / trail_active
   f. Rebuild Redis state from DB+broker reconciliation + checkpoint
   g. Acquire Redis run lock; if already held by another worker → skip (someone else owns it)
   h. Re-subscribe to ZMQ ticks for each open leg
   i. Resume tick loop
3. If recovery fails for a run → mark stopped_at=NOW, stop_reason='recovery_failed', alert user via UI banner
```

**Worst-case data loss:** up to 30s of trail-floor advancement (between the last checkpoint and crash). The actual SL behavior is still safe because per-leg SL evaluation re-runs from the LTP on the next tick; we may briefly use a stale floor for Lock+Trail, but it's clamped by `peak − trail_step` which is recomputed from broker MTM, so it self-heals within a few ticks.

### 5.5 Event bus — decoupled side-effects (architecture principle)

The strategy module is **event-driven, never polling**. Every state change is published as a typed event; subscribers handle side-effects independently. The publisher doesn't know who's listening; new subscribers can be added without touching producers.

The bus implementation is ported from OpenAlgo's `utils/event_bus.py` — ~70 lines of stdlib (`threading.Lock` + `ThreadPoolExecutor`). Zero new dependencies. Non-blocking publish. Per-subscriber error isolation.

**Files (Phase 1 lands the foundation):**

```
backend/utils/event_bus.py         # EventBus class, singleton `bus`, base Event dataclass
backend/events/__init__.py         # public re-exports
backend/events/strategy_events.py  # typed events: StrategyCreated, LegSlHit, …
backend/subscribers/__init__.py    # register_all() — wires subscribers to topics
backend/subscribers/strategy_audit_subscriber.py
                                    # writes every event to sm_strategy_event
                                    # uses sync engine (sync_database_url),
                                    # same pattern as api_log_writer
```

**Topic naming:** `strategy.<kind>` where `<kind>` matches `sm_strategy_event.kind`. The audit subscriber derives the DB column from the topic, so a typo in a topic string can't silently drop events.

**Publish points (Phase 1 — config-layer):**
- `repository.create_strategy` → `StrategyCreatedEvent`
- `repository.update_strategy` → `StrategyUpdatedEvent`
- `repository.rotate_webhook_token` → `WebhookTokenRotatedEvent` (severity=warn)
- `StrategyDeletedEvent` is intentionally not persisted to `sm_strategy_event` because the FK cascade deletes the audit rows when the strategy is dropped — deletion is logged to app logs instead. Future enhancement: change the strategy_id FK to `ON DELETE SET NULL` and denormalize `strategy_name` into the event row to preserve deletion history.

**Publish points (Phase 4+):** every risk-event topic listed in Section 4.1 `sm_strategy_event.kind` enum is published from the engine when its rule fires.

**Future subscribers** (Phase 2 onwards) can be added without touching publishers:
- WebSocket fan-out (push to UI Live tab on every event)
- Telegram alerts (severity=critical events only)
- External webhook out (mirror events to user-configured URLs)
- Metrics / Prometheus

**No polling rule:**
- The engine does not poll broker for fills — it consumes broker WS / order-update events.
- The engine does not poll Redis for state changes — its tick subscriber is push-driven via the existing `MarketDataCache` priority pub-sub (Section 5.6).
- The frontend Live tab does not poll REST — it consumes the strategy WS push (Section 7).
- The audit subscriber writes synchronously on each published event — there's no background sweeper that scans for "new events" on a timer.
- REST polling exists exactly once: as the **fallback** in the tick feed when broker WS is unavailable. It's not the primary path.

### 5.6 Engine ownership / multi-worker safety

If multiple FastAPI workers run, only one should own a given run's tick loop. Implemented via Redis SETNX-based lock with a 30s TTL renewed each tick. If a worker dies mid-run, the lock expires and another worker picks it up on its next recovery sweep (run every 60s, or on demand).

### 5.7 Tick source — LTP-first, WebSocket-primary, REST-fallback

**Design principle**: LTP is sufficient for SL, target, trail, lock-profit, and overall MTM checks. Quotes (bid/ask) and depth are NOT on the risk-evaluation hot path. They're fetched on-demand only when a specific feature needs them (e.g. depth-aware exit pricing in v2). The resilience layer below is therefore LTP-only.

#### Single abstraction: `RiskTickFeed`

```
backend/strategy/tick_feed.py

class RiskTickFeed:
    Provides last-known LTP per (symbol, exchange) for every leg in a run.
    Hides whether the data came from WS (push) or REST polling (pull).

    Engine just calls feed.get_ltp(symbol, exchange) on each tick — never
    knows or cares which source delivered it.
```

State machine per symbol:

```
                      first WS tick within startup window
INITIAL ──subscribe──▶ WS_LIVE
   │                      │
   │ subscribe failed     │ no tick for stale_threshold (default 10s)
   │ or no tick            ▼
   ▼                    POLLING ◀──────┐
POLLING                    │           │
   ▲                       │ a WS tick │
   │ poll succeeds         │ arrives   │
   │                       ▼           │
   └────────────────── WS_LIVE ────────┘

ANY state ──both fail for 60s──▶ STALE → engine alerts user, optionally
                                          pauses run with stop_reason='tick_stale'
```

#### Primary: WebSocket via existing infrastructure

The platform already has `backend/websocket_proxy/server.py` that:
- maintains broker WS adapters (Upstox, Zerodha, Fyers, Dhan, Angel)
- publishes ticks on a ZMQ PUB socket bound to `127.0.0.1:{ZMQ_PORT}`
- supports `MODE_LTP` (cheapest), `MODE_QUOTE`, `MODE_DEPTH`

The strategy engine subscribes **in `MODE_LTP`** to all symbols across all running strategies — it's the lightest mode and gives us everything we need. Implementation:

1. On run start, the engine collects every leg's `(symbol, exchange)` and calls into the websocket_proxy's adapter to subscribe in LTP mode (reusing `BaseBrokerAdapter.subscribe` — no new broker code).
2. The engine spawns a single ZMQ SUB task per FastAPI process that consumes ticks for all running strategies, fans them out to per-run state in Redis, and triggers per-run risk evaluation.
3. The market data cache (`backend/services/market_data_cache.py`) already caches the latest LTP/quote/depth — the engine reads from there as well, so cold reads after a restart get the last known value before the first new tick arrives.

#### Fallback: REST polling

If WS is unavailable or LTP becomes stale (>10s old), the engine falls back to **REST polling via `quotes_service.get_multi_quotes_with_auth`** — already implemented and batched (one HTTP call covers all legs).

- Default poll interval: **2 seconds** (configurable per run; tighter feels nice but burns rate-limit budget — recall the Fyers 429 errors in past logs)
- All legs of a run that need polling are batched into one `/multiquotes` call per cycle
- On 429: exponential backoff to 5s, then 10s, capped at 30s. Engine logs and sets a `tick_source_degraded` flag.
- Polling is per-symbol, not per-run: the engine can have leg-1 on WS_LIVE and leg-2 on POLLING simultaneously (e.g. leg-2 is on an exchange that broker doesn't stream).
- When a WS tick arrives for a symbol that was polling, the symbol switches back to WS_LIVE and is removed from the next poll cycle.

#### Quotes / Depth — on-demand only

Not part of the hot path. If a future feature needs them:
- **Quotes** → `quotes_service.get_quotes_with_auth(symbol, exchange, ...)` per call
- **Depth** → `depth_service.get_depth_with_auth(symbol, exchange, ...)` per call
- Both already exist; both bypass the risk loop and don't gate tick processing

#### Visibility

Every WS broadcast to the client (Section 7) includes `tick_source: "ws" | "polling" | "stale"` per leg, so the user can see live in the UI when a leg is running on the fallback. A degraded tick source is not a stop reason on its own — only `tick_stale` (both sources failing for 60s+) triggers an automated halt.

#### Redis keys

See the `strategy:tickfeed:*` family in Section 4.2 — same keys, owned by RiskTickFeed.

#### Configuration knobs (env, with sane defaults)

```
STRATEGY_TICK_STALE_THRESHOLD_SEC=10     # WS_LIVE → POLLING after this long without a tick
STRATEGY_TICK_POLL_INTERVAL_SEC=2        # REST polling cadence
STRATEGY_TICK_STALE_FATAL_SEC=60         # both sources failing for this long → STALE/halt
STRATEGY_TICK_POLL_BATCH_MAX=50          # cap symbols per multiquotes call
```

---

## 6. REST API surface

All endpoints under `/api/v1/strategy/` require API key auth (existing `get_api_user` dependency). Webhook endpoint uses body-secret only.

### 6.1 CRUD

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/api/v1/strategy/` | Create | Body: full config; returns `{id, webhook_url, webhook_secret}` |
| GET | `/api/v1/strategy/` | List | Query: `status`, `mode`, `q` (name search) |
| GET | `/api/v1/strategy/{id}` | Detail | Returns config + current run snapshot if running |
| PATCH | `/api/v1/strategy/{id}` | Update | Allowed only when `status=stopped`; 409 otherwise |
| DELETE | `/api/v1/strategy/{id}` | Delete | Allowed only when `status=stopped`; 409 otherwise |

### 6.2 Lifecycle

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/api/v1/strategy/{id}/start` | Manual start | Body: `{mode: "live"\|"sandbox"}` |
| POST | `/api/v1/strategy/{id}/stop` | Manual stop — closes any open legs at MARKET, then sets `status=stopped`, `stop_reason=manual` | |
| POST | `/api/v1/strategy/{id}/close_all` | Strategy-level "Close All" — same effect as `stop`, named for UI clarity when legs are open | Same handler as `/stop`; emits `close_all_manual` event so the audit trail captures user intent |
| POST | `/api/v1/strategy/{id}/legs/{leg_id}/close` | **Close a single leg only — run keeps going** | Body optional: `{pricetype: "MARKET"\|"LIMIT", price?}` (default MARKET). Returns 409 if leg is not `open`. Records `strategy_order` with `kind=exit_leg_manual` and `strategy_event` with `kind=leg_close_manual`. Does NOT trigger `trail_sl_to_entry` (manual close is an operator override, not a signal) |

### 6.3 Strategy-scoped views

These endpoints return data in the **exact same response envelope** as the existing global counterparts in `backend/services/`:

- `/api/v1/strategy/{id}/orderbook` ↔ same shape as `/api/v1/orderbook` (`orderbook_service.get_orderbook_with_auth`) — fields, formatting, statistics block all identical, just filtered to this strategy
- `/api/v1/strategy/{id}/tradebook` ↔ same shape as `/api/v1/tradebook` (`tradebook_service.get_tradebook_with_auth`)
- `/api/v1/strategy/{id}/positions` ↔ same shape as `/api/v1/positions` (`positions_service.get_positions_with_auth`)

**How filtering works:** the strategy module's `repository.list_strategy_orders(strategy_id, run_id?)` reads `strategy_order` rows for the strategy/run, collects the set of `broker_order_id` values, then calls the existing global service and **post-filters** the broker response to that set. This gives:

1. Same field names and data types the frontend already knows how to render — existing orderbook/tradebook/positions components and tables can be reused without changes
2. Live data (broker is queried, not just the DB) — the filled qty / status / avg price reflect the broker's current truth, not whatever was last written to `strategy_order`
3. Strategy-level statistics (total orders, fills, P&L) computed by passing the filtered list back through `orderbook_service._format_statistics`

**Sandbox runs** route the same way but the underlying call goes to `sandbox_service` instead of the broker. Field names and shape stay identical so the UI is mode-agnostic.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/strategy/{id}/orderbook?run_id=` | Strategy orderbook (broker-canonical fields, filtered) |
| GET | `/api/v1/strategy/{id}/tradebook?run_id=` | Strategy tradebook (filled fills only) |
| GET | `/api/v1/strategy/{id}/positions` | Strategy net per-leg positions + aggregate MTM |
| GET | `/api/v1/strategy/{id}/events?run_id=&since=&kind=&limit=` | Risk-event audit trail (`strategy_event` rows, IST timestamps) |
| GET | `/api/v1/strategy/{id}/runs` | Run history |
| GET | `/api/v1/strategy/{id}/checkpoints?run_id=` | Checkpoint timeline (for charts) |

### 6.4 Helper endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/strategy/underlyings?exchange=` | List underlyings for a tab. For `MCX`, distinct base symbols of all non-expired FUT contracts (dynamic — no hardcode). For `NFO`/`BFO`, predefined index list + F&O-enabled stocks |
| GET | `/api/v1/strategy/strikes?underlying=&exchange=&expiry=&option_type=` | Strike picker source — returns sorted strikes from `symtoken` |

**Expiry list — reuse existing endpoint.** Don't build a strategy-specific expiry endpoint. The platform already exposes `POST /api/v1/expiry` (router: `backend/api/expiry.py`, service: `backend.services.market_data_service.get_expiry_dates`) which returns exactly the format the strategy wizard needs:

```json
{
  "status": "success",
  "data": ["18-MAY-26", "18-JUN-26", "20-JUL-26", "19-AUG-26", "21-SEP-26", "19-OCT-26"]
}
```

Request body: `{apikey, symbol, exchange, instrumenttype: "options"|"futures"}`. Sorted ascending. The frontend wizard's expiry dropdown calls this directly. Engine reuses the same service in-process via `get_expiry_dates(symbol, exchange, instrumenttype)`.
| GET | `/api/v1/strategy/{id}/webhook` | Reveal webhook URL + secret |
| POST | `/api/v1/strategy/{id}/webhook/rotate` | Rotate secret |

### 6.5 Webhook endpoint

```
POST /webhook/strategy/{strategy_id}
Headers: Content-Type: application/json
Body:
{
  "secret": "<webhook_secret>",
  "action": "start" | "stop",
  "mode": "live" | "sandbox"     // required when action=start
}

Response 200: {"status":"ok","action":"start","run_id":"..."}
Response 4xx: {"status":"error","message":"...","reason":"..."}
```

**Validation rules:**
1. Path strategy_id must exist; else 404 (`rejected_unknown_strategy`)
2. Body `secret` must equal `strategy.webhook_secret`; else 401 (`rejected_secret`)
3. Body `action` must be `start` or `stop`; else 400 (`rejected_invalid_action`)
4. On `start`, `mode` must be `live` or `sandbox`; else 400
5. Idempotency: same `(strategy_id, action)` within 60s → 200 OK no-op (`rejected_dedupe` logged)
6. On `start`, if already running → 200 OK no-op
7. On `stop`, if not running → 200 OK no-op

Every request — accepted or rejected — is logged to `webhook_event`.

---

## 7. WebSocket protocol

### 7.1 Connection

```
WS /ws/strategy/{id}
Auth: session cookie (or `?token=` for desktop clients)
```

### 7.2 Server → client messages

**Snapshot** (sent on connect and after reconnect):
```json
{
  "type": "snapshot",
  "ts": 1715192400000,
  "strategy_id": "uuid",
  "run_id": "uuid",
  "status": "running",
  "mode": "live",

  // Strategy-level live risk fields (UI displays all of these):
  "mtm_realized": 600.0,         // sum of P&L from legs already closed (fills locked in)
  "mtm_unrealized": 634.5,       // sum of (ltp − entry_avg) × signed_qty for legs still open
  "mtm_total": 1234.5,           // realized + unrealized — this is what Overall SL/Target evaluates against
  "peak": 2000,                  // session high of mtm_total
  "trough": -500,                // session low of mtm_total
  "overall_sl_mtm": -3000,
  "overall_target_mtm": 5000,
  "lock_profit": {
    "mode": "lock_and_trail",
    "armed": true,
    "if_profit_reaches": 1500,
    "lock_profit_initial": 800,
    "trail_step": 100,
    "current_floor": 1100
  },
  "trail_to_entry_active": false,
  "tick_source_degraded": false,

  "legs": [
    {
      "leg_id": 1,
      "symbol": "NIFTY28MAY2624000CE",
      "exchange": "NFO",
      "status": "open",                  // pending|open|closed|errored
      "position": "B",                   // B|S
      "qty": 75,
      "ltp": 120.5,
      "entry_avg": 100.0,
      "mtm": 1537.5,

      // Per-leg live risk fields (UI displays all of these):
      "sl_pts": 20,                      // configured pts
      "target_pts": null,                // configured pts (null if disabled)
      "effective_sl": 80.0,              // current SL price after trail/trail-to-entry
      "effective_target": null,          // current target price (or null)
      "trail": {                         // trail-SL state
        "x": 10, "y": 5,                 // configured trigger / step in pts
        "active": true,                  // has trail kicked in?
        "favorable_peak": 35.0           // best-favorable move from entry that drives trail
      },
      "distance_to_sl_pts": 40.5,        // ltp − effective_sl (signed by position)
      "distance_to_target_pts": null,
      "tick_source": "ws"                // ws|polling|stale
    }
  ]
}
```

**Delta** (every engine tick, throttled ≤ 10/sec — only changed fields, plus always P&L triplet and `ts`):
```json
{
  "type": "delta",
  "ts_ist": "2026-05-08T15:42:11.200+05:30",
  "ts_ms_utc": 1715192531200,
  "mtm_realized": 600.0,
  "mtm_unrealized": 640.0,
  "mtm_total": 1240.0,
  "peak": 2000,
  "lock_profit": {"current_floor": 1140},
  "tick_source_degraded": false,
  "legs": [
    {
      "leg_id": 1,
      "ltp": 120.6,
      "mtm": 1545.0,
      "effective_sl": 85.0,                // moved up because trail tripped
      "trail": {"active": true, "favorable_peak": 40.0},
      "distance_to_sl_pts": 35.6,
      "tick_source": "ws"
    }
  ]
}
```

**Event** (every persisted `strategy_event` row is also broadcast — same envelope on the wire as in the audit table):
```json
{
  "type": "event",
  "event_id": "9b2f...",                       // matches strategy_event.id
  "ts_ist": "2026-05-08T15:42:10.327+05:30",   // IST ISO 8601
  "ts_ms_utc": 1715192530327,                  // for ordering/math
  "kind": "leg_sl_hit",
  "severity": "warn",
  "leg_id": 1,
  "message": "SL hit on leg 1 (NIFTY28MAY2624000CE) at ₹78.50 — exiting at MARKET",
  "payload": {
    "ltp_at_trigger": 78.5,
    "effective_sl": 80.0,
    "entry_avg": 100.0,
    "leg_mtm_at_trigger": -1612.5,
    "strategy_mtm_at_trigger": -1280.0
  }
}
```

The frontend appends every `event` message to a scrolling **Events** panel on the Live tab and into the dedicated Events tab. Since each event has a stable `event_id` that matches the DB row, refreshing the page or resubscribing replays the same events from `GET /api/v1/strategy/{id}/events`.

**Terminal** (run stopped):
```json
{
  "type": "terminal",
  "ts_ist": "2026-05-08T15:30:00.000+05:30",
  "ts_ms_utc": 1715191200000,
  "stop_reason": "overall_target",
  "pnl_realized": 5230.0
}
```

All `ts` fields in `snapshot` and `delta` messages also use the same `ts_ist` + `ts_ms_utc` pair.

### 7.3 Client → server messages

None in v1. Controls go through REST. (Avoids dual-write race conditions.)

---

## 8. Symbol resolution

### 8.1 ATM mode (delegates to existing service)

`symbol_resolver.resolve_atm(leg, underlying, exchange, expiry_date)` → calls `backend.services.option_symbol_service.get_option_symbol(...)` with `offset=leg.atm_offset` (e.g. `"ATM"`, `"ITM2"`, `"OTM1"`). No new code needed for the lookup logic.

### 8.2 Direct strike mode (new)

`symbol_resolver.resolve_direct_strike(leg, underlying, expiry_date)`:
1. Build OpenAlgo symbol: `f"{base}{expiry_DDMMMYY}{format_strike(leg.strike_value)}{leg.option_type}"`
2. Validate against `symtoken` via existing `_lookup_option_in_db` helper
3. Return `{symbol, exchange, lotsize, tick_size, strike, expiry}`

If not found → fail leg resolution with a clear error (`No option contract found for NIFTY 28-MAY-26 24000 CE on NFO`).

### 8.3 Underlying-for-ATM rules (segment-aware)

The "underlying" used to compute ATM differs per segment. The existing `option_symbol_service._quote_exchange_for` already encodes this; the strategy module just delegates:

| Underlying | Segment | ATM source | Quote symbol used | Quote exchange |
|---|---|---|---|---|
| NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, INDIAVIX | Index options (NFO) | Spot index | `NIFTY` | `NSE_INDEX` |
| SENSEX, BANKEX, SENSEX50 | Index options (BFO) | Spot index | `SENSEX` | `BSE_INDEX` |
| Equity stocks (RELIANCE, TCS, …) | Stock options (NFO) | Cash equity | `RELIANCE` | `NSE` |
| **MCX commodities** (CRUDEOIL, GOLD, …) | **Commodity options (MCX)** | **Nearest non-expired FUT** | `CRUDEOIL18MAY26FUT` (whichever rank-1 FUT) | `MCX` |
| MCX commodities | Currency/CDS — out of v1 scope | (FUT) | (varies) | `CDS` |

This is critical for MCX: there is no "spot" CRUDEOIL we can quote — the underlying for ATM purposes IS the futures contract. The existing helper handles this automatically; the strategy module passes through.

### 8.4 Expiry-rank resolution (weekly / monthly / current / next)

Each leg has `expiry: weekly | monthly | current | next`. Engine delegates expiry discovery to the **existing** `get_expiry_dates(symbol, exchange, instrumenttype)` service in `backend.services.market_data_service` — it returns a sorted list of expiry strings in `DD-MMM-YY` format (e.g. `["18-MAY-26", "18-JUN-26", ...]`) with the nearest first.

**Date formats — read carefully.**
- Service output / DB column: `DD-MMM-YY` with hyphens (`28-APR-26`)
- OpenAlgo symbol embedding: `DDMMMYY` no hyphens (`NIFTY28APR2624250CE`)
- Conversion is mechanical (`s.replace("-", "")` and back). Existing `option_symbol_service` already handles this.

**Rank resolution** (input is the sorted list returned by `get_expiry_dates`):

| Value | Meaning | Resolution rule | Allowed on tabs |
|---|---|---|---|
| `weekly` | Rank-1: nearest non-expired expiry | `data[0]` from `get_expiry_dates(symbol, exchange, instrumenttype)` | Underlyings that have weeklies in `symtoken` (currently NIFTY, SENSEX). Engine doesn't decide what's "weekly" — it just takes whatever rank-1 the symtoken currently produces, which in practice is the nearest weekly when one exists |
| `monthly` | Nearest expiry that is the last of its calendar month | from `data`, pick the smallest entry where there's no later entry within the same `(year, month)` AND it's ≥ today | Index/stock options on NSE/BSE — Monthly tab + Stocks tab |
| `current` | Rank-1 | `data[0]` | MCX |
| `next` | Rank-2 | `data[1]` (or rank-1 if only one expiry exists) | MCX |

UI tab restrictions:
- Weekly & Monthly tab → `weekly` or `monthly`
- Monthly Only tab → `monthly` only
- Stocks tab → `monthly` only
- MCX tab → `current` or `next`

Resolved expiry is cached on the run record — every leg of a strategy uses the same resolved date for the same kind, so the strategy stays internally consistent for the duration of the run. On a positional run that spans multiple sessions, the cached resolution holds (we don't roll to the next contract mid-run automatically — that's a v2 concern).

### 8.5 MCX-specific notes

Three behaviors that differ materially from NSE/BSE — captured here so future maintainers don't re-derive them:

1. **FUT is the underlying.** For an MCX option leg with `underlying=CRUDEOIL`, the engine resolves the underlying FUT by calling `get_expiry_dates("CRUDEOIL", "MCX", "futures")` → takes `data[0]` (e.g. `"18-MAY-26"`) → builds `CRUDEOIL18MAY26FUT` → fetches its LTP via the existing quote pipeline → uses that LTP to drive ATM selection. The existing `option_symbol_service._find_near_month_futures` does effectively the same thing today; the strategy module's resolver delegates so we don't duplicate logic.

2. **Per-commodity expiry cycles, never hardcoded.** CRUDEOIL is monthly, GOLD has bi-monthly + quarterly cycles, SILVER is quarterly + monthly minis (SILVERM/SILVERMIC), NATURALGAS is monthly, weekly options on CRUDEOIL/NATURALGAS exist as MCX rolls them out. We **call `get_expiry_dates`** at run-start and let the user pick `current` / `next` by rank from whatever the service returns. Zero code changes when MCX adds a new product or cycle.

3. **Strike steps vary by commodity.** CRUDEOIL=50, GOLD=100, SILVER=500, NATURALGAS=5, COPPER=10, etc. The strike picker reads from `symtoken` and lists exactly what's tradable — no hardcoded steps anywhere.

### 8.6 Frontend strike picker

```
GET /api/v1/strategy/strikes?underlying=NIFTY&exchange=NFO&expiry=28MAY26&option_type=CE
→ {"strikes": [23000, 23050, 23100, ..., 25000]}
```

Backed by the existing `_STRIKES_CACHE` in `option_symbol_service`. Underlying + expiry + option_type already form the cache key.

---

## 9. Risk engine logic — precise semantics

### 9.1 Per-leg checks (each tick)

Computed for each leg with `status=open`:
```
leg.mtm = (leg.ltp - leg.entry_avg) * sign * leg.qty
  where sign = +1 if position=B else -1
```

**Stop Loss:** if `leg.sl_pts > 0`:
```
if position=B: trigger when ltp ≤ entry_avg - sl_pts
if position=S: trigger when ltp ≥ entry_avg + sl_pts
```

**Target:** if `leg.target_pts > 0`:
```
if position=B: trigger when ltp ≥ entry_avg + target_pts
if position=S: trigger when ltp ≤ entry_avg - target_pts
```

**Trail SL** (X = trigger move, Y = step in pts):
```
peak_favorable = max over all ticks of (ltp - entry_avg) for B, or (entry_avg - ltp) for S
when peak_favorable ≥ X:
  effective_sl moves to entry_avg + (peak_favorable - X) * sign  ... in steps of Y
```

The effective_sl never moves against the position. Once trailed, `effective_sl` replaces the static SL.

### 9.2 Trail-SL-to-entry (cross-cutting)

When `strategy.trail_sl_to_entry=true` AND any one open leg's SL fires:
- For every other still-open leg: `effective_sl = entry_avg`
- `trail_to_entry_active = true` for the run
- Overall SL is bypassed for the remainder of this run (per AlgoTest UI banner)

**P&L definitions used below**:
- `mtm_unrealized` = sum over open legs of `(ltp − entry_avg) × signed_qty`
- `mtm_realized` = sum over closed legs of `(exit_avg − entry_avg) × signed_qty`
- `mtm_total` = `mtm_realized + mtm_unrealized` — what every strategy-level rule evaluates against

### 9.3 Lock-profit semantics

**Lock mode** (static floor):
```
if not lock_armed and mtm_total ≥ if_profit_reaches:
  lock_armed = true
  lock_floor = lock_profit
if lock_armed and mtm_total ≤ lock_floor:
  close_all_legs(stop_reason='lock_profit', order_kind='exit_lock_profit')
```

**Lock+Trail mode** (trailing floor):
```
if not lock_armed and mtm_total ≥ if_profit_reaches:
  lock_armed = true
  lock_floor = lock_profit
if lock_armed:
  if mtm_total > peak: peak = mtm_total
  lock_floor = max(lock_profit, peak - trail_step)
  if mtm_total ≤ lock_floor:
    close_all_legs(stop_reason='lock_profit', order_kind='exit_lock_profit')
```

### 9.4 Overall SL / Target

Evaluated last each tick (after per-leg and lock-profit). Skipped entirely if `trail_to_entry_active=true`.
```
if overall_target_mtm and mtm_total ≥ overall_target_mtm:
    close_all_legs(stop_reason='overall_target', order_kind='exit_overall_target')
elif overall_sl_mtm and mtm_total ≤ -|overall_sl_mtm|:
    close_all_legs(stop_reason='overall_sl', order_kind='exit_overall_sl')
```

### 9.5 EOD / expiry exit

- Intraday: at `exit_time` IST, close all open legs. `stop_reason='eod'`, order `kind='exit_eod'`, event `kind='eod_squareoff'`.
- Positional: on the last open leg's contract expiry day at 15:20 IST, close all open legs. `stop_reason='expiry'`, order `kind='exit_expiry'`, event `kind='expiry_squareoff'`.

---

## 10. Scheduler

APScheduler `AsyncIOScheduler` running in-process.

### 10.1 Job model

For each strategy with `scheduler.enabled=true`:
- One cron job: `cron(day_of_week=days, hour=H, minute=M)` → calls `engine.start_run(strategy_id, mode=strategy.default_mode)` (idempotent: if already running, no-op)
- Optional auto-stop cron job at `auto_stop_time`

### 10.2 Job persistence

Jobs are recreated from DB on FastAPI startup (in `recovery.py`). Source of truth is the `strategy.scheduler` jsonb — the in-memory APScheduler is purely operational state.

### 10.3 Default config

```jsonc
{
  "enabled": false,
  "days": ["MON", "TUE", "WED", "THU", "FRI"],
  "start_time": "09:15",
  "auto_stop_time": null,
  "default_mode": "sandbox"   // safer default
}
```

---

## 11. TradingView webhook

**Design choice:** the webhook URL itself contains a per-strategy secret token (Slack / GitHub / Stripe pattern). The strategy is identified BY the token; there's no separate `strategy_id` in the URL and no `secret` field in the body. This keeps the TV alert config minimal and removes one round of validation.

### 11.1 URL format shown to user

```
https://your-host/webhook/strategy/{webhook_token}
```

`webhook_token` is a high-entropy random string generated server-side at strategy creation:
- Format: `obwh_` prefix + 43 chars URL-safe base64 (32 bytes of entropy)
- Example: `obwh_R8tK3xZ-7vN2mQpL9wY4eA1bF5cH6gI0jK2lM3nO4pQ`
- Stored in DB as `SHA-256(token)` in `strategy.webhook_token_hash` (unique index for O(1) lookup)
- Plaintext is **shown once** in the API response on create/rotate; never readable from the DB thereafter
- Rotating issues a fresh token and invalidates the old one immediately

### 11.2 Recommended TV alert message body

```json
{
  "action": "start",
  "mode": "sandbox"
}
```

That's it. No secret in the body — the URL already carries it. `mode` is required when `action="start"`.

### 11.3 Curl example (shown in UI)

```
curl -X POST 'https://your-host/webhook/strategy/obwh_R8tK3xZ-7vN2mQpL9wY4eA1bF5cH6gI0jK2lM3nO4pQ' \
  -H 'Content-Type: application/json' \
  -d '{"action":"start","mode":"sandbox"}'
```

### 11.4 Validation pipeline

```
incoming POST /webhook/strategy/{token} with body B:
  hash = sha256(token)
  strategy = SELECT * FROM strategy WHERE webhook_token_hash = hash    # O(1) indexed
  if not strategy → 401 (rejected_token; payload logged with strategy_id=null)
  if request_ip not in strategy.webhook_ip_allowlist (when set) → 401 (rejected_ip)
  if rate_limit(strategy.id) exceeded → 429 (rate_limited)
  parse_json(B); if too large → 413; if invalid → 400
  if B.action ∉ {start, stop} → 400 (rejected_invalid_action)
  if B.action = start and B.mode ∉ {live, sandbox} → 400
  if B.action = start and B.mode = live and not strategy.live_enabled → 403 (rejected_live_disabled)
  if dedupe(strategy.id, action, mode) seen in last 60s → 200 OK (rejected_dedupe in event log only)
  if cooling_off active (last stop < 30s ago) → 200 OK (rejected_cooling_off in event log only)
  call engine.start_run / engine.stop_run with trigger_source='webhook', webhook_event_id=this
  return 200 OK
```

Every request — including rejections — is logged to `webhook_event`. UI surface for these logs lives under `/strategy/:id` → Webhook tab. The token plaintext is **never** logged; only `webhook_event.result` and the action/mode fields make it into the audit row.

### 11.5 Why this design works for TradingView only

- TV's free plan supports webhook URL but not custom headers — URL-embedded token works on every plan.
- TV stores the URL server-side, so it doesn't end up in user browser history.
- For server-side access logs, configure the reverse proxy to redact `/webhook/strategy/*` paths (deployment guide note).
- For the threat model in Section 14.1, this gives equivalent security to body-secret + constant-time comparison while halving the validation surface.

---

## 12. Sandbox / live routing

Single decision point at order placement:

```python
if run.mode == 'live':
    broker_module.place_order_api(order_data, auth_token)
else:  # sandbox
    sandbox_service.place_sandbox_order(order_data, user_id)
```

Sandbox fills happen at the next-tick LTP plus a configurable slippage (existing sandbox_service behavior). All other engine logic — risk evaluation, exits, MTM, WS broadcast — is mode-agnostic.

`strategy_order` rows are written for sandbox runs too. `broker_order_id` for sandbox uses a synthetic prefix (`SANDBOX-<uuid>`) to keep the schema unified.

---

## 13. Error handling

| Scenario | Behavior |
|---|---|
| Leg resolution fails on start | Reject `start_run`, no orders placed, return 4xx with leg index |
| Partial fill on entry | Wait `entry_fill_timeout` (default 60s); if still pending, cancel and reject the run |
| One leg's entry fails after others filled | Cancel any other in-flight, square off any filled, fail run with `error` |
| WS subscribe fails for a leg | Mark leg's `tick_source=polling`, start REST polling immediately; log warning; engine continues with no risk-eval delay |
| WS LTP becomes stale (>10s for a leg) | Auto-switch that leg to REST polling; broadcast `tick_source=polling` to client; keep retrying WS in background; switch back on first WS tick |
| REST poll returns 429 | Exponential backoff 2s → 5s → 10s → 30s cap per symbol; flag run as `tick_source_degraded`; engine continues with last-known LTP |
| Both WS and REST fail for 60s | Stop the run with `stop_reason='tick_stale'`, square off open legs at MARKET, alert user via UI banner |
| Broker order API timeout/5xx on exit | Retry 3x with exponential backoff; if all fail, leave `strategy_order.status='rejected'` with `reject_reason='broker_unreachable'`, emit `severity=critical` event, surface a red banner on the Live tab prompting manual square-off via broker terminal. Run stays running so the user can act. |
| Sandbox service unavailable | Reject start_run with clear error |
| Redis unavailable | Engine refuses to start new runs; existing runs continue with degraded checkpoint cadence (every 5s instead of 30s, direct to DB) |
| Recovery fails for a run | Mark stopped, `stop_reason=recovery_failed`, surface in UI banner |

---

## 14. Security

Security is woven through every phase below, not bolted on at the end. The strategy module places real orders with real money and accepts unauthenticated public webhooks — both surfaces demand strict controls. This section defines the policies; each build phase implements its share.

### 14.1 Threat model

| # | Threat | Vector |
|---|---|---|
| T1 | Unauthorized order placement | Compromised user session / API key abused via REST |
| T2 | Webhook abuse | Public `/webhook/strategy/{id}` endpoint hit with guessed/leaked secret |
| T3 | Cross-tenant data leak | User A reads/edits user B's strategy via path-id manipulation |
| T4 | Fat-finger / runaway orders | Misconfigured strategy or bug fires excessive lots; webhook loop |
| T5 | Mode confusion | Operator believes they're in sandbox, places live orders |
| T6 | Data tampering | SQL injection, mass-assignment via PATCH |
| T7 | Audit gap | An action happens but isn't recorded — incident response can't reconstruct |
| T8 | DoS / resource exhaustion | Many strategies × many ticks × many WS subscribers crash the box |
| T9 | Secret exposure | Webhook secret leaks via logs, error messages, response bodies, or DB dump |
| T10 | Replay attack | Captured webhook request replayed to trigger unintended state changes |

### 14.2 Authentication & authorization

**REST API (`/api/v1/strategy/*`)**: every endpoint behind the existing `get_api_user` dependency. No anonymous access.

**WebSocket (`/ws/strategy/{id}`)**: same auth flow as `/websocket/test` — first message after socket open must be `{"action": "authenticate", "api_key": "..."}`. No unauthenticated subscriptions. Reject with 4401 close code on bad/missing key. Inactivity timeout: 60s without an authenticate frame → close.

**Webhook (`/webhook/strategy/{id}`)**: deliberately public; auth is shared-secret-in-body (Section 11). Detailed controls in Section 14.4.

**Per-resource ownership** — every endpoint that accepts `strategy_id`, `run_id`, or `leg_id` enforces `record.user_id == current_user.id` at the repository layer.

> Cross-tenant access returns **404, not 403** — never reveal that someone else's resource exists. (Mitigates T3 enumeration.)

The repository wrapper for any read is shaped:
```python
def get_strategy(self, strategy_id: UUID, user_id: int) -> Strategy:
    row = session.execute(
        select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return row
```
Engine code never sees a strategy_id without the user_id filter; this is enforced by repository-layer signatures, not engine convention.

### 14.3 Live-mode guardrails (T1, T4, T5)

- **Per-strategy live opt-in** — every new strategy is born `live_enabled=false`. Going live requires an explicit user action via `POST /api/v1/strategy/{id}/enable_live` which requires re-authentication (password re-entry within last 5 minutes, or 2FA where configured). This is recorded in `strategy_event` with `kind=live_enabled`.
- **Mode mismatch protection** — `start_run` with `mode=live` returns 403 if `live_enabled=false`. Webhooks with `mode=live` likewise.
- **Visual mode indicators** — UI shows a red `LIVE` badge on every page and a yellow `SANDBOX` badge in sandbox; confirmation modals display the active mode in bold before executing destructive actions (Start, Close All, single-leg Close).
- **Order sanity caps** — enforced server-side at order-build time, not just UI:
  - `lots ≤ MAX_LOTS_PER_LEG` (env, default 50)
  - `legs.length ≤ MAX_LEGS_PER_STRATEGY` (env, default 10)
  - Notional check: `sum(lots × lotsize × ltp)` ≤ `MAX_NOTIONAL_PER_STRATEGY` (env, default ₹50,00,000) — refused with `reject_reason='notional_exceeded'`
  - Per-user concurrent running cap: `MAX_RUNNING_STRATEGIES_PER_USER` (env, default 10)
- **Daily loss limit** — per-user setting (defaults to none). When active and `today's realized P&L across all live runs ≤ -limit`, all running live strategies are stopped with `stop_reason='daily_loss_limit'`, and new starts in live mode are refused for the rest of the day.
- **Cooling-off period** — after a `stop` for any reason, the same strategy cannot start again within `STRATEGY_COOLING_OFF_SEC` (default 30s). Mitigates webhook loops where a misconfigured TV alert oscillates start/stop/start.

### 14.4 Webhook security (T2, T9, T10)

#### Token storage & lookup

- **URL-embedded token** (Section 11): high-entropy `obwh_<43char>` slug per strategy.
- **Stored as SHA-256(token)** in `strategy.webhook_token_hash`, **uniquely indexed** for O(1) lookup. SHA-256 is the right hash here — the input has 256+ bits of entropy, so the slow-by-design property of argon2/bcrypt that prevents password brute-force isn't relevant. SHA-256 is deterministic (no salt) which is what enables the indexed query.
- **Plaintext shown once** at create/rotate in the API response, never readable thereafter. Rotating regenerates and invalidates the old token immediately.
- **Plaintext never logged** — log redaction filter scrubs the token from any structured log payload, and the URL path itself is redacted by the reverse proxy in production (deployment guide).
- **Constant-time miss handling** — when the lookup returns no row, 401 is returned with the same body and timing characteristics as a successful auth. Never reveal "no such strategy" vs "wrong token".

#### Replay protection

- **Idempotency window** (Section 11.4): same `(strategy_id, action, mode)` within 60s → 200 OK, no engine action. Stored in Redis `strategy:webhook:dedupe:*`.
- Optional **timestamp/nonce** field for stricter clients: if body includes `ts`, reject if `|now − ts| > 5 min`. (Documented; not required because TV's alert engine doesn't natively send timestamps.)

#### Rate limiting

- Per-strategy: `WEBHOOK_RATE_PER_STRATEGY_PER_MIN` (default 60). Token bucket in Redis.
- Per-IP: `WEBHOOK_RATE_PER_IP_PER_MIN` (default 600 across all strategies). Catches scrapers/scanners.
- Burst over the cap → 429 with `Retry-After`; logged to `webhook_event` with `result='rate_limited'`.

#### Optional IP allow-list

`strategy.webhook_ip_allowlist` (jsonb, nullable) — when populated, requests from outside the CIDR list are rejected with `result='rejected_ip'`. Defaults empty (any IP). UI surfaces TradingView's published IP ranges as a one-click preset.

#### HTTPS enforcement

`/webhook/strategy/*` only available over HTTPS in production. Reverse proxy enforces TLS; FastAPI middleware additionally rejects `X-Forwarded-Proto: http` for these paths.

#### Payload size cap

Body limited to 8 KB (FastAPI middleware). Anything larger → 413.

#### HMAC headers — not in v1

Considered and dropped. TradingView's free plan does not support custom webhook headers, and the v1 trigger source is TV alerts only. With argon2id-hashed body-secret + constant-time comparison + HTTPS + rate limiting + idempotency + optional IP allow-list, HMAC adds complexity without a meaningful security delta for this threat model. Revisit if a non-TV signal source is added.

### 14.5 Input validation (T6)

- **Pydantic strict mode** for every request schema (`extra = 'forbid'`). Unknown fields → 400. No silent drops.
- **Numeric bounds**: `lots > 0`, `lots ≤ MAX_LOTS_PER_LEG`, `sl_pts ≥ 0`, `target_pts ≥ 0`, `trail.x ≥ 0`, `trail.y ≥ 0`, `overall_sl_mtm ≥ 0` (entered as positive, applied as negative threshold).
- **Enum strictness**: every enum field validated against the documented enum; unknown values rejected. (`segment`, `position`, `option_type`, `strike_mode`, `expiry`, `mode`, `kind`, …)
- **Symbol/exchange validation**: `exchange` must be in `VALID_EXCHANGES` for entry orders; symbols must resolve in `symtoken` before run-start.
- **String length caps**: `name ≤ 100`, free-form text fields capped to prevent DB bloat / XSS surface.
- **Schedule sanity**: `entry_time < exit_time` for intraday; `start_time` parsed strictly as `HH:MM` (24h, no seconds).
- **PATCH allowlist**: `PATCH /api/v1/strategy/{id}` accepts a curated set of fields only. Mass-assignment of `webhook_secret_hash`, `user_id`, `current_run_id`, etc. is **never** possible.
- **All DB queries are parameterized** via SQLAlchemy `text(:param)` or ORM. No string concatenation. (Already the codebase convention; explicit reminder.)

### 14.6 Audit & forensics (T7)

- Every state-changing action produces a `strategy_event` row (Section 4.1). Every webhook hit produces a `webhook_event` row.
- `strategy_event` denormalizes `user_id` for forensic queries that span tables without joining.
- `webhook_event` records `result`, `ip`, `user_agent`, but **never** the raw secret or any header that contains one.
- Audit rows are append-only; no UPDATE or DELETE statements anywhere in the strategy module touch them.
- Logs include a structured `[strategy_id, run_id, leg_id, user_id]` context block on every risk-event line so `grep` works for incident response.

### 14.7 Resource limits / DoS (T8)

- **Engine subscription cap**: `MAX_TICK_SUBSCRIPTIONS_PER_PROCESS` (env, default 2000).
- **WS connections per user**: `MAX_WS_CONNECTIONS_PER_USER` (default 5). Existing `MAX_WS_CONNECTIONS=10` global cap (in `websocket_proxy/server.py`) stays.
- **Scheduler job cap**: `MAX_SCHEDULED_JOBS_PER_USER` (default 20).
- **REST rate limits**: `/start`, `/close_all`, `/legs/*/close` are limited per user (default 30/min); list endpoints 120/min. Backed by the same Redis token bucket as webhook rate limiting.
- **Tick processing back-pressure**: if engine queue depth exceeds 10× normal for 30s, log warning and slow new run starts.

### 14.8 Secret & credential handling (T9)

- **Webhook secret**: stored hashed (argon2id), shown once on create / rotate; never readable thereafter.
- **API keys**: existing platform handling — never logged, redacted in error traces.
- **Broker auth tokens**: held in encrypted user session storage (existing); never written to `strategy_*` tables. Engine pulls them from session at run-start.
- **Log redaction filter**: a logging filter in `backend/strategy/__init__.py` scrubs `secret`, `api_key`, `auth_token`, `password`, `Authorization` keys from any log payload. Tested with synthetic payloads in CI.
- **Error responses** never echo back submitted secrets. A request body with a wrong secret returns `{"status":"error","message":"Authentication failed"}` — the message is identical regardless of why.

### 14.9 Mode-specific UI guardrails (T5)

- Live-mode badge: persistent `LIVE` chip in the header of every strategy page when `live_enabled=true`.
- Confirmation modal copy explicitly names the mode: "Place LIVE order on broker XYZ?" — not generic "Are you sure?".
- Sandbox-only banner on the wizard when no live strategies exist for the user, suggesting paper-test first.

### 14.10 Security work mapped to build phases

Security isn't a separate phase — each build step (Section 15) lands its share:

| Phase | Security work |
|---|---|
| 1 (schema/CRUD) | Repository-layer `user_id` filtering on every read/write; Pydantic strict mode; PATCH allowlist; `webhook_secret_hash` (not plaintext) column |
| 2 (frontend shell) | Mode badges; live-enable opt-in flow; confirmation modals |
| 3 (resolver) | Symbol whitelist enforcement; numeric bounds on strike picker queries |
| 4 (engine sandbox) | Concurrency caps; cooling-off period; mode mismatch refusals |
| 5 (recovery) | Recovery never elevates a sandbox run to live; broker reconciliation refuses to reconcile a run whose mode differs from observed broker state |
| 6 (per-leg risk + WS) | WS auth flow; per-user WS cap; log redaction filter live; audit rows for every event |
| 7 (strategy-level risk) | Notional caps enforced at order-build; daily-loss-limit applied across all live runs |
| 8 (scheduler) | Per-user scheduled-job cap; scheduler refuses to start live runs if `live_enabled=false` (defense in depth) |
| 9 (webhook) | Argon2 secret comparison; rate limiting; idempotency; payload size cap; HTTPS enforcement; optional HMAC header; optional IP allow-list |
| 10 (live wiring) | **Final hardening pass**: dependency audit (npm/pip), `bandit`/`safety` scan, manual penetration test against webhook + REST surfaces, log-redactor regression suite |

### 14.11 Open security decisions (need your call)

| # | Decision | Default | Alternative |
|---|---|---|---|
| S1 | Live-mode opt-in granularity | Per strategy | Per user (single toggle gates everything) — simpler but coarser |
| S2 | Daily loss limit default | Off (user opts in) | On with sane default like ₹50,000 — paternalistic but safer |
| S3 | Webhook IP allow-list | Optional, off by default | Required, populated with TV ranges by default — stricter but breaks user testing from local curl |
| S4 | Re-auth window for live-enable | 5 minutes | Session-fresh-only (within last 60s) for highest assurance, or none (just confirm dialog) for lowest friction |

**Resolved by user input on 2026-05-08:**
- Webhook auth: URL-embedded token (Slack-style), SHA-256-hashed in DB, no body secret, no HMAC. (Was S1, S5.)
- Token format and rotation flow: per Section 11.1.
- TV-only assumption baked in: no header-based auth needed.

---

## 15. Build order

Every step shipped as its own commit on `main`, independently testable.

| # | Scope | Commit | Status |
|---|---|---|---|
| 1 | **Foundation** — DB schema (`sm_strategy`, `sm_strategy_run`, `sm_strategy_order`, `sm_strategy_checkpoint`, `sm_webhook_event`, `sm_strategy_event`, all `sm_` prefixed, Integer PKs); ORM + strict-mode Pydantic schemas + repository (user_id ownership filter); CRUD router at `/web/strategy/*` (session-cookie auth); webhook URL-token (SHA-256-hashed, one-time view, indexed lookup); IST timestamp helper; **event bus** (ported from OpenAlgo `utils/event_bus.py`); **strategy_audit_subscriber** persists every published event into `sm_strategy_event` via a sync engine. | `aef3a4c` | ✓ |
| 2 | Frontend: list page (Realized/Unrealized/Total P&L cols), wizard (5 universe tabs incl. MCX dynamic), detail page shell with all tabs (config-only, no live runtime) | `ffaceef` | ✓ |
| 3 | Symbol resolver (ATM delegation + direct strike) + helper endpoints (`/strikes`, `/underlyings`); strike picker dialog | `a318d88` | ✓ |
| 4 | Engine skeleton: sandbox order path, manual start/stop/close_all + per-leg `/legs/{leg_id}/close` + run lifecycle (no risk logic yet); Phase-4 duplicate-exit guard | `c13f191` | ✓ |
| 5 | Crash-safe recovery + checkpoint loop + Redis ownership lock; broker reconciliation wired (live exercised in Phase 10) | `8cf87d0` | ✓ |
| 6 | Tick feed via existing `MarketDataCache` priority pub/sub + Per-leg risk (SL, Target, Trail SL) + WebSocket streaming with realized/unrealized/total split + Events tab wired to live WS feed | `29dfd36` | ✓ |
| 7 | Strategy-level risk: Overall SL/Target, Lock-profit (Lock + Lock+Trail), Trail-SL-to-entry. Audit-hygiene fix: single `lock_profit_armed` event on the Lock+Trail arming tick (trail-adjusted floor written directly into the armed event). | `c4fdf39`, `6ceee71` | ✓ |
| 8 | Scheduler (APScheduler `AsyncIOScheduler` with `Asia/Kolkata` tz, `default_mode` field); cron jobs persisted by re-syncing from `strategy.scheduler` jsonb on every boot — no jobstore dependency | `19f9e7e` | ✓ |
| 9 | TradingView webhook receiver at `POST /webhook/strategy/{token}`; URL-embedded SHA-256-hashed token; full 11-stage validation pipeline; every accepted/rejected request audited to `sm_webhook_event`; `GET /web/strategy/{id}/webhook_events` for the UI | `4beb51c` | ✓ |
| 10 | **Live mode wiring** — `POST /web/strategy/{id}/enable_live` (password re-auth) + `disable_live`; `backend.strategy.live_auth.resolve_live_auth` fetches the user's BrokerAuth token fresh on every auto-exit; scheduler / webhook / tick processor all thread real broker auth through for live runs; UI "Enable LIVE" / "Disable LIVE" buttons with password-prompt dialog | `7e9735c` | ✓ |

Each commit includes the integration test that verifies the phase, with results captured in the commit message. Lot sizes are dynamically read from `symtoken.lotsize` at runtime — never hardcoded anywhere in the engine path.

---

## 16. Open questions / risks

**Resolved in v1:**
- **Time zone**: UTC stored in PG `timestamptz`, IST rendered everywhere on the wire and in the UI; APScheduler runs with `tz='Asia/Kolkata'` (Section 4.4, Phase 8).
- **Concurrent webhook + scheduler start**: idempotency lands at two layers — `engine.start_run` refuses when `strategy.status='running'`, and the webhook handler's 60s dedupe window catches repeats within the same minute (Phase 9 verified).
- **Live broker token expiry mid-run**: `resolve_live_auth` runs on every auto-exit, so a token refresh during the trading day is transparent. When the session is expired/revoked, auto-exits are refused and logged at WARN — operator must square-off manually via the UI (Phase 10).

**Still open / accepted v1 limitations:**

| Topic | Note | Default in v1 |
|---|---|---|
| Multi-broker | Strategy bound to user's active broker at start. What if user changes broker mid-run? | Run continues on original broker; new starts pick the user's current active broker |
| Partial fills | Engine assumes all-or-nothing leg fills. Real markets can fill 50/75 lots. | Behaviour observed but not enforced; broker reconciliation reflects whatever filled. Hard rejection on partial is a v2 concern. |
| Slippage in sandbox | What slippage model? | Reuses existing `sandbox_service` default; user-tunable in v2 |
| WS scaling | Many concurrent strategies × many subscribers per strategy | v1 ships single-process. Redis pub/sub fan-out is a deployment concern, not a code change to the engine. |
| Holiday calendar | Scheduler firing on NSE holidays | Rely on user-defined weekday subset; NSE market calendar integration is v2 |

---

## 17. Glossary

- **MTM** — mark-to-market profit or loss
- **ATM** — at-the-money strike (closest strike to underlying spot)
- **ITMn / OTMn** — n strikes in-the-money / out-of-the-money relative to ATM
- **Trail SL** — stop loss that ratchets up (favorable direction only) as price moves in your favor
- **Lock-profit** — once a profit threshold is reached, set a floor that limits how much profit can be given back
- **Square-off** — close all open positions in a strategy
- **Run** — a single activation of a strategy from start to stop (one strategy can have many runs over its lifetime)
