# Signal-Mode Strategies — Design

**Status:** design only; build in progress
**Owner:** rajandran
**Coexists with:** the batch-mode strategy module (`docs/plan/strategy-module.md`)
**Initiated:** 2026-05-13

---

## 1. Why

The v1 strategy module is **batch-mode**: a single `start` fires every leg's entry as one transaction, and a single `stop` exits everything. That fits multi-leg options spreads (iron condors, strangles, etc.) where you want all legs alive together.

It does NOT fit TradingView-driven equity trading, where:
- An alert fires one signal at a time (`Long Entry`, `Long Exit`, `Short Entry`, `Short Exit`).
- A strategy may hold positions in multiple unrelated symbols (a 5-stock portfolio).
- The user wants raw share quantity, not "lots × lotsize".
- The user wants per-direction control (long-only intraday, short-only intraday, or both).

Signal mode is a new strategy kind alongside batch mode — same DB tables, same engine machinery for state/orders/recovery, but a different webhook protocol and a different leg shape.

---

## 2. Confirmed design decisions (from user, 2026-05-13)

| Decision | Choice |
|---|---|
| Coexistence | **Coexist** — new `strategy_kind` column (`"batch"` default, `"signal"` opt-in). Existing strategies untouched. |
| Leg shape | **One leg per symbol** — each leg is `{ symbol, exchange, side, qty }`. Webhook signal targets a specific `leg_id`. |
| Mismatched signal | **Silent no-op** — `long_exit` on a flat leg returns `200 {status:"ok", note:"no_matching_position"}` and writes one `sm_webhook_event` row. |

---

## 3. Data model deltas

### 3.1 `sm_strategy` (additive only — batch rows unchanged)

| New column | Type | Notes |
|---|---|---|
| `strategy_kind` | `text` not null default `'batch'` | `'batch'` | `'signal'` |
| `direction` | `text` not null default `'both'` | `'long_only'` / `'short_only'` / `'both'` — gates incoming signals for signal-mode strategies; ignored for batch |

Batch-mode rows get `strategy_kind='batch'` via the DB default — no migration of data, no UI change.

### 3.2 Per-leg shape (in the `legs` jsonb)

**Batch mode** keeps its existing shape (see `strategy-module.md` §4.1.1).

**Signal mode** uses:

```jsonc
{
  "id": 1,
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "side": "long" | "short" | "both",      // which signals can hit this leg
  "qty": 100,                              // raw shares for cash; lot-multiple for FUT
  "segment": "cash" | "futures",           // no options in signal mode v1
  "expiry": "current" | "next" | null,     // only when segment=futures
  "target_pts": null,
  "sl_pts": null,
  "trail": {"x": 0, "y": 0}
}
```

Notes:
- `qty` is the **absolute** quantity sent to the broker (or sandbox). The lotsize multiplier is a UX detail for the wizard, not a runtime field.
- `option_type`, `strike_mode`, `atm_offset`, `strike_value` are **not used** in signal mode. Multi-leg option spreads stay in batch mode.
- `side: "both"` means the leg accepts both `long_*` and `short_*` signals (typical for intraday equity). `"long"` or `"short"` legs reject the wrong-side signals.

---

## 4. Webhook protocol — signal mode

Endpoint stays the same: `POST /webhook/strategy/{token}`. The body changes.

### 4.1 Payload shape

```json
{
  "action": "long_entry" | "long_exit" | "short_entry" | "short_exit",
  "leg_id": 1
}
```

OR, fall back to symbol lookup (resolved server-side to a leg by `(symbol, exchange)`):

```json
{
  "action": "long_entry",
  "symbol": "RELIANCE",
  "exchange": "NSE"
}
```

If both `leg_id` and `symbol` are present, `leg_id` wins.

### 4.2 Action validation

