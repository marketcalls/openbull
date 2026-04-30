# Broker Integration Guide

How to add a new broker plugin to OpenBull. Five plugins ship today (Upstox, Zerodha, Angel One, Dhan, Fyers) and follow the same shape — copy the closest one and adapt.

## Directory Structure

Each broker lives in `backend/broker/{name}/` with the following layout:

```
backend/broker/{name}/
├── __init__.py
├── plugin.json                 # Manifest read by the plugin loader
├── api/
│   ├── __init__.py
│   ├── auth_api.py             # OAuth / credential exchange → access token
│   ├── order_api.py            # place / modify / cancel + orderbook / tradebook / positions / holdings
│   ├── funds.py                # account funds
│   ├── data.py                 # quotes / multiquotes / depth / history (+ TIMEFRAME_MAP)
│   └── margin_api.py           # pre-trade margin calculator
├── mapping/
│   ├── __init__.py
│   ├── transform_data.py       # OpenBull ↔ broker order field mapping
│   ├── order_data.py           # response → OpenBull (token ↔ symbol resolution)
│   └── margin_data.py          # margin request/response normalisation
├── streaming/
│   ├── __init__.py
│   └── {name}_adapter.py       # WebSocket → ZMQ adapter (BaseBrokerAdapter subclass)
└── database/
    ├── __init__.py
    └── master_contract_db.py   # Master-contract download + symtoken upsert
```

The plugin loader scans `backend/broker/*/plugin.json` at startup. Add the new broker's name to `VALID_BROKERS` in `.env` to enable it.

## Step-by-Step

### 1. Manifest (`plugin.json`)

```json
{
    "name": "examplebroker",
    "display_name": "Example Broker",
    "description": "Example Broker integration for OpenBull",
    "version": "1.0",
    "supported_exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX", "NSE_INDEX", "BSE_INDEX"],
    "broker_type": "IN_stock",
    "oauth_type": "auth_code",
    "auth_url_template": "https://auth.examplebroker.com/connect?client_id={api_key}&redirect_uri={redirect_url}&response_type=code"
}
```

Key fields:

| Field | Notes |
|-------|-------|
| `name` | Lowercase identifier. Must match the directory name. |
| `display_name` | Shown in `/broker/select`. |
| `supported_exchanges` | OpenBull-canonical exchange codes the broker supports (see [order-constants.md](./order-constants.md)). |
| `broker_type` | `"IN_stock"` for Indian equity/F&O brokers. Tells the master-contract scheduler which downloads to run. |
| `oauth_type` | One of `request_token`, `auth_code`, `credentials`, `token_id`. Drives the auth flow in `/broker/select`. |
| `auth_url_template` | Pre-login URL the user is redirected to. `{api_key}` and `{redirect_url}` are substituted at runtime; for `credentials` flows leave empty. |

### 2. Auth (`api/auth_api.py`)

One required entry point:

```python
def authenticate_broker(
    request_token: str | None,
    config: dict,
) -> tuple[str | None, str | None]:
    """Exchange the OAuth request_token for a long-lived access_token.

    Returns:
        (auth_token, error_message). One is None.
    """
```

`config` is the per-user `BrokerConfig` row — contains `api_key`, `api_secret`, plus any broker-specific fields (e.g. Angel's TOTP secret, Dhan's client_id). The OAuth callback handler stores the returned `auth_token` encrypted in the user's `auth` row.

For the `credentials` flow (Angel) `request_token` will be `None` — read everything from `config`. For `token_id` (Dhan) the token is static — return it as-is.

### 3. Orders (`api/order_api.py`)

Required entry points (every plugin re-exports the same names):

```python
def place_order_api(data: dict, auth: str) -> tuple[bool, str, dict]:
def modify_order(data: dict, auth: str) -> tuple[bool, str, dict]:
def cancel_order(orderid: str, auth: str) -> tuple[bool, dict]:

def get_order_book(auth: str) -> dict:           # all orders for the day
def get_trade_book(auth: str) -> dict:           # all trades for the day
def get_positions(auth: str) -> dict:            # current positions
def get_holdings(auth: str) -> dict:             # portfolio holdings
```

