# Getting Started — Place Your First Order Through OpenBull

| Field | Value |
|---|---|
| **Last updated** | 2026-05-11 |
| **Owner** | Platform team |
| **Audience** | New integrators — first time using OpenBull's REST + WS API |
| **Time to complete** | ~15 minutes (assuming the backend is already installed) |

This tutorial walks you from "I have OpenBull running" to "I placed an order, queried positions, and streamed live ticks." Every step uses **sandbox mode** so you can run it during or after market hours without spending real money.

If OpenBull isn't installed yet, do that first: [Quick Start in the top-level README](../README.md#quick-start).

---

## What you'll build

By the end of this tutorial you will have:

1. Generated an API key.
2. Switched the platform into sandbox mode.
3. Placed a `MARKET` order on NIFTY ATM CE.
4. Read it back from the orderbook and position book.
5. Streamed live LTP ticks over WebSocket.
6. Closed your sandbox position.

All from `curl` and Python — no UI clicks required after step 1.

---

## Step 0 — Create the admin user

If this is a fresh install:

```bash
curl -X POST http://127.0.0.1:8000/web/auth/check-setup
# → {"setup_complete": false}

# Open http://127.0.0.1:5173/setup in a browser, fill the form.
# Then http://127.0.0.1:5173/broker/config to wire one broker
# (Upstox / Zerodha / Angel / Dhan / Fyers — pick whichever you have).
```

Why through the UI? Setup and broker OAuth are one-time steps that involve secrets you don't want sitting in `curl` command history. Once those are done, everything else is API.

---

## Step 1 — Generate an API key

In the UI: `/apikey` → click "Generate" → copy the key (shown **once** — the database stores only an Argon2id hash, so save it somewhere safe).

```bash
export OPENBULL_API_KEY=4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6
```

Sanity check:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ping \
  -H "Content-Type: application/json" \
  -d "{\"apikey\": \"$OPENBULL_API_KEY\"}"
# → {"status": "success", "message": "pong"}
```

If you get `{"status": "error", "message": "Invalid API key"}`, the key didn't copy cleanly. Regenerate.

---

## Step 2 — Switch to sandbox mode

```bash
curl -b "access_token=<your_session_cookie>" \
  -X POST http://127.0.0.1:8000/web/trading-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "sandbox"}'
```

Or just flip the topbar switch in the UI — sandbox mode is a global, single-row setting (`app_settings` table). The UI tints amber when sandbox is active.

**What changes**: every `/api/v1` order/info call still works exactly the same — same shape, same response, same fields — but the *fills* are simulated. Sandbox capital, leveraged margins, T+1 settlement, scheduled squareoff at 15:15 IST — all the lifecycle pieces of live trading, in a sealed environment that can't touch the broker.

---

## Step 3 — Find an ATM NIFTY call

You need an OpenBull symbol to place an order. Use `optionsymbol` to resolve "ATM CE on next expiry":

```bash
curl -X POST http://127.0.0.1:8000/api/v1/optionsymbol \
  -H "Content-Type: application/json" \
  -d "{
    \"apikey\": \"$OPENBULL_API_KEY\",
    \"underlying\": \"NIFTY\",
    \"exchange\": \"NSE_INDEX\",
    \"expiry_date\": \"28APR26\",
    \"offset\": \"ATM\",
    \"option_type\": \"CE\"
  }"
```

Response (truncated):

```json
{
  "status": "success",
  "symbol": "NIFTY28APR2624250CE",
  "exchange": "NFO",
  "strike": 24250.0,
  "lotsize": 75,
  "underlying_ltp": 24231.30
}
```

Two things to note:

- `symbol` is the OpenBull canonical option symbol you'll use in every subsequent call.
- `lotsize` is the contract size — 75 here is the current NIFTY F&O lot. Your `quantity` must be a multiple of this.

Use the next expiry the master has. To list available expiries:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/expiry \
  -H "Content-Type: application/json" \
  -d "{
    \"apikey\": \"$OPENBULL_API_KEY\",
    \"symbol\": \"NIFTY\",
    \"exchange\": \"NFO\",
    \"instrumenttype\": \"OPT\"
  }"
```

---

## Step 4 — Place a MARKET order

```bash
curl -X POST http://127.0.0.1:8000/api/v1/placeorder \
  -H "Content-Type: application/json" \
  -d "{
    \"apikey\": \"$OPENBULL_API_KEY\",
    \"strategy\": \"first-tutorial\",
    \"symbol\": \"NIFTY28APR2624250CE\",
    \"exchange\": \"NFO\",
    \"action\": \"BUY\",
    \"quantity\": \"75\",
    \"pricetype\": \"MARKET\",
    \"product\": \"NRML\"
  }"
```

Response:

```json
{"status": "success", "orderid": "260511143052123456"}
```

The order id is sandbox-generated (`YYMMDD-microsecond` shape). In live mode, the broker generates it.

What just happened, end-to-end:

1. API key was Argon2-verified against `api_keys` (cache miss on first call, then Redis-cached for 15 min).
2. `validate_order_data` checked the order shape against `backend/utils/constants.py`.
3. `place_order_with_auth` saw `trading_mode == "sandbox"` and dispatched to `backend/sandbox/order_manager`.
4. The sandbox engine validated margin (`fund_manager`), created an `sandbox_orders` row, and registered the order on the tick fanout for `NIFTY28APR2624250CE`.
5. On the next matching tick (MARKET orders fill at the cached LTP), the order moved to `complete`, a row landed in `sandbox_trades`, and the position was opened.

