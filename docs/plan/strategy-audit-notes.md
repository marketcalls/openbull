# Strategy Module Audit — Running Scratchpad

Ralph loop validating `backend/strategy/*` against `docs/plan/strategy-module.md`
and the design docs (`docs/design/order-constants.md`, `symbol-format.md`,
`websockets-format.md`, `SERVICES.md`). One iteration per minute.

Conventions:
- **FIX (commit)** — bug confirmed, fix landed locally, no push.
- **FLAG (skip)** — ambiguous or behavioural — surfaced for user; not fixed.
- **CLEAN** — area checked, nothing actionable.

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

1. ~~Order constants misuse~~ — done (this iteration)
2. Symbol format consistency (scheduler/webhook/tick-processor ↔ services)
3. WebSocket subscribe/mode-1/2/3 parsing + reconnect + dedupe
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