`data` arrives in OpenBull's canonical order shape. Use `mapping/transform_data.py` to translate to the broker's field names — exchange codes (`NFO` → `NSE_FO` etc.), product types (`NRML` → broker name), pricetype, action, symbol.

Output of read endpoints (`get_*`) should be the broker's raw response. The corresponding service (`backend/services/orderbook_service.py` etc.) calls `mapping/order_data.py` to normalise into OpenAlgo shape:

```python
def map_order_data(broker_data: dict | list) -> list[dict]:
    """Normalise broker orderbook rows to OpenBull/OpenAlgo schema."""
```

### 4. Funds (`api/funds.py`)

```python
def get_funds(auth: str) -> tuple[bool, dict]:
    """Returns (success, {availablecash, utiliseddebits, ...})."""
```

Output shape is fixed: `availablecash`, `collateral`, `m2munrealized`, `m2mrealized`, `utiliseddebits`. Convert the broker's keys here, not in the service layer.

### 5. Market Data (`api/data.py`)

```python
def get_quotes(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
def get_multi_quotes(symbols_list: list[dict], auth_token: str, config: dict | None = None) -> list[dict]:
def get_market_depth(symbol: str, exchange: str, auth_token: str, config: dict | None = None) -> dict:
def get_history(
    symbol: str, exchange: str, interval: str,
    start_date: str, end_date: str,
    auth_token: str, config: dict | None = None,
) -> list[dict]:

# Required module-level constant:
TIMEFRAME_MAP: dict[str, str] = {
    "1m": "minute",   # broker-specific values
    "5m": "5minute",
    ...
}
```

`TIMEFRAME_MAP` is what `/api/v1/intervals` reads to advertise supported intervals to clients.