---

## Step 5 — Read it back

### Orderbook

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orderbook \
  -H "Content-Type: application/json" \
  -d "{\"apikey\": \"$OPENBULL_API_KEY\"}"
```

You should see your order with `order_status: "complete"`.

### Position book

```bash
curl -X POST http://127.0.0.1:8000/api/v1/positions \
  -H "Content-Type: application/json" \
  -d "{\"apikey\": \"$OPENBULL_API_KEY\"}"
```

One row with `netqty: 75` and a `pnl` that updates as the live tick moves.

### Open position (single-symbol)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/openposition \
  -H "Content-Type: application/json" \
  -d "{
    \"apikey\": \"$OPENBULL_API_KEY\",
    \"symbol\": \"NIFTY28APR2624250CE\",
    \"exchange\": \"NFO\",
    \"product\": \"NRML\"
  }"
```

```json
{"status": "success", "data": {"quantity": 75}}
```

---

## Step 6 — Stream live ticks

Open a WebSocket to the proxy. Here in Python:

```python
import json
import websocket

API_KEY = "<your_key>"

def on_open(ws):
    ws.send(json.dumps({"action": "authenticate", "api_key": API_KEY}))

def on_message(ws, raw):
    msg = json.loads(raw)
    if msg.get("type") == "auth" and msg.get("status") == "success":
        ws.send(json.dumps({
            "action": "subscribe",
            "symbols": [{"symbol": "NIFTY28APR2624250CE", "exchange": "NFO"}],
            "mode": "LTP",
        }))
    elif msg.get("type") == "market_data":
        d = msg["data"]
        print(f"{msg['symbol']}: ltp={d['ltp']} change={d['change']:+.2f}")

ws = websocket.WebSocketApp("ws://127.0.0.1:8765",
                            on_open=on_open, on_message=on_message)
ws.run_forever()
```

Run it. During market hours you'll see LTP ticks at up to 20 per second per symbol (50 ms throttle).

Subscribe to `QUOTE` instead of `LTP` to get OHLCV + OI + total buy/sell qty on the same tick. Subscribe to `DEPTH` to get the 5-level order book on top of that. The proxy's mode hierarchy means one subscribe with the highest mode you want delivers everything below it too.

---

## Step 7 — Close the position

```bash
curl -X POST http://127.0.0.1:8000/api/v1/closeposition \
  -H "Content-Type: application/json" \
  -d "{\"apikey\": \"$OPENBULL_API_KEY\"}"
```

```json
{"status": "success", "message": "All Open Positions SquaredOff"}
```

This places a MARKET sell on every open position. In sandbox, it's instant. In live, it depends on the broker.

Alternatively, place a manual reversing order:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/placeorder \
  -d "{...same as before but action=SELL...}"
```

---

## Where to go from here

| You want to… | Read |
|---|---|
| Design a multi-leg strategy (Iron Condor, etc.) without manual leg math | [optionsmultiorder](./api/order-management/optionsmultiorder.md), or open `/tools/strategybuilder` in the UI for the visual designer |
| Compute live Greeks for a strategy | [optiongreeks](./api/options-services/optiongreeks.md), or `/web/strategybuilder/snapshot` for a one-shot multi-leg snapshot |
| Place a basket of correlated trades | [basketorder](./api/order-management/basketorder.md) — BUY-before-SELL ordering enforced server-side |
| Pre-check margin before placing a leg | [margin](./api/account-services/margin.md) — hedge-aware basket margin |
| Build a historical IV / OI / straddle dashboard | [Analytics endpoints](./api/README.md#analytics-tools) — 7 endpoints powering `/tools/*` |
| Switch to a different broker | Flip `VALID_BROKERS` in `.env`, complete OAuth on the new broker. Your client code doesn't change. |
| Switch to live trading | Flip the topbar Live/Sandbox switch. Same code, real money. **Test thoroughly first.** |
| Understand how it all fits together | [ARCHITECTURE.md](./design/ARCHITECTURE.md) |
| Diagnose a problem | [RUNBOOK.md](./design/RUNBOOK.md) |

---

## A note on going live

When you flip Live ↔ Sandbox at the top of the UI, your code keeps working — that's the design — but the **fills come from the broker now, not the simulator**. A few mental checks before flipping:

- **Margin** in live is broker-computed and broker-blocked. Sandbox margin is OpenBull-computed against your simulated capital. They will differ.
- **Slippage and partial fills** are real in live; sandbox's MARKET orders fill at one cached LTP.
- **Errors are real**: a rejected order, a revoked token, a maintenance window. The same APIs surface them, but the failure modes are richer.
- **Rate limits**: brokers enforce their own — 1–10 orders/second is typical. SlowAPI handles per-OpenBull limits, brokers handle theirs on top.

The shipped sandbox mode is *behaviourally* identical to live, but the *consequences* are not. Develop in sandbox, paper-trade your strategy in sandbox during real market hours, then go live with small size first.

---

## See also

- [API Reference](./api/README.md) — every endpoint, with a Swagger UI at `/docs` on a running backend
- [Product Overview](./PRODUCT.md) — what OpenBull is and what makes it different
- [Operations Runbook](./design/RUNBOOK.md) — when something breaks
