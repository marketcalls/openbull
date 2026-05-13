# Strategy Module Audit — Running Scratchpad

Ralph loop validating `backend/strategy/*` against `docs/plan/strategy-module.md`
and the design docs (`docs/design/order-constants.md`, `symbol-format.md`,
`websockets-format.md`, `SERVICES.md`). One iteration per minute.

Conventions:
- **FIX (commit)** — bug confirmed, fix landed locally, no push.
- **FLAG (skip)** — ambiguous or behavioural — surfaced for user; not fixed.
- **CLEAN** — area checked, nothing actionable.

---

## Iteration 7 — Concurrent webhook + scheduler-start idempotency

### FIX — `engine.start_run` had a TOCTOU race between status check and commit
- File: `backend/strategy/engine.py` line 209 (pre-fix) / 211+ (post-fix)
- Symptom: two concurrent triggers (webhook + scheduler at the same
  minute, manual UI + webhook, or just two workers in a multi-process
  deployment) could both observe `strategy.status='stopped'` on their
  own stale in-memory copies, both pass the gate at line 209, both
  proceed to resolve legs and place entry orders. Only the second's
  `repo.start_run` commit would win - the result is duplicate broker
  orders against a single strategy, and `current_run_id` pointing only
  at the second run while the first run row is orphaned but with real
  filled orders attached.
- Plan section 16 explicitly lists this as a resolved invariant:
  "Concurrent webhook + scheduler start: idempotency lands at two
  layers - engine.start_run refuses when strategy.status='running'..."
  The check existed but was not atomic.
- Webhook 60s dedupe protects webhook-vs-webhook with the same
  action+mode. It does NOT cover webhook+scheduler, manual+webhook,
  or scheduler+scheduler (APScheduler misfires).
- Fix: at the top of `engine.start_run`, run
  `SELECT * FROM sm_strategy WHERE id=:id FOR UPDATE` and use the
  freshly-locked row's status (not the caller's stale copy). The lock
  is held until `repo.start_run` commits (which flips status to
  'running'), at which point any blocked concurrent caller wakes up,
  reads the new status, and raises EngineError. Postgres row locks
  work across processes, so multi-worker deployments are covered too.
- The lock is acquired before leg resolution and order placement, so
  no broker calls happen for a losing-race invocation. Side-effect
  free rejection.

### FLAG — `engine.stop_run` has the symmetric race (not fixed this iteration)
- File: `backend/strategy/engine.py` line 512
- Symptom: parallel `stop_run` calls (manual Stop + webhook stop +
  scheduler auto-stop firing within the same window) both pass the
  `status != "running"` gate, both call `_exit_legs`, both call
  `repo.finalize_run`. The `legs_with_exits` guard in `_exit_legs`
  catches duplicate exits at the leg level, but `finalize_run` is
  called twice - the second commit overwrites the first's
  `stopped_at` / `stop_reason` / `pnl_realized`. Less destructive
  than the start race (no duplicate broker orders), but the audit
  trail "why did this run stop" becomes incorrect.
- Not fixed this iteration: plan section 16 only lists the start
  race; stop concurrency is a different scope. Same `with_for_update`
  pattern would fix it - flagging for a later iteration or your
  product decision on whether the messy audit trail is worth a fix.

---

## Iteration 6 — Time-zone handling

### CLEAN — End-to-end time-zone hygiene matches plan section 4.4
- **Storage**: every timestamp column on `sm_strategy*` tables is
  `DateTime(timezone=True)` (Postgres `timestamptz`). Verified across
  `sm_strategy`, `sm_strategy_run`, `sm_strategy_order`,
  `sm_strategy_checkpoint`, `sm_strategy_event`, `sm_webhook_event`.
