# Signal-Mode Webhook — Operator Playbook

Copy-paste-able `curl` examples + verification scenarios for the
signal-driven strategy mode (`docs/plan/strategy-signal-mode.md`).

For batch-mode webhook examples see the existing `Webhook` tab on a
batch strategy's detail page or `docs/plan/strategy-module.md` section
11.

---

## Setup

Replace `<TOKEN>` with the plaintext token shown once when you created
the strategy (or rotated it from the **Webhook** tab on the detail
page). Replace `<HOST>` with your OpenBull host — `http://127.0.0.1:5173`
for the dev server, or your production hostname.

```bash
HOST=http://127.0.0.1:5173
TOKEN=your-strategy-token
URL=$HOST/webhook/strategy/$TOKEN
```

All examples assume a signal-mode strategy with at least one leg
(`leg_id=1`). The detail page's **Setup** tab shows each leg's actual
id.

---

## The four signal actions

### Long Entry

Opens a long position on the leg.

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}'
```

Expected on first fire: `200 {"status":"ok","action":"long_entry","leg_id":1,"run_id":<n>,"broker_order_id":"..."}`.

### Long Exit

Closes the leg's long position.

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"long_exit","leg_id":1}'
```

### Short Entry

Opens a short position. Only valid when the leg's `side` allows it
(`"short"` or `"both"`) AND the strategy's `direction` permits it
(`"short_only"` or `"both"`).

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"short_entry","leg_id":1}'
```

### Short Exit

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"short_exit","leg_id":1}'
```

---

## Symbol-keyed fallback

When TradingView's alert template can't easily inject the leg id, the
engine falls back to a `(symbol, exchange)` lookup against the
strategy's legs.

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","symbol":"RELIANCE","exchange":"NSE"}'
```

If both `leg_id` and `symbol`+`exchange` are present, `leg_id` wins.

---

## Optional `mode` override

Signal payloads can carry `"mode":"sandbox"` or `"mode":"live"`. Default
is `"sandbox"`. Live mode also requires the strategy's `live_enabled`
flag (see **Enable LIVE** on the detail page).

```bash
curl -X POST $URL \
  -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1,"mode":"sandbox"}'
```

---

## Verification scenarios

### 1. Happy path: long_entry then long_exit

```bash
# 1) Open long
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq

# 2) Same alert again within 60s -> dedupe response
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq
# expected: {"status":"ok","note":"duplicate long_entry on leg 1 within 60s - ignored"}

# 3) Close long
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_exit","leg_id":1}' | jq
```

### 2. Silent no-op: long_exit while flat

```bash
# Assuming leg 1 is flat (just stopped, or never opened):
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_exit","leg_id":1}' | jq
# expected: {"status":"ok","note":"leg is flat; long_exit requires current_side=long","leg_id":1}
```

Same response for `short_exit` on a flat leg, `long_exit` on a short
leg, etc. The webhook audit row carries `result_label="rejected_no_match"`.

### 3. Direction-blocked: short signal on long_only strategy

```bash
# Strategy created with direction="long_only":
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"short_entry","leg_id":1}' | jq
# expected: HTTP 403, {"status":"error","message":"Signal 'short_entry' blocked by strategy direction 'long_only'","leg_id":1}
```

### 4. Already-in-position idempotency

```bash
# After a successful long_entry, fire it again from a different
# alert (different IP / different webhook to bypass dedupe):
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq
# expected: {"status":"ok","note":"already long","leg_id":1,...}
```

### 5. Position conflict (deferred-to-v2 flip)

```bash
# After a successful long_entry, fire a short_entry on the same leg
# (without exiting first). v1 refuses the flip:
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"short_entry","leg_id":1}' | jq
# expected: HTTP 409, {"status":"error","message":"leg currently long; exit first via long_exit before opening the opposite side","leg_id":1}
```

To flip: fire `long_exit` first, wait for the engine event (Live tab),
then fire `short_entry`.

### 6. Intraday window enforcement

Before `entry_time` IST (default `09:35`):

```bash
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq
# expected: HTTP 200, {"status":"ok","note":"entry signals are accepted from 09:35:00 IST onward","leg_id":1}
```

Exit signals before `entry_time` are NOT blocked — operator may need
to flatten a stale overnight position.

After `exit_time` IST (default `15:15`) — all signals blocked; the
scheduler's auto-square should have already flattened everything:

```bash
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq
# expected: {"status":"ok","note":"trading window closed at 15:15:00 IST; auto-square has run","leg_id":1}
```

### 7. Bad leg_id / unknown symbol

```bash
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":999}' | jq
# expected: HTTP 400, {"status":"error","message":"No leg with id=999 on this strategy"}

curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","symbol":"NOTREAL","exchange":"NSE"}' | jq
# expected: HTTP 400, {"status":"error","message":"No leg matching symbol=NOTREAL exchange=NSE"}
```

### 8. Cross-kind action (batch vs signal)

```bash
# Sending start/stop to a signal-mode strategy:
curl -s -X POST $URL -H 'Content-Type: application/json' \
  -d '{"action":"start","mode":"sandbox"}' | jq
# expected: HTTP 400, {"status":"error","message":"action 'start' not valid for signal-mode strategy. Allowed: ['long_entry', 'long_exit', 'short_entry', 'short_exit']"}

# Sending long_entry to a batch-mode strategy:
curl -s -X POST $BATCH_URL -H 'Content-Type: application/json' \
  -d '{"action":"long_entry","leg_id":1}' | jq
# expected: HTTP 400, {"status":"error","message":"action 'long_entry' not valid for batch-mode strategy. Allowed: ['start', 'stop']"}
```

---

## Audit trail

Every accepted or rejected webhook writes one `sm_webhook_event` row,
visible on the strategy detail page's **Webhook** tab. The
`result_label` column carries the disposition:

| result_label | When |
|---|---|
| `ok` | placed, exited, or already-running idempotency |
| `rejected_token` | token unknown or oversized payload |
| `rejected_invalid_body` | non-JSON or non-object body |
| `rejected_ip` | source IP not in `webhook_ip_allowlist` |
| `rate_limited` | > 60 hits/min on this strategy |
| `rejected_invalid_action` | action not allowed for this kind |
| `rejected_invalid_mode` | mode is not 'live'/'sandbox' |
| `rejected_live_disabled` | mode=live but strategy.live_enabled=false |
| `rejected_dedupe` | identical signal within 60s |
| `rejected_cooling_off` | start within 30s of last stop (batch mode only) |
| `rejected_no_leg` | signal-mode: leg lookup failed |
| `rejected_side_mismatch` | signal-mode: leg.side rejects the action |
| `rejected_direction_blocked` | signal-mode: strategy.direction rejects |
| `rejected_no_match` | silent no-op: already in position OR flat-on-exit |
| `rejected_position_conflict` | signal-mode: opposite-side, exit first |
| `rejected_outside_window` | signal-mode: outside intraday window |
| `rejected_engine_error` | engine-side failure (broker rejection, etc.) |

The detail page's **Events** tab shows the corresponding
`sm_strategy_event` rows from the engine (entry placed, exit placed,
direction blocked, etc.).

---

## Smoke test script

A minimal one-shot script for verifying a fresh signal-mode strategy:

```bash
#!/usr/bin/env bash
set -e
HOST=${HOST:-http://127.0.0.1:5173}
TOKEN=${TOKEN:?set TOKEN to the strategy webhook token}
URL="$HOST/webhook/strategy/$TOKEN"
LEG=${LEG:-1}

echo "1. long_entry on leg $LEG"
curl -fsS -X POST "$URL" -H 'Content-Type: application/json' \
  -d "{\"action\":\"long_entry\",\"leg_id\":$LEG}" | jq

echo
echo "2. long_entry again (dedupe expected)"
curl -fsS -X POST "$URL" -H 'Content-Type: application/json' \
  -d "{\"action\":\"long_entry\",\"leg_id\":$LEG}" | jq

echo
echo "3. long_exit (close the position)"
curl -fsS -X POST "$URL" -H 'Content-Type: application/json' \
  -d "{\"action\":\"long_exit\",\"leg_id\":$LEG}" | jq

echo
echo "4. long_exit again (silent no-op - leg is flat)"
curl -fsS -X POST "$URL" -H 'Content-Type: application/json' \
  -d "{\"action\":\"long_exit\",\"leg_id\":$LEG}" | jq

echo
echo "Done. Check the Webhook tab on the detail page for the audit trail."
```