The history list items must include `timestamp` (Unix seconds), `open`, `high`, `low`, `close`, `volume`, and **`oi`** (Open Interest — set to `0` if the broker doesn't supply it for the instrument). The Strategy Builder's Multi-Strike OI tab depends on this column.

Quote responses should always include `ltp`, `open`, `high`, `low`, `prev_close`, `volume`, `oi`, `bid`, `ask`, `bid_qty`, `ask_qty`. Missing values → `0`, never `None`.

### 6. Margin (`api/margin_api.py`)

```python
def calculate_margin_api(positions: list[dict], auth: str) -> tuple[bool, dict]:
```

Input is OpenBull's canonical `MarginPosition` list (symbol, exchange, action, quantity, product, pricetype, price). Output should normalise to:

```python
{
    "status": "success",
    "data": {
        "total_margin_required": <number>,   # net of hedge benefits
        "span_margin": <number>,
        "exposure_margin": <number>,
        "margin_benefit": <number>,          # how much hedge saved vs sum of standalones
    }
}
```

The Strategy Builder's `PositionsPanel` reads `total_margin_required` directly — defined-risk strategies should reflect the hedge benefit.

### 7. Master Contract (`database/master_contract_db.py`)

Required entry point:

```python
def master_contract_download(auth_token: str, config: dict | None = None) -> tuple[bool, str]:
    """Download the broker's instrument master, parse it, and bulk-upsert into symtoken."""
```

Schema columns to populate (the `symtoken` table):

| Column | Meaning |
|--------|---------|
| `symbol` | OpenBull-canonical symbol (e.g. `NIFTY28APR2624250CE`) |
| `brsymbol` | Broker's native ticker |
| `name` | **Underlying name** — `NIFTY`, `BANKNIFTY`, `RELIANCE INDUSTRIES`. **NOT the per-contract description.** See "Common pitfalls" below. |
| `exchange` | OpenBull canonical (`NSE`, `NFO`, `NSE_INDEX`, …) |
| `brexchange` | Broker's native exchange code |
| `token` | Broker's instrument token (string) |
| `expiry` | `DD-MMM-YY` uppercase (`28-APR-26`) — empty for non-F&O |
| `strike` | Numeric — 0 for non-options |
| `lotsize` | Contract size (1 for equities) |
| `tick_size` | Price granularity in rupees (e.g. `0.05` for NFO) |
| `instrumenttype` | `EQ`, `CE`, `PE`, `FUT`, `INDEX` |

The download is idempotent — every run replaces all rows. Run on demand from `/broker/config` or via the scheduled job (NSE master refreshes once daily).

### 8. Streaming Adapter (`streaming/{name}_adapter.py`)

Subclass `BaseBrokerAdapter` (`backend/websocket_proxy/base_adapter.py`):

```python
from backend.websocket_proxy.base_adapter import BaseBrokerAdapter

class ExampleBrokerAdapter(BaseBrokerAdapter):
    def connect(self) -> None:
        """Open broker WS, authenticate, store the connection."""
        self.setup_zmq()
        # ... open broker WS, register on_message callback ...
        self._running = True

    def subscribe(self, symbols: list[dict], mode: int) -> None:
        """symbols: [{symbol, exchange}, ...]
        mode: 1=LTP, 2=QUOTE, 3=DEPTH.
        Translate to broker tokens + send subscribe frame."""

    def unsubscribe(self, symbols: list[dict], mode: int) -> None: ...
    def disconnect(self) -> None: ...

    # Internal: when a tick arrives, normalise + publish:
    def _on_message(self, raw):
        for tick in self._parse(raw):
            self.publish(
                topic=f"{tick['exchange']}_{tick['symbol']}_{tick['mode']}",
                data=tick,
            )
```

Topic format on the ZMQ bus: `{EXCHANGE}_{SYMBOL}_{MODE}` — e.g. `NSE_RELIANCE_LTP`, `NSE_INDEX_NIFTY_QUOTE`. The proxy SUBs by exact topic and forwards to subscribed clients.

Tick payload schema (every adapter must produce this):

```json
{
    "type": "market_data",
    "exchange": "NSE",
    "symbol": "RELIANCE",
    "mode": "LTP",
    "data": {
        "ltp": 1234.5,
        "ltt": 1716540000,
        "ltq": 100,
        "cp": 1230.0,
        "change": 4.5,
        "change_pct": 0.366
    }
}
```

For QUOTE add `open / high / low / close / volume / oi / atp / total_buy_qty / total_sell_qty`. For DEPTH add `bids` / `asks` arrays of 5 levels each.

The streaming adapter is registered with the WS proxy in `backend/websocket_proxy/proxy_server.py` — add the new broker name to the `_BROKER_ADAPTERS` map.

### 9. Register the broker

Add the name to `VALID_BROKERS` in `.env`:

```env
VALID_BROKERS=upstox,zerodha,angel,dhan,fyers,examplebroker
```

Restart the backend. The plugin loader will pick up the new directory on next startup. If `auth_url_template` is set, `/broker/select` will show the new broker as an option.

## Common Pitfalls

### `symtoken.name` must be the underlying ticker, not the contract description

Some broker masters (Fyers' `Symbol Details`) populate per-contract descriptions like `"NIFTY 02 Jun 26 18650 CE"`. If you set `name = "Symbol Details"` for F&O rows, `get_option_underlyings()` will GROUP BY name and produce **one row per contract** in the underlying picker — visible to the user as garbage entries.

✅ **Right:** for F&O rows, `df["name"] = df["Underlying symbol"]` (the bare ticker — `NIFTY`, `RELIANCE`).
❌ **Wrong:** `df["name"] = df["Symbol Details"]` for F&O rows.

For cash-market rows (EQ / INDEX), `Symbol Details` is fine — there it's the company / index name.

### Lot size must come from the master, not be hardcoded

Lot sizes change quarterly in NSE F&O. Read from the master row's `lotsize` column at order-build time. Don't fall back to a per-symbol dict.

### Retry transient broker 429s in-process

Brokers throttle aggressively (especially Fyers and Upstox). For idempotent reads (history, quotes, multiquotes), wrap the broker call in a small retry loop with exponential backoff. **Don't** retry order placement.

```python
for attempt in range(3):
    resp = _api_get(url, ...)
    if resp.status_code != 429:
        break
    time.sleep(2 ** attempt)
```

### History endpoint must return `oi` per candle

Strategy Chart and Multi-Strike OI tabs depend on this. If the broker doesn't provide OI in history, set `oi = 0` — don't omit the key.

### F&O OI bucket caps

Some brokers cap quotes-per-call (e.g. Fyers caps OI buckets at 100 symbols per request). Services like `gex_service` and `max_pain_service` have already shrunk their chain windows to fit; new analytics services should follow the same convention if they fan out > 100 symbols.

### Sandbox EOD rollover

If your broker plugin returns `expiry` strings in a non-standard format, `backend/sandbox/expiry_handler.py` may not detect F&O contracts correctly at end-of-day. The expected format is `DD-MMM-YY` uppercase (`28-APR-26`) — match this in the master.

### Symbol round-trip

Order placement uses OpenBull symbol → broker symbol. Order responses come back with the broker's symbol — the service layer needs `mapping/order_data.py` to map back. Make sure `_get_oa_symbol(brsymbol, exchange)` and `_get_br_symbol(symbol, exchange)` both work as inverses; an asymmetric mapping causes orderbook entries to display incorrectly.

### TIMEFRAME_MAP is broker-specific

The `/api/v1/intervals` endpoint reads `TIMEFRAME_MAP.keys()`. If the broker doesn't support a particular interval (e.g. Zerodha indices on `1m`), simply omit the key — don't return a value the broker will reject.

### Tick size matters for the basket-order dialog

`BasketOrderDialog` snaps prices to `tick_size` on blur. If your master sets `tick_size = 0`, the dialog will show floating-point drift. Always populate it from the broker (default `0.05` for NFO if missing).

## Reference Implementations

When in doubt, read the closest existing broker:

| Broker | OAuth flow | Streaming | Useful as reference for |
|--------|------------|-----------|--------------------------|
| Zerodha | request_token | KiteTicker binary | Token-based binary streaming, basket margin |
| Upstox | auth_code | Protobuf | Modern OAuth + protobuf streaming |
| Fyers | auth_code | HSM binary | Custom binary parser + retry-on-429 patterns |
| Angel | credentials + TOTP | SmartStream binary | TOTP / non-OAuth flow |
| Dhan | static token | Dhan binary | Token-only auth, simpler flow |

## Testing the New Broker

1. Run the master-contract download from `/broker/config`. Verify rows in `symtoken` look right (`SELECT exchange, COUNT(*) FROM symtoken GROUP BY exchange`).
2. From the API key page, generate a key. Hit `/api/v1/quotes` for `RELIANCE` on `NSE` — should return `ltp` etc.
3. Hit `/api/v1/optionchain` for `NIFTY` on `NFO` — should return `chain[]` with CE/PE per strike. Verify ATM IV by calling `/api/v1/ivsmile`.
4. Place a test order via `/api/v1/placeorder` (sandbox mode is your friend here — toggle it on first).
5. WebSocket: `wscat -c ws://127.0.0.1:8765`, send the auth message, subscribe to `RELIANCE@NSE` LTP — verify ticks arrive within ~2 seconds.
6. Strategy Builder: pick a 2-leg template (Bull Call Spread), confirm in `TemplateConfigDialog`, hit Refresh Snapshot — Greeks should populate, payoff curve should draw.

If any of those fail, check `logs/openbull.log` first — request errors are logged with the broker's response body and a request id.
