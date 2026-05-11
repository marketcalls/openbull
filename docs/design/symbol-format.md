# Symbol Format

OpenBull uses the **OpenAlgo symbol convention** — one canonical string per instrument that's identical across every broker. The broker's native ticker is kept on the master-contract row (`brsymbol`) but never crosses the API surface; clients see, store, and trade the OpenBull symbol.

This lets you switch brokers, write algos, share strategies, and integrate third-party tools without per-broker symbol mapping.

---

## 1. Construction Rules

### 1.1 Equity

Just the base ticker. No exchange suffix in the symbol — the `exchange` field carries that.

| Instrument | Symbol | Exchange |
|---|---|---|
| Infosys (NSE) | `INFY` | `NSE` |
| Tata Motors (BSE) | `TATAMOTORS` | `BSE` |
| State Bank of India | `SBIN` | `NSE` |

### 1.2 Futures

```
<BASE><EXPIRY>FUT
```

Expiry is `DDMMMYY` uppercase (`28MAY26`). No spaces, no hyphens.

| Instrument | Symbol |
|---|---|
| Bank Nifty April 2026 future | `BANKNIFTY30APR26FUT` |
| SENSEX April 2026 future | `SENSEX30APR26FUT` |
| USDINR currency future, May 2026 | `USDINR28MAY26FUT` |
| Crude Oil (MCX), May 2026 | `CRUDEOIL19MAY26FUT` |
| 7.26 GS 2033 bond future | `726GS203325APR26FUT` |

### 1.3 Options

```
<BASE><EXPIRY><STRIKE><CE|PE>
```

Strike is the actual strike price; decimals allowed (`292.5`). Option type is `CE` (call) or `PE` (put).

| Instrument | Symbol |
|---|---|
| Nifty 24,250 Call, 28-Apr-2026 | `NIFTY28APR2624250CE` |
| Vedanta 292.5 Call, 30-Apr-2026 | `VEDL30APR26292.5CE` |
| USDINR 84 Call, 28-May-2026 | `USDINR28MAY2684CE` |
| Crude Oil 6,750 Call (MCX), 19-May-2026 | `CRUDEOIL19MAY266750CE` |
| 7.26 GS 2032 Put, 25-Apr-2026, strike 97 | `726GS203225APR2697PE` |

---

## 2. Indices (Exchange `NSE_INDEX`)

Indices are spot-feed only — they're not directly tradable, but you stream them, query history, and use them as `underlying` in option services.

```
NIFTY              NIFTYMIDCAP50       NIFTYMETAL
NIFTYNXT50         NIFTYMIDSML400      NIFTYMIDLIQ15
FINNIFTY           NIFTYMNC            NIFTYPHARMA
BANKNIFTY          NIFTYPSE            NIFTYPSUBANK
MIDCPNIFTY         NIFTYPVTBANK        NIFTYREALTY
INDIAVIX           NIFTYSERVSECTOR     NIFTYSMLCAP100
NIFTY100           NIFTYSMLCAP250      NIFTYSMLCAP50
NIFTY200           NIFTY100EQLWGT      NIFTY100LIQ15
NIFTY500           NIFTY100LOWVOL30    NIFTY100QUALTY30
NIFTYALPHA50       NIFTY200QUALTY30    NIFTY50DIVPOINT
NIFTYAUTO          NIFTY50EQLWGT       NIFTY50PR1XINV
NIFTYCOMMODITIES   NIFTY50PR2XLEV      NIFTY50TR1XINV
NIFTYCONSUMPTION   NIFTY50TR2XLEV      NIFTY50VALUE20
NIFTYCPSE          NIFTYDIVOPPS50      NIFTYENERGY
NIFTYFMCG          NIFTYGROWSECT15     NIFTYINFRA
NIFTYIT            NIFTYMEDIA          NIFTYMIDCAP100
NIFTYMIDCAP150     HANGSENGBEESNAV     NIFTYGS10YR
NIFTYGS10YRCLN     NIFTYGS1115YR       NIFTYGS15YRPLUS
NIFTYGS48YR        NIFTYGS813YR        NIFTYGSCOMPSITE
```

## 3. Indices (Exchange `BSE_INDEX`)

