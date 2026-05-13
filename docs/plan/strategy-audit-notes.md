# Strategy Module Audit — Running Scratchpad

Ralph loop validating `backend/strategy/*` against `docs/plan/strategy-module.md`
and the design docs (`docs/design/order-constants.md`, `symbol-format.md`,
`websockets-format.md`, `SERVICES.md`). One iteration per minute.

Conventions:
- **FIX (commit)** — bug confirmed, fix landed locally, no push.
- **FLAG (skip)** — ambiguous or behavioural — surfaced for user; not fixed.
- **CLEAN** — area checked, nothing actionable.

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
4. Service-layer contract drift (modify/cancel signatures, response shape)
5. Lot-size resolution end-to-end (partially done — re-verify after fix)
6. Time-zone handling (UTC store / IST wire / APScheduler `Asia/Kolkata`)
7. Concurrent webhook + scheduler start idempotency
8. Live-mode auth (BrokerAuth fetched FRESH per auto-exit, not cached)
9. Auto-exit / SL / TP trigger correctness (off-by-one, races, double-firing)
10. DB transaction boundaries around state transitions
11. Silent exception-swallow paths leaving the strategy inconsistent

## Open questions for user (when loop stops)
- The FLAG above: should `mode='live'` bypass the global `trading_mode`
  sandbox override, or is the global setting intentionally authoritative?