- **Server clock**: `backend/strategy/time_utils.now_utc()` returns
  `datetime.now(timezone.utc)`. Every server-side timestamp assignment
  in `backend/strategy/*` uses `now_utc()` - `repository.run.stopped_at`,
  `recovery.order.filled_at`, `webhook_handler` cooling-off window,
  `tick_processor` WS frames, `ws.py` snapshot frames. Zero naive
  `datetime.now()` or deprecated `datetime.utcnow()` calls anywhere in
  the strategy module.
- **API rendering**: `format_ist()` wraps every timestamp on the way
  out - `created_at`, `updated_at`, `placed_at`, `filled_at`,
  `started_at`, `stopped_at`, audit event `ts`, webhook `received_at`
  (all verified in `routers/strategy_module.py`). Output is ISO 8601
  with explicit `+05:30` offset, sub-second precision via
  `isoformat(timespec="milliseconds")`.
- **WS payloads**: `tick_processor._broadcast_delta`,
  `ws._build_snapshot`, `strategy_ws_subscriber.push_event` all emit
  both `ts_ist` (IST ISO string) and `ts_ms_utc` (epoch ms) - matches
  plan section 4.4 dual-field design for high-frequency frames.
- **APScheduler**: `scheduler.py:60` initializes
  `AsyncIOScheduler(timezone=IST_TZ)` where `IST_TZ = "Asia/Kolkata"`;
  every `CronTrigger` (start + auto-stop) also passes
  `timezone=IST_TZ`. Defense-in-depth - if APScheduler's process-level
  default ever drifts, the per-trigger override is authoritative.
- **Frontend**: `Detail.tsx:formatIst` and `List.tsx:formatIst` both
  force `timeZone: "Asia/Kolkata"` on `toLocaleString("en-IN", ...)`
  and append a literal `IST` suffix. The backend already sends
  IST-offset strings, so the frontend essentially round-trips the
  timezone for display formatting only - no UTC ever reaches the UI.
- **Defensive fallback** in `format_ist`: naive input is assumed UTC
  via `dt.replace(tzinfo=timezone.utc)`. Effectively dead code given
  PG `timestamptz` always returns aware datetimes through SQLAlchemy
  `DateTime(timezone=True)`, but harmless.

---

## Iteration 5 — Lot-size resolution end-to-end (re-verification)

### CLEAN — Strategy module lot-size path is consistent after iteration 1's fix
- Source of truth: symtoken via `option_symbol_service._lookup_option_in_db`
  for both futures (engine.py:118) and options (symbol_resolver.py:171 via
  `get_option_symbol` line 276). Cash equity correctly uses an explicit
  `lotsize=1` (engine.py:75) - correct for 1-share NSE/BSE cash units.
- Iteration 1's `engine.py:232-252` guard raises EngineError when an
  options/futures leg's resolved lotsize is missing or non-positive,
  blocking the silent 1-unit-order anti-pattern.
- `qty = int(r["lots"]) * int(r["lotsize"])` (engine.py:275) is the only
  quantity computation in the engine path. Result is stored on the
  `sm_strategy_order` row at placement.
- Exit path uses `entry.qty` (engine.py:462) so exit qty == entry qty
  by construction - no second multiplication, no re-derivation from
  potentially-stale lotsize.
- Redis state stores absolute `qty` only (state.py:65), not lots +
  lotsize separately, so a symtoken-row change mid-run cannot poison
  the in-flight state.
- `repository.py`, `risk_evaluator.py`, `checkpoint.py` contain zero
  lotsize references - they consume `qty` only.
- Frontend wizard collects `lots` (count) only; backend multiplies by
  symtoken lotsize. The `StartRunResponse.legs[].lotsize` echoed back
  is display-only - no client-side qty math against it.

### FLAG (out of scope - sandbox layer, not strategy module)
- `backend/sandbox/symbol_info.py:84` has `lot_size=int(row.lotsize or 1)`,
  the same anti-pattern iteration 1 fixed in the strategy engine. It
  drives the sandbox layer's "qty is a lot multiple" validation. Today
  the strategy module sends post-multiplied qty to sandbox so the
  validation only fails if symtoken is broken AND a non-lot-multiple
  qty reaches sandbox via some other surface (manual API, basket order
  service, etc.). Not a strategy-module bug per the audit scope, but
  flagging as a related observation worth a separate fix if you choose.