```
SENSEX             BSEINDIAINFRASTRUCTUREINDEX
BANKEX             BSEINDUSTRIALS
SENSEX50           BSEINFORMATIONTECHNOLOGY
BSE100             BSEIPO
BSE150MIDCAPINDEX  BSELARGECAP
BSE200             BSEMETAL
BSE250LARGEMIDCAPINDEX  BSEMIDCAP
BSE400MIDSMALLCAPINDEX  BSEMIDCAPSELECTINDEX
BSE500             BSEOIL&GAS
BSEAUTO            BSEPOWER
BSECAPITALGOODS    BSEPSU
BSECARBONEX        BSEREALTY
BSECONSUMERDURABLES  BSESENSEXNEXT50
BSECPSE            BSESMALLCAP
BSEDOLLEX100       BSESMALLCAPSELECTINDEX
BSEDOLLEX200       BSESMEIPO
BSEDOLLEX30        BSETECK
BSEENERGY          BSETELECOM
BSEFASTMOVINGCONSUMERGOODS
BSEFINANCIALSERVICES
BSEGREENEX
BSEHEALTHCARE
```

> Index lists above are advisory. The authoritative set on any given day is whatever the broker's master-contract download writes into the `symtoken` table with `exchange = 'NSE_INDEX'` or `'BSE_INDEX'`.

---

## 4. Exchange Codes

See [order-constants.md](./order-constants.md) for the complete validated set. Quick reference:

| Code | Use |
|---|---|
| `NSE`, `BSE` | Cash equities |
| `NFO`, `BFO` | F&O |
| `CDS`, `BCD` | Currency derivatives |
| `MCX`, `NCDEX` | Commodities |
| `NSE_INDEX`, `BSE_INDEX`, `MCX_INDEX` | Index quote / history feeds (non-tradable) |

---

## 5. Master Contract Schema

Each row in the `symtoken` table represents one tradable instrument. Populated by `backend/broker/{name}/database/master_contract_db.py:master_contract_download` and mirrored into Redis for fast lookup.

| Column | Type | Meaning |
|---|---|---|
| `id` | int | Primary key |
| `symbol` | str | OpenBull canonical symbol (per § 1) |
| `brsymbol` | str | Broker-native ticker (whatever the broker calls it) |
| `name` | str | **Underlying** ticker for F&O rows (`NIFTY`, `RELIANCE`); company / index name for equity & index rows |
| `exchange` | str | OpenBull canonical exchange code |
| `brexchange` | str | Broker-native exchange code (e.g. Upstox's `NSE_FO`) |
| `token` | str | Broker instrument token |
| `expiry` | str | `DD-MMM-YY` uppercase (`28-APR-26`); empty for cash |
| `strike` | float | Strike price; `0` for non-options |
| `lotsize` | int | Contract size (`1` for equities) |
| `tick_size` | float | Minimum price increment in rupees (e.g. `0.05` for NFO) |
| `instrumenttype` | str | `EQ`, `CE`, `PE`, `FUT`, `INDEX` |

The download is idempotent — every run replaces all rows for that broker. Run on demand from `/broker/config`, or the scheduled job (NSE master refreshes once daily).

The `name` column is critically important: it's what the underlying-picker combobox groups by. Setting `name` to the per-contract description (e.g. `"NIFTY 02 Jun 26 18650 CE"`) on F&O rows produces a UI with one entry per contract instead of one per underlying. See [broker-integration.md § Common Pitfalls](./broker-integration.md#common-pitfalls) for the diagnostic.

---

## 6. Symbol Round-Trip

Order placement uses OpenBull → broker symbol; order responses come back with the broker's symbol and have to map back to OpenBull. Each plugin's `mapping/order_data.py` provides:

```python
def _get_br_symbol(symbol: str, exchange: str) -> str: ...   # OpenBull → broker
def _get_oa_symbol(brsymbol: str, exchange: str) -> str: ...  # broker → OpenBull
```

These must be mathematical inverses. An asymmetric pair causes orderbook entries to display incorrectly while orders still fire — a hard-to-debug bug because the trade is real.

---

## 7. Live Lookup

Use the search and symbol endpoints to confirm a symbol is in the master before placing an order:

```http
GET /api/v1/symbol?symbol=NIFTY28APR2624250CE&exchange=NFO
GET /api/v1/search?query=infy
GET /web/symbols/search?query=banknifty&exchange=NFO
```

The in-app search at `/search` is the same endpoint with a tokenised UI on top.

---

## 8. See Also

- [Order Constants](./order-constants.md) — canonical enums for exchange / product / pricetype / action
- [Broker Integration](./broker-integration.md) — how a broker plugin produces `symtoken` rows during master-contract download
- [API: Symbol Services](../api/symbol-services/symbol.md) — `/api/v1/symbol`, `/api/v1/search`, `/api/v1/expiry`