| Strategy `strategy_kind` | Allowed actions |
|---|---|
| `batch` | `start`, `stop` only — the four signal actions return `400 rejected_invalid_action` |
| `signal` | `long_entry`, `long_exit`, `short_entry`, `short_exit` only — `start`/`stop` return `400 rejected_invalid_action` |

The router is the same; the action validator branches on `strategy.strategy_kind`.

### 4.3 Direction gate

Before dispatch, check `strategy.direction`:

| `direction` | Allowed entry signals | Allowed exit signals |
|---|---|---|
| `long_only` | `long_entry` | `long_exit` |
| `short_only` | `short_entry` | `short_exit` |
| `both` | both entry actions | both exit actions |

Rejected by direction → `403 rejected_direction_blocked`, with a clear message.

### 4.4 Mismatched-signal silent no-op

For `*_exit` actions, after resolving the leg, check its current state in Redis:

| Signal | Leg state in Redis | Outcome |
|---|---|---|
| `long_exit` | open long | proceed: place SELL exit order |
| `long_exit` | flat, short, or rejected entry | **silent no-op** — record event, return `200 {status:"ok", note:"no_matching_position"}` |
| `short_exit` | open short | proceed: place BUY-to-cover exit order |
| `short_exit` | flat, long, or rejected | silent no-op |

For `*_entry` actions, check if the leg is **already** in the requested direction:

| Signal | Leg state | Outcome |
|---|---|---|
| `long_entry` | flat | proceed |
| `long_entry` | already long | **silent no-op** — `note: "already_long"` |
| `long_entry` | currently short | **two-step**: square the short first, then open long (single atomic dispatch from engine's perspective) |
| (mirrored for short_entry) | | |

The two-step flip is only when `direction="both"` and the leg's `side="both"`. If the leg's `side="long"`, a `short_entry` signal is rejected by `side`, not by leg state.

---

## 5. Engine surface

### 5.1 New entry points

```
async def enter_leg(strategy, leg_id, side, mode, broker, auth_token, config):
    # idempotent if already in the requested side
    # auto-flips if opposite side and configuration allows
    # respects strategy.direction
    # places one entry order, records sm_strategy_order(kind="entry"),
    # marks leg.status="open" + leg.side in Redis state

async def exit_leg_by_signal(strategy, leg_id, side, ...):
    # silent no-op if leg isn't in the requested side
    # places one exit order, records sm_strategy_order(kind="exit_signal"),
    # marks leg.status="closed" in Redis state
```

These replace `engine.start_run` / `engine.stop_run` for signal-mode strategies. Batch mode keeps its existing API.

### 5.2 Lifecycle differences

| | Batch | Signal |
|---|---|---|
| Run row | one per `start` → `stop` cycle | **one per strategy day** — created on first signal of the trading day, finalized at EOD or auto-stop time |
| `current_run_id` | set on start, cleared on stop | set on first signal, cleared at auto-stop or manual close-all |
| Legs at run start | every leg's entry placed in one batch | legs are **inactive** until a signal opens them |
| Order kinds | `entry`, `exit_sl`, `exit_target`, `exit_close_all`, etc. | adds `exit_signal` |
| Auto-exit at `exit_time` | square all legs | square all open legs (same effect, different trigger language) |

### 5.3 Intraday window enforcement

For `strategy_type="intraday"`:
- Before `entry_time` IST: signal-mode `*_entry` actions return `200 {status:"ok", note:"outside_entry_window"}`. Exit signals proceed normally.
- After `exit_time` IST: all signals return `200 {status:"ok", note:"outside_trading_window"}`. The scheduler's auto-exit job runs at `exit_time` and closes any still-open legs.

For `strategy_type="positional"`: no window check.

### 5.4 Per-leg risk (SL / Target / Trail) in signal mode

Same machinery as batch mode — the tick processor runs `risk_evaluator.evaluate_leg` per open leg. Triggered exits use `kind="exit_sl"` / `exit_target` / `exit_trail`. The leg's `side` informs the SL direction (long leg → SL below entry, short leg → SL above entry).

---

## 6. Frontend layout

### 6.1 Wizard — kind picker

At the top of `/strategy/new`, above the universe tabs:

```
Strategy kind: [ Multi-leg (batch) ] [ Signal-driven (TradingView) ]
```

When `signal` is picked:
- Universe tabs reduced to: Stocks – Cash / F&O, Commodities (MCX). Options tabs hidden (signal mode doesn't do option spreads).
- "Underlying" picker hidden — each leg has its own symbol.
- A multi-symbol leg builder replaces the current per-underlying leg builder. Each leg row: symbol search, exchange (auto-resolved), side (long / short / both), qty.
- New "Direction" radio: Long Only / Short Only / Both.
- Webhook tab preview shows the four-action curl examples.

### 6.2 Detail page — kind-aware

- Header badge: `Signal mode` next to the mode (sandbox/live) badge.
- Setup tab: renders the multi-symbol leg table instead of the option-spread table.
- Webhook tab: payload example uses `long_entry` / `long_exit` / etc. plus a leg-id reference.
- Live tab: per-leg states reflect the dynamic open/close that signals drive — leg can be `flat` (configured, never opened), `open` (entry filled), `closed` (entry+exit filled).

---

## 7. Out of scope for this build

- Option-spread signal strategies (mixing signal mode with option legs)
- Backtesting
- Per-signal position sizing (qty stays fixed per leg)
- Pyramiding (multiple stacked entries on the same leg without exit in between)
- Net-position reconciliation across strategies for the same symbol

---

## 8. Build status

| # | Slice | Commit | Status |
|---|---|---|---|
| 1 | Design doc | (this commit) | done |
| 2 | Backend schema + migration + Pydantic | — | pending |
| 3 | Webhook handler — signal actions + leg lookup | — | pending |
| 4 | Engine — `enter_leg` / `exit_leg_by_signal` | — | pending |
| 5 | Engine — direction gating | — | pending |
| 6 | Engine — intraday window enforcement | — | pending |
| 7 | Frontend types | — | pending |
| 8 | Frontend wizard — kind toggle + signal-mode leg builder | — | pending |
| 9 | Frontend detail — kind-aware Setup tab + webhook examples | — | pending |
| 10 | E2E sanity — curl examples, sandbox dispatch verified | — | pending |

---

## 9. Open questions

(Resolve here before the slice that needs the answer.)

- **Multi-symbol intraday auto-exit** — when `exit_time` fires for a signal-mode strategy with 5 open legs, do exits go out in parallel or sequentially? Sequential preserves audit ordering but is slower. Decision needed in slice 6.
- **Sandbox mode and per-leg symbol** — sandbox's `place_order` validates symbol/lot/tick. For signal-mode cash legs the symbol differs per leg; need to confirm each leg's symbol resolves in `symtoken` before the strategy is allowed to save. Decision needed in slice 8 (wizard validation).
- **Fill-propagation gap** — the iteration-9 audit finding (entry_avg never populated, so SL/Target/Trail never fire) applies to signal mode too. The build assumes that gap is fixed before signal-mode goes live, OR signal-mode skips per-leg risk eval for v1 and relies solely on user-fired exit signals. Decision needed in slice 4.

---

## 10. Audit-as-you-build rules

Each slice ends with a self-audit pass against the same classes of bug found in the batch-mode audit:

- Hardcoded lotsize fallbacks
- TOCTOU on state transitions (use `SELECT ... FOR UPDATE`)
- Fill propagation
- Time-zone slips (UTC store / IST wire / APScheduler `Asia/Kolkata`)
- Service contract drift (use documented entry points, not raw broker plugins)
- Silent exception swallow paths
- Hardcoded enum strings that should reference the constants in `backend/utils/constants.py`

Findings get logged inline in the commit message so the diff and the audit travel together.