---

## Iteration 4 — Service-layer contract drift

### FIX — `_reconcile_live_order` mis-unpacks broker plugin response
- File: `backend/strategy/recovery.py:101` (now line ~100 after fix)
- Symptom: `ok, response = get_book(auth_token)` unpacks two values from
  what every broker plugin (Zerodha, Upstox, Angel, Dhan, Fyers) returns
  as a single `dict` — `get_order_book(auth) -> dict`. The unpack iterates
  the dict's keys; whatever happens next (`response.get("data")` on a
  string-keyed local) raises AttributeError and lands in the outer
  `except`, silently swallowing the result and returning None. Live
  recovery reconciliation would never work once enabled.
- Why it didn't bite yet: `_reconcile_run_orders` currently short-circuits
  the live branch with a comment ("recovery doesn't have a session
  token"), so `_reconcile_live_order` is dead code today. But it's
  invoked the moment that branch is wired (Phase 10 contract is for
  live recovery per plan §5.4 step 2.c).
- Fix: route through the documented service entry point
  `orderbook_service.get_orderbook_with_auth(auth_token, broker)` which
  returns the canonical `(success, response, status)` tuple with
  per-broker mapping/transform applied. Switch the field-lookup order
  to prefer `order_status` (the documented post-transform key) over
  `status`. user_id intentionally omitted so the call always hits the
  broker, never sandbox.

### CLEAN — `dispatch_order` consumer-side response handling
- `engine.start_run` / `engine._exit_legs` extract `response.get("orderid")`
  for the broker order id. Matches the contract on both sandbox
  (`{"status": "success", "orderid": row.orderid, "mode": "sandbox"}`)
  and live (`{"status": "success", "orderid": order_id}`). On failure
  both surfaces produce `{"status": "error", "message": ...}` and the
  engine correctly stores `broker_order_id=None`, `status="rejected"`,
  `reject_reason=response["message"]`.

### CLEAN — No modify/cancel surface drift
- Strategy module never calls `modify_order_service` or
  `cancel_order_service`. Exit orders are placed as fresh MARKET orders
  via `dispatch_order` rather than modifying or cancelling pending
  entry orders. So modify/cancel contract drift is moot today.

### CLEAN — All service imports use documented entry points
- `dispatch_order` -> `sandbox_service.place_order` + `order_service.
  place_order_with_auth` (canonical).
- `symbol_resolver` -> `market_data_service.get_expiry_dates`,
  `option_symbol_service.{get_option_symbol, _lookup_option_in_db,
  _format_strike, _option_exchange_for, _fetch_available_strikes,
  _run_query}` (existing helpers, audited iteration 2).
- `tick_feed` -> `market_data_cache.subscribe_critical` (audited
  iteration 2).
- `recovery` (after this fix) -> `orderbook_service.get_orderbook_with_auth`.

---

## Iteration 3 — WebSocket subscribe / push / reconnect / dedupe

### CLEAN — Frontend WS reconnect behaviour
- `frontend/src/hooks/useStrategyWebSocket.ts`: exponential backoff 1s,
  2s, 4s, 8s, capped at 30s (matches plan §3.3); retry counter resets
  on a successful `snapshot` frame (line 154); 30s server-side heartbeat
  via ``{"type": "ping"}`` keeps proxies and half-open sockets in check;
  `enabled=false` cleanly tears down (line 201) without leaking timers.

### CLEAN — Push throttle and per-strategy queue isolation
- `backend/strategy/broadcast.py`: `DELTA_THROTTLE_MS=100` matches plan
  §2's "WS broadcast throttle: at most 10 messages/sec/strategy".
  Events are unthrottled (correct — operators must see SL hits live).
  `push_terminal` drops the throttle reservation so a terminal frame
  always wins. Queue size 200 + `put_nowait` drops on overflow with
  the snapshot-on-reconnect repair contract documented inline.

### CLEAN — Event topic / subscriber wiring
- `backend/subscribers/__init__.py` enumerates 24 strategy topics and
  registers each on both audit and ws subscribers. Cross-referenced
  every published event class in `backend/events/strategy_events.py`
  (LegSlHitEvent, OverallSlHitEvent, LockProfit*, etc.) — every emitted
  topic is in the subscribed set. Plan §4.1's enum lists additional
  kinds (`eod_squareoff`, `expiry_squareoff`, `tick_source_*`,
  `recovery_*`, `run_paused/resumed`, `close_all_manual`) but no Event
  class is defined for them, so the subscriber gap is plan-internal
  and not a code bug (feature-scope choice).

### FLAG (minor, self-healing) — Snapshot-then-register race
- `backend/strategy/ws.py:159-162`: builds the snapshot from Redis
  state, sends it, then calls `broadcast.register`. If the tick
  processor publishes a delta between Redis-read and queue-register,
  that delta hits `_broadcast_nowait` with zero subscribers and is
  dropped. The client's snapshot is one tick stale.
- Why self-healing: subsequent ticks include full strategy-level P&L
  + per-leg LTP/MTM overwrites, so steady-state ticking heals the gap
  within one tick. Per-leg `effective_sl`/`trail_active` advances
  during the race window stay stale until the next tick on that leg.
- Why not fixed: minimum-scope fix requires registering BEFORE
  snapshot and either (a) accepting a partial-merge race against the
  snapshot base or (b) draining queue once after sending snapshot.
  Either choice trades one race for another. The current behaviour
  is benign — flag for awareness, not action.

### FLAG (cosmetic but worth noting) — `event_id: None` placeholder
- `backend/subscribers/strategy_ws_subscriber.py:26`: WS event frames
  carry `"event_id": None` with a comment "Phase 6: not paired with
  the DB row id; Phase 7+". Phase 7+ shipped. Pairing the WS frame
  with the persisted `sm_strategy_event.id` would let the UI
  deduplicate WS-pushed events against REST-loaded ones. Currently
  the UI dedupes by `(kind, ts)` approximate key — works but is
  hashier than an id match.
- Not fixed: closing this requires either changing the publish
  interface to carry the persisted id back, OR a second event after
  DB write — both are architecture changes.

### FLAG — Module-level asyncio.Lock in `broadcast.py`
- `backend/strategy/broadcast.py:31`: `_lock = asyncio.Lock()` at
  import time. On Python 3.10+ this lazily binds to the running loop
  on first acquire, so works in single-loop apps. Multi-loop or
  process-fork scenarios (e.g. uvicorn with multiple workers) may
  produce loop-binding issues — not currently exercised but worth a
  note when the deployment topology changes.

---

## Iteration 2 — Symbol-format consistency (scheduler / webhook / tick / services)

### FIX — Recovery does not re-subscribe ticks for open legs
- File: `backend/strategy/recovery.py` (`_recover_run`)
- Symptom: after a crash + restart, recovery rebuilds Redis state from DB
  + latest checkpoint but never calls `tick_feed.add_run_subscriptions`.
  The local `_index` in `tick_feed` stays empty for the recovered run, so
  even if broker ticks are arriving on the cache, the strategy tick
  processor's interest filter (`_index.get((exchange, symbol))`) returns
  empty and the tick is dropped. Net effect: SL / Target / Trail / Overall
  rules never fire for any recovered run.
- Plan §5.4 step 3.h: "Re-subscribe to ZMQ ticks for each open leg."
- Fix: at the end of `_recover_run`, collect every leg with
  `status == "open"` and call `tick_feed.add_run_subscriptions(run.id,
  [(exchange, symbol), ...])` once. Rejected/closed legs are terminal so
  they're correctly omitted.

### FLAG — No internal broker WS subscribe path from the strategy module
- Files: `backend/strategy/engine.py` `start_run` line 373 calls
  `tick_feed.add_run_subscriptions(...)` but NOTHING in the strategy
  module calls `_adapter.subscribe(symbols, mode)` on the WS proxy.
  `_adapter.subscribe` is only invoked from `backend/websocket_proxy/
  server.py:299`, exclusively when an external WS client sends a
  `{"action": "subscribe"}` frame.
- Symptom: a strategy run started while no human-driven UI client happens
  to be subscribed to the leg's broker WS feed receives zero ticks. Most
  exposed: scheduler-fired runs (cron triggers at 09:15 IST, no human
  online). The plan §5.2 explicitly says "subscribe to ZMQ ticks for each
  leg's symbol" — the implementation only updates the local interest map.
- Why ambiguous / not fixed in this iteration: closing this requires
  either (a) a new public function on `websocket_proxy.server` for
  internal subscribers, or (b) the strategy module reaching into
  `_adapter` (private) — both are architecture-scope changes, not
  single-file fixes. User decision needed.

### FLAG — MarketDataCache event_type filter drops mode-2 / mode-3 ticks
- File: `backend/services/market_data_cache.py` `_broadcast` line 478-490
- The cache fires CRITICAL subscribers only when broadcast `event_type`
  matches the subscriber's filter. `tick_feed.init` registers via
  `subscribe_critical(...)` which hardcodes `event_type="ltp"`. The
  broadcast maps `mode=1→"ltp"`, `mode=2→"quote"`, `mode=3→"depth"`.
- Symptom: if any concurrent UI client subscribes the same symbol in
  Quote or Depth mode, the broker WS adapter may push only mode-2 or
  mode-3 frames (some adapters consolidate to highest mode). LTP is
  still inside those frames, but the strategy subscriber is filtered out
  by the event_type check and gets nothing.
- Why ambiguous: this might be intentional given the plan's "LTP-first"
  framing, and may not happen in practice depending on each broker
  adapter's subscription consolidation rules. Surface for user.

### CLEAN — Symbol/exchange canonicalization across layers
- Engine resolver returns OpenBull canonical (`{base}{DDMMMYY}{strike}{CE|PE}`
  for options, `{base}{DDMMMYY}FUT` for futures, plain ticker for cash)
  via `_lookup_option_in_db` and `option_symbol_service`. Exchanges land
  as `NFO`/`BFO`/`MCX`/`NSE`/`BSE` per `_option_exchange_for`.
- `MarketDataCache` keys by `f"{exchange}:{symbol}"`; `tick_feed`
  indexes by `(exchange, symbol)` tuple — same form.
- `tick_processor` matches legs by strict `leg["symbol"] == symbol and
  leg["exchange"] == exchange` — fine given upstream canonicalization.
- Webhook handler and scheduler do not touch symbols directly; they
  route by `strategy_id`.

### FLAG — `stop_reason="webhook"` is not in the plan's documented enum
- File: `backend/strategy/webhook_handler.py:509` passes
  `stop_reason="webhook"` to `engine.stop_run`. The plan §4.1 lists
  allowed values: `manual` / `scheduler` / `overall_sl` / `overall_target`
  / `lock_profit` / `eod` / `expiry` / `daily_loss_limit` / `tick_stale`
  / `recovery_failed` / `error`. "webhook" is not in that list.
- `_exit_kind_for_stop` falls back to `exit_close_all` (line 556), so
  the order audit is fine — but the `stop_reason` text persisted to
  `sm_strategy_run.stop_reason` is a value the plan/UI doesn't enumerate.
- Why ambiguous: trivial to add "webhook" to the plan, OR map to
  "manual". Behavioural choice — flag.

---

## Iteration 1 — Order dispatch + lot-size handling

### FIX — Silent lot-size fallback to 1 masks bad/missing symtoken data
- File: `backend/strategy/engine.py` line 238
- Symptom: `"lotsize": r["lotsize"] or 1` silently substitutes 1 if the
  resolved leg returns lotsize=None or 0. For an options/futures leg this
  produces a 1-unit order instead of the correct lot-multiple (e.g. 75 for
  NIFTY, 25 for SENSEX) — orders likely rejected by the exchange, but if
  not, position size is wildly wrong.
- Plan contract (§15): "Lot sizes are dynamically read from
  `symtoken.lotsize` at runtime — never hardcoded anywhere in the engine
  path." The `or 1` fallback is exactly the hardcoded-default anti-pattern
  the plan forbids.
- Fix: raise `EngineError` if lotsize is missing/zero for an options or
  futures leg. Cash equity legs keep their explicit `lotsize=1` (correct
  for NSE cash).

### FLAG — Strategy `mode='live'` silently routed to sandbox by global `trading_mode`
- File: `backend/strategy/order_dispatch.py` line 56 → flows into
  `backend/services/order_service.py:75-82` (`place_order_with_auth`).
- Symptom: when the strategy router calls `dispatch_order(mode='live',
  user_id=...)`, the live branch forwards `user_id` to
  `place_order_with_auth`. That function checks `get_trading_mode_sync()`
  and, if the **global** trading mode is `sandbox`, silently re-routes the
  order to `sandbox_service.place_order(...)`. Net effect: a strategy run
  explicitly opted into live (incl. `strategy.live_enabled=True`) becomes
  a sandbox order with no warning or event. The `mode='live'` value on
  `strategy_run` and `strategy_order` rows will not reflect reality.
- Plan §5.3: "mode=`live` → `backend.broker.{name}.api.order_api.place_order_api(...)`"
  — direct broker plugin call. The plan does not describe the global
  `trading_mode` as a kill-switch over per-strategy live opt-in.
- Why ambiguous: there is a defensible reading where the global setting
  is intended as a master kill-switch covering ALL surfaces (basket
  orders, manual API, strategy). User-visible decision: either (a) drop
  `user_id` from the live-branch call so it bypasses the global override,
  or (b) keep current behaviour but emit a `run_started` event with
  effective mode + a warning banner. Not auto-fixing — needs product call.

### CLEAN — Order-constants enum usage in `engine.py`
- `pricetype` defaults to `strategy.pricetype or "MARKET"`, `product` to
  `strategy.product or "NRML"`. Both are in `VALID_PRICE_TYPES` and
  `VALID_PRODUCT_TYPES`. Action mapped via `_entry_action`/`_exit_action`
  to BUY/SELL (in `VALID_ACTIONS`). Exit orders force MARKET pricetype
  (line 439) — matches plan §5.2 "limit retry, then market" for stops,
  acceptable for v1 single-attempt exits.

---

## Areas remaining (rotate next iterations)

1. ~~Order constants misuse~~ — done (iteration 1)
2. ~~Symbol format consistency~~ — done (iteration 2)
3. ~~WebSocket subscribe / push / reconnect / dedupe~~ — done (iteration 3)
4. ~~Service-layer contract drift~~ — done (iteration 4)
5. ~~Lot-size resolution end-to-end~~ — done (iteration 5; re-verified post-fix)
6. ~~Time-zone handling~~ — done (iteration 6; clean)
7. ~~Concurrent webhook + scheduler start idempotency~~ — done (iteration 7)
8. Live-mode auth (BrokerAuth fetched FRESH per auto-exit, not cached)
9. Auto-exit / SL / TP trigger correctness (off-by-one, races, double-firing)
10. DB transaction boundaries around state transitions
11. Silent exception-swallow paths leaving the strategy inconsistent

## Open questions for user (when loop stops)
- The FLAG above: should `mode='live'` bypass the global `trading_mode`
  sandbox override, or is the global setting intentionally authoritative?
