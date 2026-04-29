# Services Layer Documentation

## Overview

The services layer (`backend/services/`) contains all of OpenBull's business logic. FastAPI endpoints are thin handlers that resolve auth (via the `get_api_user(request, db)` dependency), parse the request body, call a service function, and return its response. Services are broker-agnostic — they use `importlib.import_module(f"backend.broker.{broker_name}.api.{module}")` to dynamically load the correct broker plugin.

Unlike the OpenAlgo SDK, OpenBull services do **not** accept an `api_key` parameter. The FastAPI dependency resolves the API key into the tuple `(user_id, auth_token, broker_name, config)` once per request, and these values are passed straight into the service. Every public service function returns the same tuple shape:

```python
(success: bool, response_data: dict, http_status_code: int)
```

## Table of Contents

1. [Order Management Services](#order-management-services)
   - [PlaceOrder](#placeorder)
   - [PlaceSmartOrder](#placesmartorder)
   - [ModifyOrder](#modifyorder)
   - [CancelOrder](#cancelorder)
   - [CancelAllOrders](#cancelallorders)
   - [CloseAllPositions](#closeallpositions)
   - [BasketOrder](#basketorder)
   - [SplitOrder](#splitorder)
   - [PlaceOptionsOrder](#placeoptionsorder)
   - [OptionsMultiOrder](#optionsmultiorder)
2. [Order Information Services](#order-information-services)
   - [OrderBook](#orderbook)
   - [TradeBook](#tradebook)
   - [Positions](#positions)
   - [Holdings](#holdings)
   - [OrderStatus](#orderstatus)
   - [OpenPosition](#openposition)
3. [Market Data Services](#market-data-services)
   - [Quotes](#quotes)
   - [MultiQuotes](#multiquotes)
   - [Depth](#depth)
   - [History](#history)
4. [Symbol & Reference Data Services](#symbol--reference-data-services)
   - [SymbolInfo](#symbolinfo)
   - [SearchSymbols](#searchsymbols)
   - [ExpiryDates](#expirydates)
   - [SupportedIntervals](#supportedintervals)
   - [MasterContracts](#mastercontracts)
   - [OptionUnderlyings](#optionunderlyings)
5. [Options Services](#options-services)
   - [OptionSymbol](#optionsymbol)
   - [OptionChain](#optionchain)
   - [OptionGreeks](#optiongreeks)
   - [SyntheticFuture](#syntheticfuture)
6. [Account Services](#account-services)
   - [Funds](#funds)
   - [Margin](#margin)
7. [Analytics & Tools Services](#analytics--tools-services)
   - [MaxPain](#maxpain)
   - [OITracker](#oitracker)
   - [IVSmile](#ivsmile)
   - [IVChart](#ivchart)
   - [VolSurface](#volsurface)
   - [StraddleChart](#straddlechart)
   - [GEX](#gex)
8. [Sandbox Service](#sandbox-service)
9. [Trading Mode Service](#trading-mode-service)
10. [Strategy Builder & Portfolio](#strategy-builder--portfolio)
    - [SaveStrategy / List / Get / Update / Delete](#savestrategy--list--get--update--delete)
    - [StrategySnapshot](#strategysnapshot)
    - [StrategyChart](#strategychart)
11. [Market Data Cache](#market-data-cache)
12. [Common Patterns](#common-patterns)

---

## Order Management Services

### PlaceOrder

Place a new order with the configured broker.

**Function:** `place_order(order_data, api_key=None, auth_token=None, broker=None, config=None, user_id=None)`

**Location:** `backend/services/order_service.py:109`

A thin shell around `place_order_with_auth(order_data, auth_token, broker, config, user_id)` (`backend/services/order_service.py:61`) that adds field validation. When `user_id` is provided and the global trading mode is `sandbox`, the call is routed to the sandbox simulator instead of the broker API.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| order_data | dict | Yes | Order payload (see Data Fields) |
| api_key | str | No | Accepted for back-compat; resolved upstream by `get_api_user` |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin name (e.g. `upstox`, `zerodha`) |
| config | dict | No | Broker config dict (api_key/secret etc.) |
| user_id | int | No | App user id; enables sandbox dispatch |

**Order Data Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symbol | str | Yes | OpenBull symbol (e.g. `NHPC`, `NIFTY28OCT2525000CE`) |
| exchange | str | Yes | Member of `VALID_EXCHANGES` |
| action | str | Yes | `BUY` or `SELL` (auto-uppercased) |
| quantity | int/str | Yes | Order quantity |
| pricetype | str | Yes | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| product | str | Yes | `MIS`, `CNC`, `NRML` |
| price | float/str | No | Limit price (default `"0"`) |
| trigger_price | float/str | No | Trigger price for SL/SL-M |
| disclosed_quantity | int/str | No | Disclosed quantity |
| strategy | str | No | Strategy tag for telemetry |

**Validation:** `validate_order_data` (`backend/services/order_service.py:34`) checks for the six required fields and asserts each enum field is present in the corresponding constant set.

**Example:**

```python
from backend.services.order_service import place_order

success, response, status_code = place_order(
    order_data={
        "symbol": "NHPC",
        "exchange": "NSE",
        "action": "BUY",
        "quantity": 1,
        "pricetype": "MARKET",
        "product": "MIS",
        "strategy": "Python",
    },
    auth_token=auth_token,
    broker=broker_name,
    config=config,
    user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "orderid": "250408000989443"
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### PlaceSmartOrder

Place a position-aware order. The broker's `place_smartorder_api` reads the current net position and computes the delta order quantity itself.

**Function:** `place_smart_order(order_data, auth_token, broker, config=None)`

**Location:** `backend/services/order_service.py:137`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| order_data | dict | Yes | Smart-order payload |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin name |
| config | dict | No | Broker config dict |

**Order Data Fields:** Same as PlaceOrder, plus:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| position_size | int/str | Yes | Target net position (signed: positive = long, negative = short, 0 = flat) |

**Example:**

```python
from backend.services.order_service import place_smart_order

success, response, status_code = place_smart_order(
    order_data={
        "symbol": "TATAMOTORS",
        "exchange": "NSE",
        "action": "SELL",
        "pricetype": "MARKET",
        "product": "MIS",
        "quantity": 1,
        "position_size": 5,
        "strategy": "Python",
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "orderid": "250408000997543"
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### ModifyOrder

Modify quantity/price/trigger of a live order.

**Function:** `modify_order_service(data, auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/order_service.py:165`

Sandbox dispatch is supported when `user_id` is supplied.

**Data Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| orderid | str | Yes | Broker order id to modify |
| quantity | int/str | Yes | New quantity |
| price | float/str | Yes | New price |
| pricetype | str | Yes | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| trigger_price | float/str | No | New trigger price |
| disclosed_quantity | int/str | No | New disclosed quantity |

The OpenBull API endpoint also accepts `symbol`, `action`, `exchange`, `product`, `strategy` for OpenAlgo-shape parity but does not forward them to the broker (see `backend/api/place_order.py:130`).

**Example:**

```python
from backend.services.order_service import modify_order_service

success, response, status_code = modify_order_service(
    data={
        "orderid": "250408001002736",
        "quantity": 1,
        "price": 16.5,
        "pricetype": "LIMIT",
    },
    auth_token=auth_token,
    broker=broker_name,
    user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "orderid": "250408001002736"
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### CancelOrder

Cancel a specific live order.

**Function:** `cancel_order_service(orderid, auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/order_service.py:197`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| orderid | str | Yes | Broker order id |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin name |
| config | dict | No | Broker config dict |
| user_id | int | No | Enables sandbox dispatch |

**Example:**

```python
from backend.services.order_service import cancel_order_service

success, response, status_code = cancel_order_service(
    orderid="250408001002736",
    auth_token=auth_token,
    broker=broker_name,
    user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "orderid": "250408001002736"
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### CancelAllOrders

Cancel every open and trigger-pending order on the broker side.

**Function:** `cancel_all_orders_service(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/order_service.py:229`

**Example:**

```python
from backend.services.order_service import cancel_all_orders_service

success, response, status_code = cancel_all_orders_service(
    auth_token=auth_token,
    broker=broker_name,
    user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "canceled": ["250408001042620", "250408001042667"],
    "failed": []
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### CloseAllPositions

Square off every open position by submitting opposing orders. Routes to sandbox when `user_id` is supplied and trading mode is `sandbox`.

**Function:** `close_all_positions_service(api_key, auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/order_service.py:262`

`api_key` is forwarded to `broker_module.close_all_positions(api_key, auth_token)` for brokers (e.g. Upstox) that need it for symbol resolution during the squareoff loop.

**Example:**

```python
from backend.services.order_service import close_all_positions_service

success, response, status_code = close_all_positions_service(
    api_key=api_key,
    auth_token=auth_token,
    broker=broker_name,
    user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "message": "All Open Positions Squared Off"
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### BasketOrder

Place multiple orders concurrently. BUY legs are dispatched before SELL legs so that opening longs free up cash before opposing shorts hit the margin engine. Orders are executed in batches of `BATCH_SIZE = 10` with a `BATCH_DELAY_SEC = 1.0` pause between batches.

**Function:** `place_basket_order(basket_data, auth_token, broker, config=None)`

**Location:** `backend/services/basket_order_service.py:79`

**Basket Data Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| strategy | str | No | Strategy tag applied to every leg |
| orders | list[dict] | Yes | Order legs (each validated independently) |

**Per-Leg Fields:** identical to PlaceOrder Order Data Fields (`symbol`, `exchange`, `action`, `quantity`, `pricetype`, `product`, optional `price`/`trigger_price`/`disclosed_quantity`).

**Example:**

```python
from backend.services.basket_order_service import place_basket_order

success, response, status_code = place_basket_order(
    basket_data={
        "strategy": "Python",
        "orders": [
            {"symbol": "BHEL", "exchange": "NSE", "action": "BUY",
             "quantity": 1, "pricetype": "MARKET", "product": "MIS"},
            {"symbol": "ZOMATO", "exchange": "NSE", "action": "SELL",
             "quantity": 1, "pricetype": "MARKET", "product": "MIS"},
        ],
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "results": [
    {"symbol": "BHEL", "status": "success", "orderid": "250408000999544"},
    {"symbol": "ZOMATO", "status": "success", "orderid": "250408000997545"}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### SplitOrder

Split a large quantity into chunks of `splitsize` placed sequentially with a rate-limit delay derived from `ORDER_RATE_LIMIT` (default `"10 per second"` → 0.1 s between orders).

**Function:** `split_order(split_data, auth_token, broker, config=None)`

**Location:** `backend/services/split_order_service.py:98`

The total number of generated orders cannot exceed `MAX_ORDERS = 100`.

**Split Data Fields:** PlaceOrder fields plus:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| splitsize | int | Yes | Quantity per child order; remainder placed as the last child |

**Example:**

```python
from backend.services.split_order_service import split_order

success, response, status_code = split_order(
    split_data={
        "symbol": "YESBANK",
        "exchange": "NSE",
        "action": "SELL",
        "quantity": 105,
        "splitsize": 20,
        "pricetype": "MARKET",
        "product": "MIS",
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "split_size": 20,
  "total_quantity": 105,
  "results": [
    {"order_num": 1, "quantity": 20, "status": "success", "orderid": "250408001021467"},
    {"order_num": 2, "quantity": 20, "status": "success", "orderid": "250408001021459"},
    {"order_num": 3, "quantity": 20, "status": "success", "orderid": "250408001021466"},
    {"order_num": 4, "quantity": 20, "status": "success", "orderid": "250408001021470"},
    {"order_num": 5, "quantity": 20, "status": "success", "orderid": "250408001021471"},
    {"order_num": 6, "quantity": 5,  "status": "success", "orderid": "250408001021472"}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### PlaceOptionsOrder

Resolve an option symbol from `(underlying, expiry_date, offset, option_type)` via `option_symbol_service.get_option_symbol`, then place the order. Optionally pipes the resolved order through `split_order` when `splitsize > 0`.

**Function:** `place_options_order(options_data, auth_token, broker, config=None)`

**Location:** `backend/services/place_options_order_service.py:17`

**Options Data Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| underlying | str | Yes | Base symbol (e.g. `NIFTY`, `RELIANCE`, or pre-formatted `NIFTY28APR26FUT`) |
| exchange | str | Yes | `NSE_INDEX`, `BSE_INDEX`, `NSE`, `BSE`, `NFO`, `BFO`, `MCX`, `CDS` |
| expiry_date | str | Conditional | `DDMMMYY` (e.g. `28OCT25`); optional if embedded in `underlying` |
| offset | str | Yes | `ATM`, `ITM1..ITM50`, `OTM1..OTM50` |
| option_type | str | Yes | `CE` or `PE` |
| action | str | Yes | `BUY` or `SELL` |
| quantity | int/str | Yes | Order quantity |
| pricetype | str | Yes | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| product | str | Yes | `MIS` or `NRML` |
| price | float/str | No | Limit price |
| trigger_price | float/str | No | Trigger for SL/SL-M |
| disclosed_quantity | int/str | No | Disclosed quantity |
| splitsize | int | No | If > 0, leg is split via `split_order_service` |
| strategy | str | No | Strategy tag |

**Example - ATM call:**

```python
from backend.services.place_options_order_service import place_options_order

success, response, status_code = place_options_order(
    options_data={
        "underlying": "NIFTY",
        "exchange": "NSE_INDEX",
        "expiry_date": "28OCT25",
        "offset": "ATM",
        "option_type": "CE",
        "action": "BUY",
        "quantity": 75,
        "pricetype": "MARKET",
        "product": "NRML",
        "strategy": "python",
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "symbol": "NIFTY28OCT2525950CE",
  "exchange": "NFO",
  "underlying": "NIFTY",
  "underlying_ltp": 25966.05,
  "offset": "ATM",
  "option_type": "CE",
  "orderid": "25102800000006"
}
```

When `splitsize > 0` the response replaces `orderid` with a nested `split` payload from the split-order service.

**Returns:** `(success, response_data, http_status_code)`.

---

### OptionsMultiOrder

Place a multi-leg options strategy. Each leg's offset / expiry / option_type is resolved independently (so diagonal spreads work) and the legs are dispatched in batches with BUY legs first.

**Function:** `place_options_multiorder(multi_data, auth_token, broker, config=None)`

**Location:** `backend/services/options_multiorder_service.py:92`

**Top-Level Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| underlying | str | Yes | Default underlying (each leg can override) |
| exchange | str | Yes | Default exchange (each leg can override) |
| expiry_date | str | No | Default expiry; per-leg `expiry_date` overrides |
| pricetype | str | No | Default `MARKET` |
| product | str | No | Default `NRML` |
| strategy | str | No | Strategy tag applied to every leg |
| legs | list[dict] | Yes | Leg specifications |

**Per-Leg Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| offset | str | Yes | `ATM`, `ITMn`, `OTMn` |
| option_type | str | Yes | `CE` / `PE` |
| action | str | Yes | `BUY` / `SELL` |
| quantity | int | Yes | Leg quantity |
| expiry_date | str | No | Per-leg expiry override (diagonal spreads) |
| pricetype | str | No | Per-leg override |
| product | str | No | Per-leg override |
| splitsize | int | No | If > 0, route this leg through `split_order` |

**Example - Iron Condor:**

```python
from backend.services.options_multiorder_service import place_options_multiorder

success, response, status_code = place_options_multiorder(
    multi_data={
        "strategy": "Iron Condor",
        "underlying": "NIFTY",
        "exchange": "NSE_INDEX",
        "expiry_date": "25NOV25",
        "legs": [
            {"offset": "OTM6", "option_type": "CE", "action": "BUY",  "quantity": 75},
            {"offset": "OTM6", "option_type": "PE", "action": "BUY",  "quantity": 75},
            {"offset": "OTM4", "option_type": "CE", "action": "SELL", "quantity": 75},
            {"offset": "OTM4", "option_type": "PE", "action": "SELL", "quantity": 75},
        ],
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "underlying_ltp": 26050.45,
  "results": [
    {"leg": 1, "action": "BUY",  "offset": "OTM6", "option_type": "CE",
     "symbol": "NIFTY25NOV2526350CE", "status": "success", "orderid": "25111996859688"},
    {"leg": 2, "action": "BUY",  "offset": "OTM6", "option_type": "PE",
     "symbol": "NIFTY25NOV2525750PE", "status": "success", "orderid": "25111996042210"},
    {"leg": 3, "action": "SELL", "offset": "OTM4", "option_type": "CE",
     "symbol": "NIFTY25NOV2526250CE", "status": "success", "orderid": "25111922189638"},
    {"leg": 4, "action": "SELL", "offset": "OTM4", "option_type": "PE",
     "symbol": "NIFTY25NOV2525850PE", "status": "success", "orderid": "25111919252668"}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

## Order Information Services

### OrderBook

Fetch all orders for the day. Loads `broker.{name}.api.order_api.get_order_book` plus the broker's mapping module to map / transform / aggregate the response.

**Function:** `get_orderbook_with_auth(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/orderbook_service.py:76`

A wrapper `get_orderbook(api_key=None, auth_token=None, broker=None, config=None, user_id=None)` (`backend/services/orderbook_service.py:127`) is also exported for parity with the OpenAlgo SDK signature.

**Example:**

```python
from backend.services.orderbook_service import get_orderbook_with_auth

success, response, status_code = get_orderbook_with_auth(
    auth_token=auth_token, broker=broker_name, user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "orders": [
      {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "quantity": 1,
        "price": 1234.5,
        "pricetype": "LIMIT",
        "product": "MIS",
        "order_status": "complete",
        "orderid": "250408001234567",
        "average_price": 1234.45,
        "trigger_price": 0.0,
        "timestamp": "08-Apr-2025 09:15:30"
      }
    ],
    "statistics": {
      "total_orders": 5,
      "total_completed_orders": 3,
      "total_open_orders": 1,
      "total_rejected_orders": 1,
      "total_buy_orders": 4,
      "total_sell_orders": 1
    }
  }
}
```

Numeric fields are rounded to 2 decimals (except quantity-like fields, which are coerced to int when integral). MARKET-pricetype orders have `price` forced to `0.0`.

**Returns:** `(success, response_data, http_status_code)`.

---

### TradeBook

Fetch all trades for the day. Loads `order_api.get_trade_book` and the `mapping.order_data.{map_trade_data, transform_tradebook_data}` helpers from the broker plugin.

**Function:** `get_tradebook_with_auth(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/tradebook_service.py:52`

**Example:**

```python
from backend.services.tradebook_service import get_tradebook_with_auth

success, response, status_code = get_tradebook_with_auth(
    auth_token=auth_token, broker=broker_name, user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": [
    {
      "symbol": "RELIANCE",
      "exchange": "NSE",
      "action": "BUY",
      "quantity": 1,
      "average_price": 1234.45,
      "trade_value": 1234.45,
      "product": "MIS",
      "orderid": "250408001234567",
      "timestamp": "09:15:30"
    }
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### Positions

Fetch the current position book. The broker plugin's `map_position_data` and `transform_positions_data` produce the OpenBull-shaped row.

**Function:** `get_positions_with_auth(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/positions_service.py:52`

**Example:**

```python
from backend.services.positions_service import get_positions_with_auth

success, response, status_code = get_positions_with_auth(
    auth_token=auth_token, broker=broker_name, user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": [
    {
      "symbol": "RELIANCE",
      "exchange": "NSE",
      "product": "MIS",
      "quantity": 10,
      "average_price": 1234.45,
      "ltp": 1240.0,
      "pnl": 55.5
    }
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### Holdings

Fetch the portfolio holdings page. Returns both the holdings array and aggregate statistics.

**Function:** `get_holdings_with_auth(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/holdings_service.py:56`

**Example:**

```python
from backend.services.holdings_service import get_holdings_with_auth

success, response, status_code = get_holdings_with_auth(
    auth_token=auth_token, broker=broker_name, user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "holdings": [
      {
        "symbol": "INFY",
        "exchange": "NSE",
        "quantity": 100,
        "average_price": 1450.0,
        "ltp": 1500.5,
        "pnl": 5050.0,
        "pnlpercent": 3.48
      }
    ],
    "statistics": {
      "totalholdingvalue": 150050.0,
      "totalinvvalue": 145000.0,
      "totalprofitandloss": 5050.0,
      "totalpnlpercentage": 3.48
    }
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### OrderStatus

Fetch the status of one specific order by `orderid`. Internally calls `get_order_book` + transform, then filters the returned list.

**Function:** `get_orderstatus_with_auth(orderid, auth_token, broker, config=None)`

**Location:** `backend/services/orderstatus_service.py:12`

**Example:**

```python
from backend.services.orderstatus_service import get_orderstatus_with_auth

success, response, status_code = get_orderstatus_with_auth(
    orderid="250408000185002",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "orderid": "250408000185002",
    "symbol": "YESBANK",
    "exchange": "NSE",
    "action": "BUY",
    "quantity": "1",
    "price": 0,
    "pricetype": "MARKET",
    "product": "MIS",
    "trigger_price": 0,
    "average_price": 18.95,
    "order_status": "complete",
    "timestamp": "08-Apr-2025 09:59:10"
  }
}
```

Returns `404` with `{"status": "error", "message": "Order {orderid} not found"}` when the id is not present in the orderbook.

**Returns:** `(success, response_data, http_status_code)`.

---

### OpenPosition

Return the net quantity for one specific symbol/exchange/product combination by calling the broker's `get_open_position`.

**Function:** `get_openposition_with_auth(symbol, exchange, product, auth_token, broker, config=None)`

**Location:** `backend/services/openposition_service.py:12`

**Example:**

```python
from backend.services.openposition_service import get_openposition_with_auth

success, response, status_code = get_openposition_with_auth(
    symbol="YESBANK",
    exchange="NSE",
    product="MIS",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {"quantity": -10}
}
```

Brokers that have no matching position should return `0` from `get_open_position`.

**Returns:** `(success, response_data, http_status_code)`.

---

## Market Data Services

### Quotes

Single-symbol LTP/OHLC snapshot. Loads `broker.{name}.api.data.get_quotes`.

**Function:** `get_quotes_with_auth(symbol, exchange, auth_token, broker, config=None)`

**Location:** `backend/services/quotes_service.py:12`

**Example:**

```python
from backend.services.quotes_service import get_quotes_with_auth

success, response, status_code = get_quotes_with_auth(
    symbol="RELIANCE", exchange="NSE",
    auth_token=auth_token, broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "ltp": 1187.75,
    "open": 1172.0,
    "high": 1196.6,
    "low": 1163.3,
    "close": 1165.7,
    "prev_close": 1165.7,
    "volume": 14414545,
    "bid": 1187.85,
    "ask": 1188.0
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### MultiQuotes

Batched quotes for many symbols in a single broker call.

**Function:** `get_multi_quotes_with_auth(symbols_list, auth_token, broker, config=None)`

**Location:** `backend/services/quotes_service.py:31`

**Important:** OpenBull surfaces the broker's quote list under the key **`results`**, not `data`, to match the OpenAlgo wire contract.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbols_list | list[dict] | Yes | List of `{"symbol": str, "exchange": str}` |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin name |
| config | dict | No | Broker config dict |

**Example:**

```python
from backend.services.quotes_service import get_multi_quotes_with_auth

success, response, status_code = get_multi_quotes_with_auth(
    symbols_list=[
        {"symbol": "RELIANCE", "exchange": "NSE"},
        {"symbol": "NIFTY",    "exchange": "NSE_INDEX"},
    ],
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "results": [
    {"symbol": "RELIANCE", "exchange": "NSE",
     "ltp": 1187.75, "open": 1172.0, "high": 1196.6, "low": 1163.3,
     "close": 1165.7, "volume": 14414545},
    {"symbol": "NIFTY", "exchange": "NSE_INDEX",
     "ltp": 25966.05, "open": 25900.0, "high": 26000.5, "low": 25880.0,
     "close": 25910.0, "volume": 0}
  ]
}
```

OpenBull's broker plugins return the per-symbol fields **flat** at the top level. OpenAlgo's plugins nest them under `data`. Consumers (e.g. `vol_surface_service`) handle both with `r.get("data", r)`.

**Returns:** `(success, response_data, http_status_code)`.

---

### Depth

5-level bid/ask snapshot.

**Function:** `get_depth_with_auth(symbol, exchange, auth_token, broker, config=None)`

**Location:** `backend/services/depth_service.py:12`

**Example:**

```python
from backend.services.depth_service import get_depth_with_auth

success, response, status_code = get_depth_with_auth(
    symbol="RELIANCE", exchange="NSE",
    auth_token=auth_token, broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "ltp": 1187.75,
    "bids": [
      {"price": 1187.85, "quantity": 100, "orders": 5},
      {"price": 1187.80, "quantity": 250, "orders": 9}
    ],
    "asks": [
      {"price": 1188.00, "quantity": 50,  "orders": 2},
      {"price": 1188.10, "quantity": 150, "orders": 4}
    ]
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### History

Historical OHLCV candles for a symbol/interval/date range.

**Function:** `get_history_with_auth(symbol, exchange, interval, start_date, end_date, auth_token, broker, config=None)`

**Location:** `backend/services/history_service.py:13`

The interval is validated against `data_module.TIMEFRAME_MAP` exposed by the broker plugin. Dates must be `YYYY-MM-DD` and `start_date <= end_date`.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| symbol | str | Yes | OpenBull symbol |
| exchange | str | Yes | Exchange |
| interval | str | Yes | Broker-supported interval (`1m`, `5m`, `15m`, `30m`, `1h`, `D`, ...) |
| start_date | str | Yes | `YYYY-MM-DD` |
| end_date | str | Yes | `YYYY-MM-DD` |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin |
| config | dict | No | Broker config dict |

**Example:**

```python
from backend.services.history_service import get_history_with_auth

success, response, status_code = get_history_with_auth(
    symbol="RELIANCE", exchange="NSE",
    interval="5m",
    start_date="2025-04-01", end_date="2025-04-08",
    auth_token=auth_token, broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "data": [
    {"timestamp": 1743658800, "open": 1170.0, "high": 1175.5,
     "low": 1168.0, "close": 1174.2, "volume": 12345},
    {"timestamp": 1743659100, "open": 1174.2, "high": 1176.0,
     "low": 1172.5, "close": 1175.8, "volume": 9876}
  ]
}
```

`timestamp` is a Unix epoch in seconds (IST-aware via the broker's response handler).

**Returns:** `(success, response_data, http_status_code)`.

---

## Symbol & Reference Data Services

### SymbolInfo

Look up the full `symtoken` row for a symbol/exchange. DB-only — no broker call.

**Function:** `get_symbol_info(symbol, exchange)`

**Location:** `backend/services/market_data_service.py:40`

**Example:**

```python
from backend.services.market_data_service import get_symbol_info

success, response, status_code = get_symbol_info(symbol="RELIANCE", exchange="NSE")
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "symbol": "RELIANCE",
    "brsymbol": "RELIANCE-EQ",
    "name": "RELIANCE INDUSTRIES LIMITED",
    "exchange": "NSE",
    "brexchange": "NSE",
    "token": "2885",
    "expiry": "",
    "strike": -0.01,
    "lotsize": 1,
    "instrumenttype": "EQ",
    "tick_size": 0.05
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### SearchSymbols

Fuzzy search the `symtoken` table by symbol code or company name (`ILIKE %query%`). Capped at 50 results.

**Function:** `search_symbols_api(query, exchange=None)`

**Location:** `backend/services/market_data_service.py:76`

**Example:**

```python
from backend.services.market_data_service import search_symbols_api

success, response, status_code = search_symbols_api(query="NIFTY", exchange="NFO")
```

**Response:** Top-level `{"status": "success", "data": [...]}` where each item has the same fields as SymbolInfo.

**Returns:** `(success, response_data, http_status_code)`.

A second async function `search_symbols(query, exchange, broker_name="upstox")` (`backend/services/symbol_service.py:86`) routes through the broker's `master_contract_db.search_symbols` instead of querying the DB directly.

---

### ExpiryDates

Return sorted unique expiry strings for a symbol on an F&O exchange. Optionally filtered by `instrumenttype` (`"options"` → `CE`+`PE`, `"futures"` → `FUT`).

**Function:** `get_expiry_dates(symbol, exchange, instrumenttype=None)`

**Location:** `backend/services/market_data_service.py:122`

The function tries `WHERE name = :symbol` first, falls back to `WHERE symbol LIKE :symbol%` if nothing matches. Expiries are parsed with format `%d-%b-%y` and sorted ascending.

**Example:**

```python
from backend.services.market_data_service import get_expiry_dates

success, response, status_code = get_expiry_dates(
    symbol="NIFTY", exchange="NFO", instrumenttype="options",
)
```

**Response:**

```json
{
  "status": "success",
  "data": ["08-May-25", "15-May-25", "22-May-25", "29-May-25"]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### SupportedIntervals

Return the broker-specific candle intervals exposed by `broker.{name}.api.data.SUPPORTED_INTERVALS`.

**Function:** `get_supported_intervals(broker)`

**Location:** `backend/services/market_data_service.py:189`

**Example:**

```python
from backend.services.market_data_service import get_supported_intervals

success, response, status_code = get_supported_intervals(broker="upstox")
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "minutes": ["1m", "5m", "15m", "30m"],
    "hours": ["1h"],
    "days": ["D"]
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### MasterContracts

Trigger the broker plugin's master contract download. Used to populate the `symtoken` table after broker login.

**Function:** `download_master_contracts(broker_name, auth_token=None)`

**Location:** `backend/services/symbol_service.py:13`

**Returns:** `dict` with `status`/`message`/`count` keys (does NOT use the `(success, response, status)` tuple convention — this is a startup/admin helper).

Progress tracking helpers live in `backend/services/master_contract_status.py`:

- `get_download_status()` — return current snapshot dict.
- `set_downloading(broker)`, `set_success(broker, total_symbols)`, `set_error(...)` — state transitions used by the download routine.

---

### OptionUnderlyings

Return distinct option underlyings on an exchange as `{"symbol", "name"}` pairs by parsing the `BASE{DDMMMYY}…CE` prefix from the master contract.

**Function:** `get_option_underlyings(exchange)`

**Location:** `backend/services/symbol_service.py:43`

**Example:**

```python
from backend.services.symbol_service import get_option_underlyings

result = get_option_underlyings("NFO")
# [{"symbol": "NIFTY", "name": "NIFTY"}, {"symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LIMITED"}, ...]
```

**Returns:** `list[dict]`. Test instruments containing `NSETEST` / `BSETEST` are filtered out.

---

## Options Services

### OptionSymbol

Resolve `(underlying, expiry, offset, option_type)` to a tradable option symbol. Steps:

1. Parse `underlying` into base symbol + optional embedded expiry (e.g. `NIFTY28APR26FUT` → `NIFTY`, `28APR26`).
2. Resolve the LTP exchange (`NSE_INDEX` for indices, `NSE/BSE` for equities, the futures exchange for commodities).
3. Fetch underlying LTP via `get_quotes_with_auth`.
4. Load distinct strikes for the expiry from `symtoken` (cached in `_STRIKES_CACHE`).
5. Find ATM (closest strike to LTP), apply offset (`ITMn`/`OTMn`/`ATM`) using the option's directional convention (CE: ITM = below ATM; PE: ITM = above ATM).
6. Look up the resolved symbol in `symtoken` to return all of its metadata.

**Function:** `get_option_symbol(underlying, exchange, expiry_date, offset, option_type, auth_token, broker, config=None)`

**Location:** `backend/services/option_symbol_service.py:211`

A helper `clear_strikes_cache()` (`backend/services/option_symbol_service.py:288`) drops the in-memory cache (used after a master contract refresh).

**Example:**

```python
from backend.services.option_symbol_service import get_option_symbol

success, response, status_code = get_option_symbol(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    offset="OTM2",
    option_type="CE",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "symbol": "NIFTY28OCT2526050CE",
  "exchange": "NFO",
  "lotsize": 75,
  "tick_size": 0.05,
  "strike": 26050.0,
  "expiry": "28-OCT-25",
  "underlying_ltp": 25966.05
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### OptionChain

Build a CE/PE chain centered on ATM with `strike_count` strikes either side. Fetches every leg's quote in a single `get_multi_quotes_with_auth` round-trip and labels each row (`ITMn` / `ATM` / `OTMn` independently for CE and PE).

**Function:** `get_option_chain(underlying, exchange, expiry_date, strike_count, auth_token, broker, config=None)`

**Location:** `backend/services/option_chain_service.py:77`

For exchanges without a tradable spot (MCX, CDS), the service auto-resolves the near-month FUT contract via `_find_near_month_futures` and uses its LTP as the ATM-pricing reference.

**Example:**

```python
from backend.services.option_chain_service import get_option_chain

success, response, status_code = get_option_chain(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    strike_count=10,
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "underlying_ltp": 25966.05,
  "underlying_prev_close": 25900.0,
  "quote_symbol": "NIFTY",
  "quote_exchange": "NSE_INDEX",
  "expiry_date": "28OCT25",
  "atm_strike": 25950.0,
  "chain": [
    {
      "strike": 25750.0,
      "ce": {
        "symbol": "NIFTY28OCT2525750CE", "label": "ITM4",
        "ltp": 220.5, "open": 215.0, "high": 230.0, "low": 210.0,
        "prev_close": 218.0, "volume": 123456, "oi": 1234500,
        "bid": 220.0, "ask": 221.0, "bid_qty": 75, "ask_qty": 150,
        "lotsize": 75, "tick_size": 0.05
      },
      "pe": {
        "symbol": "NIFTY28OCT2525750PE", "label": "OTM4",
        "ltp": 18.5, "open": 21.0, "high": 22.0, "low": 17.5,
        "prev_close": 20.5, "volume": 87654, "oi": 987654,
        "bid": 18.4, "ask": 18.6, "bid_qty": 75, "ask_qty": 75,
        "lotsize": 75, "tick_size": 0.05
      }
    }
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### OptionGreeks

Compute Black-76 implied volatility and Greeks (delta, gamma, theta, vega, rho) for an option. Pure-Python — no scipy / py_vollib / numba. IV is solved via bisection over `[1e-6, 5.0]` for 80 iterations. Deep-ITM with no time value falls back to theoretical Greeks.

The two public entry points mirror OpenAlgo's surface:

- `calculate_greeks(option_symbol, exchange, spot_price, option_price, interest_rate=None, expiry_time=None)` — pure math, no broker calls.
- `get_option_greeks(option_symbol, exchange, interest_rate=None, forward_price=None, underlying_symbol=None, underlying_exchange=None, expiry_time=None, auth_token=None, broker=None, config=None)` — fetches live spot + option LTP, then calls `calculate_greeks`.

**Locations:**

- `backend/services/option_greeks_service.py:260` (`calculate_greeks`)
- `backend/services/option_greeks_service.py:338` (`get_option_greeks`)

**Parameters (`get_option_greeks`):**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| option_symbol | str | Yes | Option symbol (e.g. `NIFTY28OCT2525950CE`) |
| exchange | str | Yes | Options exchange (`NFO`, `BFO`, `MCX`, `CDS`) |
| interest_rate | float | No | Annualised % (per-exchange default in `DEFAULT_INTEREST_RATES`, all 0%) |
| forward_price | float | No | Override the auto-fetched spot price |
| underlying_symbol | str | No | Override the auto-resolved spot symbol (use the matching FUT for synthetic-forward mode) |
| underlying_exchange | str | No | Override the auto-resolved spot exchange |
| expiry_time | str | No | Override exchange default (`HH:MM`, e.g. `15:30`) |
| auth_token | str | Yes | Broker session token |
| broker | str | Yes | Broker plugin |
| config | dict | No | Broker config dict |

**Defaults (`EXCHANGE_EXPIRY_TIME_DEFAULT`):**

| Exchange | Expiry HH:MM (IST) |
|----------|--------------------|
| NFO/BFO | 15:30 |
| CDS | 12:30 |
| MCX | 23:30 |

**Example:**

```python
from backend.services.option_greeks_service import get_option_greeks

success, response, status_code = get_option_greeks(
    option_symbol="NIFTY28OCT2525950CE",
    exchange="NFO",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "symbol": "NIFTY28OCT2525950CE",
  "exchange": "NFO",
  "underlying": "NIFTY",
  "strike": 25950.0,
  "option_type": "CE",
  "expiry_date": "28-Oct-2025",
  "days_to_expiry": 8.234,
  "spot_price": 25966.05,
  "option_price": 142.5,
  "interest_rate": 0.0,
  "implied_volatility": 13.42,
  "greeks": {
    "delta": 0.5302,
    "gamma": 0.000845,
    "theta": -8.4351,
    "vega": 17.6235,
    "rho": -0.039
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### SyntheticFuture

Compute the synthetic future price from ATM CE+PE quotes:

```
Synthetic = Strike + Call_LTP - Put_LTP
Basis = Synthetic - Spot_LTP
```

Internally calls `get_option_symbol` twice (CE + PE ATM) then `get_multi_quotes_with_auth` for both legs.

**Function:** `calculate_synthetic_future(underlying, exchange, expiry_date, auth_token, broker, config=None)`

**Location:** `backend/services/synthetic_future_service.py:18`

**Example:**

```python
from backend.services.synthetic_future_service import calculate_synthetic_future

success, response, status_code = calculate_synthetic_future(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "underlying_ltp": 25966.05,
  "expiry": "28OCT25",
  "atm_strike": 25950.0,
  "call_symbol": "NIFTY28OCT2525950CE",
  "call_ltp": 142.5,
  "put_symbol": "NIFTY28OCT2525950PE",
  "put_ltp": 124.3,
  "synthetic_future_price": 25968.2,
  "basis": 2.15
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

## Account Services

### Funds

Account balance / margin available. Loads `broker.{name}.api.funds.get_margin_data(auth_token, config)`.

**Function:** `get_funds_with_auth(auth_token, broker, config=None, user_id=None)`

**Location:** `backend/services/funds_service.py:24`

A wrapper `get_funds(api_key=None, auth_token=None, broker=None, config=None, user_id=None)` (`backend/services/funds_service.py:61`) is also exported.

**Example:**

```python
from backend.services.funds_service import get_funds_with_auth

success, response, status_code = get_funds_with_auth(
    auth_token=auth_token, broker=broker_name, user_id=user_id,
)
```

**Response:**

```json
{
  "status": "success",
  "data": {
    "availablecash": 50000.0,
    "collateral": 0.0,
    "m2munrealized": 0.0,
    "m2mrealized": 0.0,
    "utiliseddebits": 0.0
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### Margin

Pre-trade margin calculator for a basket of positions (max `MAX_POSITIONS = 50`). Loads `broker.{name}.api.margin_api.calculate_margin_api`.

**Function:** `calculate_margin(margin_data, auth_token, broker, config=None)`

**Location:** `backend/services/margin_service.py:87`

**Margin Data Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| positions | list[dict] | Yes | Position objects (1-50) |

Each position must include `exchange`, `symbol`, `action`, `quantity`, `product`, `pricetype`. Optional `price` (default 0). Validation runs against the same enum constants as PlaceOrder.

**Example:**

```python
from backend.services.margin_service import calculate_margin

success, response, status_code = calculate_margin(
    margin_data={
        "positions": [
            {"symbol": "NIFTY28OCT2525950CE", "exchange": "NFO",
             "action": "BUY", "quantity": "75", "pricetype": "MARKET",
             "product": "NRML", "price": "0"},
            {"symbol": "NIFTY28OCT2525950PE", "exchange": "NFO",
             "action": "SELL", "quantity": "75", "pricetype": "MARKET",
             "product": "NRML", "price": "0"},
        ],
    },
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:** Broker-shaped success body (passed through verbatim):

```json
{
  "status": "success",
  "data": {
    "total_margin": 142500.0,
    "span_margin": 95000.0,
    "exposure_margin": 47500.0
  }
}
```

Returns 501 with an error message if the broker plugin lacks `calculate_margin_api`.

**Returns:** `(success, response_data, http_status_code)`.

---

## Analytics & Tools Services

### MaxPain

Compute the Max Pain strike — the candidate expiry settle that minimises total option-buyer loss. Pulls a 45-strike chain, then for each candidate `k`:

```
pain[k] = sum_i max(k - K_i, 0) * ce_oi_i  +  sum_i max(K_i - k, 0) * pe_oi_i
```

The candidate with smallest `total_pain` is the Max Pain strike.

**Function:** `get_max_pain_data(underlying, exchange, expiry_date, auth_token, broker, config=None)`

**Location:** `backend/services/max_pain_service.py:63`

**Example:**

```python
from backend.services.max_pain_service import get_max_pain_data

success, response, status_code = get_max_pain_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "spot_price": 25966.05,
  "quote_symbol": "NIFTY",
  "quote_exchange": "NSE_INDEX",
  "atm_strike": 25950.0,
  "max_pain_strike": 26000.0,
  "total_ce_oi": 12345678,
  "total_pe_oi": 11234567,
  "pcr_oi": 0.91,
  "expiry_date": "28OCT25",
  "chain": [
    {"strike": 25500.0, "ce_oi": 234000, "pe_oi": 1234567, "total_pain": 998765432.10},
    {"strike": 25950.0, "ce_oi": 1234567, "pe_oi": 1234567, "total_pain": 50123456.78},
    {"strike": 26000.0, "ce_oi": 2345678, "pe_oi": 2123456, "total_pain": 49876543.21}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### OITracker

Open-interest snapshot per strike with PCR, totals, and the matching-expiry futures price.

**Function:** `get_oi_tracker_data(underlying, exchange, expiry_date, auth_token, broker, config=None)`

**Location:** `backend/services/oi_tracker_service.py:91`

`_find_futures_symbol` (`backend/services/oi_tracker_service.py:24`) tries `BASE{DDMMMYY}FUT` first; on miss, falls back to the nearest-month FUT for the same underlying.

**Example:**

```python
from backend.services.oi_tracker_service import get_oi_tracker_data

success, response, status_code = get_oi_tracker_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "spot_price": 25966.05,
  "futures_price": 25985.5,
  "lot_size": 75,
  "pcr_oi": 0.91,
  "pcr_volume": 0.85,
  "total_ce_oi": 12345678,
  "total_pe_oi": 11234567,
  "atm_strike": 25950.0,
  "expiry_date": "28OCT25",
  "chain": [
    {"strike": 25500.0, "ce_oi": 234000, "pe_oi": 1234567},
    {"strike": 25950.0, "ce_oi": 1234567, "pe_oi": 1234567}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### IVSmile

Implied Volatility per strike for one expiry, plus ATM IV and a 25-delta proxy skew (PE IV at ~ATM-5% minus CE IV at ~ATM+5%).

**Function:** `get_iv_smile_data(underlying, exchange, expiry_date, auth_token, broker, config=None)`

**Location:** `backend/services/iv_smile_service.py:20`

Internally pulls a 25-strike chain and calls `calculate_greeks` per leg to recover IV.

**Example:**

```python
from backend.services.iv_smile_service import get_iv_smile_data

success, response, status_code = get_iv_smile_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response:**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "spot_price": 25966.05,
  "atm_strike": 25950.0,
  "atm_iv": 13.55,
  "skew": 1.20,
  "expiry_date": "28OCT25",
  "chain": [
    {"strike": 25500.0, "ce_iv": 14.85, "pe_iv": 14.20},
    {"strike": 25950.0, "ce_iv": 13.42, "pe_iv": 13.68},
    {"strike": 26400.0, "ce_iv": 13.18, "pe_iv": 13.95}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### IVChart

Intraday IV + Greeks time series for the ATM CE and PE of a given underlying/expiry. Aligns underlying / CE / PE OHLCV histories on common timestamps and solves Black-76 IV at every bar.

**Function:** `get_iv_chart_data(underlying, exchange, expiry_date, interval, days, auth_token, broker, config=None, interest_rate=None)`

**Location:** `backend/services/iv_chart_service.py:74`

Constraints:

- `interval` must be one of `1m`, `5m`, `15m`, `30m`, `1h`, `D` (`_SUPPORTED_INTERVALS`).
- `days` must be in `[1, 30]`.
- `exchange` must resolve to `NFO` or `BFO` — Greeks history is NSE/BSE-only.

**Example:**

```python
from backend.services.iv_chart_service import get_iv_chart_data

success, response, status_code = get_iv_chart_data(
    underlying="NIFTY",
    exchange="NFO",
    expiry_date="28OCT25",
    interval="5m",
    days=3,
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "data": {
    "underlying": "NIFTY",
    "underlying_ltp": 25966.05,
    "atm_strike": 25950.0,
    "ce_symbol": "NIFTY28OCT2525950CE",
    "pe_symbol": "NIFTY28OCT2525950PE",
    "interval": "5m",
    "days": 3,
    "expiry_date": "28OCT25",
    "interest_rate": 0.0,
    "series": [
      {
        "symbol": "NIFTY28OCT2525950CE",
        "option_type": "CE",
        "strike": 25950.0,
        "iv_data": [
          {"time": 1761625800, "iv": 13.42, "delta": 0.5302,
           "gamma": 0.000845, "theta": -8.43, "vega": 17.62,
           "option_price": 142.5, "underlying_price": 25966.05}
        ]
      }
    ]
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### VolSurface

Rectangular IV grid across `(strikes × expiries)`. For each expiry, builds the OTM-leg list (CE for strikes ≥ ATM, PE for strikes < ATM), batch-quotes, and solves Black-76 IV per strike.

**Function:** `get_vol_surface_data(underlying, exchange, expiry_dates, strike_count, auth_token, broker, config=None)`

**Location:** `backend/services/vol_surface_service.py:51`

Off-hours fallback: when `ltp` is 0/missing the row falls back to `close`/`prev_close` so the surface still renders outside market hours.

**Example:**

```python
from backend.services.vol_surface_service import get_vol_surface_data

success, response, status_code = get_vol_surface_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_dates=["28OCT25", "04NOV25", "11NOV25"],
    strike_count=10,
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "data": {
    "underlying": "NIFTY",
    "underlying_ltp": 25966.05,
    "atm_strike": 25950.0,
    "strikes": [25450.0, 25500.0, 25550.0, 25600.0, 25650.0],
    "expiries": [
      {"date": "28OCT25", "dte": 8.2},
      {"date": "04NOV25", "dte": 15.2},
      {"date": "11NOV25", "dte": 22.2}
    ],
    "surface": [
      [14.95, 14.50, 13.95, 13.42, 13.18],
      [14.65, 14.25, 13.85, 13.55, 13.40],
      [14.40, 14.15, 13.85, 13.65, 13.55]
    ]
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### StraddleChart

Dynamic-ATM straddle time series with synthetic-future overlay. For each underlying candle, recomputes ATM from its close, looks up the matching CE+PE candle, and emits:

```
Straddle = CE_close + PE_close
SyntheticFuture = ATM + CE_close - PE_close
```

**Function:** `get_straddle_chart_data(underlying, exchange, expiry_date, interval, auth_token, broker, config=None, days=5)`

**Location:** `backend/services/straddle_chart_service.py:83`

**Constraints:** `days` must be in `[1, 30]`. Calendar window is padded by `2*days+2` to ride through weekends/holidays.

**Example:**

```python
from backend.services.straddle_chart_service import get_straddle_chart_data

success, response, status_code = get_straddle_chart_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    interval="5m",
    days=3,
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "data": {
    "underlying": "NIFTY",
    "underlying_ltp": 25966.05,
    "expiry_date": "28OCT25",
    "interval": "5m",
    "days_to_expiry": 8,
    "series": [
      {"time": 1761625800, "spot": 25960.5, "atm_strike": 25950.0,
       "ce_price": 142.5, "pe_price": 124.3,
       "straddle": 266.8, "synthetic_future": 25968.2}
    ]
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### GEX

Per-strike Gamma Exposure (GEX) from live OI plus Black-76 gamma:

```
GEX_leg = gamma * OI * lot_size
Net_GEX = CE_GEX - PE_GEX
```

**Function:** `get_gex_data(underlying, exchange, expiry_date, auth_token, broker, config=None)`

**Location:** `backend/services/gex_service.py:23`

Internally pulls a 45-strike chain and calls `calculate_greeks` per leg, then queries the matching-expiry FUT price via `oi_tracker_service._get_futures_price`.

**Example:**

```python
from backend.services.gex_service import get_gex_data

success, response, status_code = get_gex_data(
    underlying="NIFTY",
    exchange="NSE_INDEX",
    expiry_date="28OCT25",
    auth_token=auth_token,
    broker=broker_name,
)
```

**Response (truncated):**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "spot_price": 25966.05,
  "futures_price": 25985.5,
  "lot_size": 75,
  "atm_strike": 25950.0,
  "expiry_date": "28OCT25",
  "pcr_oi": 0.91,
  "total_ce_oi": 12345678,
  "total_pe_oi": 11234567,
  "total_ce_gex": 12345.67,
  "total_pe_gex": 11234.56,
  "total_net_gex": 1111.11,
  "chain": [
    {"strike": 25950.0, "ce_oi": 234567, "pe_oi": 234567,
     "ce_gamma": 0.000845, "pe_gamma": 0.000821,
     "ce_gex": 14854.32, "pe_gex": 14431.20, "net_gex": 423.12}
  ]
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

## Sandbox Service

The sandbox layer (`backend/services/sandbox_service.py`) is the simulated broker used when the global trading mode is `sandbox`. Every order-path service in `backend/services/` checks `get_trading_mode_sync() == "sandbox"` at the top of its `*_with_auth` function and dispatches here when the user_id is known. The function signatures below take `user_id` directly because the auth resolution is a no-op in sandbox mode.

| Function | Location | Description |
|----------|----------|-------------|
| `place_order(user_id, order_data)` | `sandbox_service.py:53` | Validates symbol/lot/tick/product, blocks margin via `fund_manager`, creates an order row, opportunistically fills MARKET orders at the cached LTP. |
| `modify_order(user_id, data)` | `sandbox_service.py:224` | Updates qty / price / trigger / pricetype on a non-final order. |
| `cancel_order(user_id, orderid)` | `sandbox_service.py:242` | Cancels and releases blocked margin. |
| `cancel_all_orders(user_id)` | `sandbox_service.py:252` | Bulk cancel of every cancellable order. |
| `close_all_positions(user_id)` | `sandbox_service.py:263` | Submits opposite-direction MARKET orders to flatten every open position. |
| `close_position(user_id, symbol, exchange, product)` | `sandbox_service.py:356` | Flatten one specific position. |
| `place_smart_order(user_id, order_data)` | `sandbox_service.py:389` | Computes delta from current netqty to the requested `position_size`, places the difference. |
| `get_orderbook(user_id)` | `sandbox_service.py:292` | Sandbox orderbook with statistics, response shape mirrors live orderbook. |
| `get_tradebook(user_id)` | `sandbox_service.py:324` | Sandbox executed trades. |
| `get_positions(user_id)` | `sandbox_service.py:329` | Sandbox open positions. |
| `get_holdings(user_id)` | `sandbox_service.py:334` | T+1-settled CNC holdings (populated by the EOD settlement scheduler). |
| `get_order_status(user_id, orderid)` | `sandbox_service.py:342` | Single-order shape compatible with live `OrderStatus`. |
| `get_funds(user_id)` | `sandbox_service.py:426` | Snapshot of sandbox cash / margin. |

All sandbox functions return the same `(success, response_data, http_status_code)` tuple as their live counterparts. Successful responses include `"mode": "sandbox"` for diagnostics.

**MARKET-order pricing:** Sandbox attempts to fill MARKET orders at the cached LTP from `MarketDataCache` (live tick stream). If the cache is empty it falls back to a broker quote API call via `backend.sandbox.quote_helper.get_ltp`. If neither yields a price the order is **rejected** rather than silently accepted with `price=0`.

---

## Trading Mode Service

Global `live` vs `sandbox` flag stored in `app_settings` (key = `trading_mode`). Two callers:

- **Async path** (`get_trading_mode`, `set_trading_mode`) — used by REST endpoints.
- **Sync path** (`get_trading_mode_sync`) — used by sync service code (every order/info service top-level dispatch).

A 10-second in-memory cache (`_CACHE_TTL_SECONDS`) avoids a DB hit per request. The cache is invalidated immediately on writes.

**Public functions (`backend/services/trading_mode_service.py`):**

| Function | Location | Description |
|----------|----------|-------------|
| `get_trading_mode_sync()` | line 60 | Returns `"live"` or `"sandbox"`. Reads cache then falls back to a sync DB query. |
| `get_trading_mode(db=None)` | line 96 | Async equivalent. |
| `set_trading_mode(db, mode)` | line 134 | Write-through update; raises `ValueError` on invalid mode. |
| `is_sandbox(mode)` / `is_live(mode)` | lines 153-158 | Boolean helpers. |
| `dispatch_by_mode(live_fn, sandbox_fn, *args, **kwargs)` | line 166 | Async dispatcher; falls back to `live_fn` if `sandbox_fn` is `None`. |
| `invalidate_cache()` | line 53 | Force the cache to refetch on next read. |

`VALID_TRADING_MODES = {"live", "sandbox"}` is defined in `backend/models/settings.py`.

---

## Strategy Builder & Portfolio

User-curated multi-leg option strategies with persistent storage, live snapshot pricing, and historical combined-premium charting. Backs the `/tools/strategybuilder` and `/tools/strategyportfolio` UI pages. Three distinct service surfaces:

1. **CRUD** over the `strategies` table — `backend/routers/strategies.py` calls SQLAlchemy directly (no separate service module — the persistence is thin enough to live in the router).
2. **Snapshot** — `backend/services/strategy_builder_service.py:get_strategy_snapshot()` — single round-trip live pricing for a leg set.
3. **Chart** — `backend/services/strategy_chart_service.py:get_strategy_chart_data()` — generalises `straddle_chart_service` to an arbitrary leg list.

All three are session-cookie-authed under `/web/*` (not `/api/v1/*`) — these are internal UI endpoints, not external API. Per-user isolation: every CRUD query filters on the authenticated user's id.

### SaveStrategy / List / Get / Update / Delete

REST CRUD over the `strategies` table.

**Router:** `backend/routers/strategies.py` (no separate service file).

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/web/strategies` | List for the current user (filters: `mode`, `status`, `underlying`). Newest first. |
| `GET` | `/web/strategies/{id}` | Fetch one (404 if not owned by the user). |
| `POST` | `/web/strategies` | Create. |
| `PUT` | `/web/strategies/{id}` | Partial update. Setting `status="closed"` from any other status auto-stamps `closed_at`. |
| `DELETE` | `/web/strategies/{id}` | Hard delete. |

**Schemas (`backend/schemas/strategies.py`):**

`StrategyLeg` is the unit of persistence inside the JSONB column:

```python
class StrategyLeg(BaseModel):
    id: Optional[str]               # client UUID for stable React keys
    action: Literal["BUY", "SELL"]
    option_type: Literal["CE", "PE"]
    strike: float
    lots: int
    lot_size: Optional[int]
    expiry_date: Optional[str]      # per-leg "DDMMMYY" — supports calendar/diagonal
    symbol: Optional[str]           # resolved option symbol
    entry_price: float = 0.0
    exit_price: Optional[float]
    status: Literal["open", "closed", "expired"] = "open"
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
```

`StrategyCreate` requires `name`, `underlying`, `exchange`, `legs: List[StrategyLeg]`. Optional: `expiry_date`, `mode` (`"live"` / `"sandbox"`, default `"live"`), `notes`.

`StrategyUpdate` is fully partial — only fields present in the body are touched. The router also validates that `status` transitions follow `{active, closed, expired}` and stamps `closed_at` server-side.

**Example — list active sandbox strategies on NIFTY:**

```bash
curl -b "access_token=..." \
  "http://127.0.0.1:8000/web/strategies?mode=sandbox&status=active&underlying=NIFTY"
```

```json
{
  "status": "success",
  "strategies": [
    {
      "id": 7,
      "user_id": 1,
      "name": "Iron Condor 02-MAY",
      "underlying": "NIFTY",
      "exchange": "NSE_INDEX",
      "expiry_date": "02MAY26",
      "mode": "sandbox",
      "status": "active",
      "legs": [
        {"action":"BUY","option_type":"CE","strike":26000,"lots":1,"lot_size":75,"symbol":"NIFTY02MAY2626000CE","entry_price":20.0,"status":"open"},
        {"action":"SELL","option_type":"CE","strike":25500,"lots":1,"lot_size":75,"symbol":"NIFTY02MAY2625500CE","entry_price":60.0,"status":"open"},
        {"action":"SELL","option_type":"PE","strike":24500,"lots":1,"lot_size":75,"symbol":"NIFTY02MAY2624500PE","entry_price":80.0,"status":"open"},
        {"action":"BUY","option_type":"PE","strike":24000,"lots":1,"lot_size":75,"symbol":"NIFTY02MAY2624000PE","entry_price":30.0,"status":"open"}
      ],
      "notes": null,
      "created_at": "2026-04-29T20:54:36+05:30",
      "updated_at": "2026-04-29T20:54:36+05:30",
      "closed_at": null
    }
  ]
}
```

---

### StrategySnapshot

Single-shot live pricing for a leg set: spot + per-leg LTP + IV + Greeks + position-aggregated totals.

**Function:** `get_strategy_snapshot(legs, underlying, exchange, auth_token, broker, config=None, options_exchange=None, interest_rate=None, expiry_time=None)`

**Location:** `backend/services/strategy_builder_service.py:97`

**Endpoint:** `POST /web/strategybuilder/snapshot`

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `underlying` | str | Yes | Base symbol (e.g. `NIFTY`). |
| `exchange` | str | No | Spot/forward exchange (auto-resolved from underlying if omitted). |
| `options_exchange` | str | No | Default leg exchange — `NFO`, `BFO`, `CDS`, `MCX`. Default `NFO`. |
| `interest_rate` | float | No | Annualized %; per-exchange default if omitted (`0` for NFO/BFO/CDS/MCX). |
| `expiry_time` | str | No | `HH:MM` — overrides the per-exchange expiry time used for `T`. |
| `legs` | array | Yes | One or more `SnapshotLeg`. |

**SnapshotLeg fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `symbol` | str | Yes | Resolved option symbol (e.g. `NIFTY02MAY2625000CE`). |
| `action` | `"BUY"` / `"SELL"` | Yes | Sign convention: BUY = +1, SELL = −1. |
| `lots` | int | Yes | Number of lots. |
| `lot_size` | int | Yes | Shares per lot. |
| `exchange` | str | No | Per-leg exchange override (rare; default = `options_exchange`). |
| `entry_price` | float | No | Supplying this turns on `unrealized_pnl` per leg and in totals. |

**How it works (lines 97-263):**

1. Validate every leg's basic shape up front so we never fan out a partial fetch.
2. Fetch the underlying spot via `get_quotes_with_auth(underlying, spot_exchange)` — one broker call.
3. Build a deduped multi-quotes request for every leg's symbol; fetch via `get_multi_quotes_with_auth()` — one broker call regardless of leg count.
4. For each leg, run the pure-math `option_greeks_service.calculate_greeks()` against the live spot + LTP. No re-hits to the broker.
5. Aggregate position-level Greeks: `Σ (sign × lots × lot_size × leg_greek)`.
6. Net premium: `Σ (sign × lots × lot_size × ltp)`. Positive = net debit, negative = net credit.
7. Stamp `as_of` in IST so the UI doesn't have to TZ-convert.

**Per-leg errors are non-fatal** — a leg with an unparseable symbol or below-intrinsic LTP returns the leg with an `error` or `note` field and zero Greeks; the rest of the snapshot still renders. This matches the deep-ITM theoretical-Greeks fallback in `option_greeks_service`.

**Example response (NIFTY long straddle, spot=24177.65, K=25000, 5.77 DTE):**

```json
{
  "status": "success",
  "underlying": "NIFTY",
  "exchange": "NSE_INDEX",
  "spot_price": 24177.65,
  "as_of": "2026-04-29T20:54:36+05:30",
  "legs": [
    {
      "index": 0,
      "symbol": "NIFTY05MAY2625000CE",
      "exchange": "NFO",
      "action": "BUY",
      "lots": 1,
      "lot_size": 75,
      "underlying": "NIFTY",
      "strike": 25000.0,
      "option_type": "CE",
      "expiry_date": "05-May-2026",
      "days_to_expiry": 5.7746,
      "ltp": 120.0,
      "implied_volatility": 32.84,
      "greeks": {"delta": 0.215, "gamma": 0.000293, "theta": -25.27, "vega": 8.89, "rho": -0.019},
      "position_premium": 9000.0,
      "entry_price": 100.0,
      "unrealized_pnl": 1500.0
    },
    {
      "index": 1,
      "symbol": "NIFTY05MAY2625000PE",
      "exchange": "NFO",
      "action": "BUY",
      "lots": 1,
      "lot_size": 75,
      "strike": 25000.0,
      "option_type": "PE",
      "ltp": 95.0,
      "implied_volatility": 0.0,
      "greeks": {"delta": -1.0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0},
      "note": "Deep ITM option with no time value - theoretical Greeks returned",
      "position_premium": 7125.0,
      "entry_price": 110.0,
      "unrealized_pnl": -1125.0
    }
  ],
  "totals": {
    "premium_paid": 16125.0,
    "delta": -58.875,
    "gamma": 0.022,
    "theta": -1895.59,
    "vega": 666.49,
    "rho": -1.42,
    "unrealized_pnl": 375.0
  }
}
```

**Returns:** `(success, response_data, http_status_code)`.

---

### StrategyChart

Historical combined-premium time series for an arbitrary leg list — generalises `straddle_chart_service` (which walks the underlying and recomputes ATM per candle) to a fixed-leg setup.

**Function:** `get_strategy_chart_data(legs, underlying, exchange, interval, auth_token, broker, config=None, options_exchange=None, days=5, include_underlying=True)`

**Location:** `backend/services/strategy_chart_service.py:67`

**Endpoint:** `POST /web/strategybuilder/chart`

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `underlying` | str | Yes | Base symbol. |
| `exchange` | str | No | Spot exchange — auto-resolved if omitted. |
| `options_exchange` | str | No | Default leg exchange. |
| `interval` | str | Yes | Broker-specific interval (`1m`, `5m`, `1h`, `D`, ...). |
| `days` | int | No | Trading-day window (1-60). Default 5. |
| `include_underlying` | bool | No | Set false to skip the underlying overlay fetch. |
| `legs` | array | Yes | Same `ChartLeg` shape as `SnapshotLeg`. |

**How it works (lines 67-228):**

1. Validate leg shape; reject empties so we never fan out a partial fetch.
2. Calendar window: `today − (days × 2 + 2)` so a 1-day request still finds Friday's session on a Monday morning.
3. Fetch the underlying history via `get_history_with_auth()`. On failure (Zerodha indices on `1m`, e.g.), set `underlying_available=false` and continue — leg-only chart still renders.
4. Fetch each leg's history sequentially. Build a `{ts -> close}` map per leg.
5. **Intersect timestamps** across every populated leg map. Any timestamp where one leg lacks a close is dropped — eliminates phantom dips when one broker candle is delayed (the same correctness call OpenAlgo's strategy chart makes).
6. For each surviving timestamp, compute `value = Σ (sign × lots × lot_size × close)`. If every leg has `entry_price`, also stamp `pnl = value(t) − entry_premium_constant`.
7. Trim to the last `days` distinct trading dates so the day window is honored, propagating the cutoff to per-leg + underlying series.
8. Fetch current `underlying_ltp` for the header card.

**Sign convention** matches `StrategySnapshot.totals.premium_paid` exactly so cross-tab numbers agree.

**Example response (long straddle, 5m × 5d):**

```json
{
  "status": "success",
  "data": {
    "underlying": "NIFTY",
    "underlying_ltp": 24177.65,
    "exchange": "NSE_INDEX",
    "interval": "5m",
    "days": 5,
    "underlying_available": true,
    "underlying_series": [{"time": 1714330000, "close": 24100.0}, ...],
    "leg_series": [
      {
        "index": 0, "symbol": "NIFTY05MAY2625000CE", "exchange": "NFO",
        "action": "BUY", "lots": 1, "lot_size": 75,
        "series": [{"time": 1714330000, "close": 95.0}, ...],
        "entry_price": 100.0
      },
      {"index": 1, "symbol": "NIFTY05MAY2625000PE", ...}
    ],
    "combined_series": [
      {"time": 1714330000, "value": 15375.0, "pnl": -375.0},
      {"time": 1714330300, "value": 15375.0, "pnl": -375.0},
      {"time": 1714330600, "value": 15750.0, "pnl": 0.0}
    ],
    "entry_premium": 15750.0
  }
}
```

`entry_premium` is null when any leg lacks `entry_price`. In that case `combined_series[*].pnl` is also absent — the chart falls back to the value series.

**Returns:** `(success, response_data, http_status_code)`. The 200 path is wrapped in a `data` key (matches the OpenAlgo straddle-chart shape); error responses are flat `{status, message}`.

---

## Market Data Cache

Process-wide singleton (`MarketDataCache` in `backend/services/market_data_cache.py`) — every WebSocket tick flows through `process_market_data()`. The cache is the single source of live LTP / quote / depth for any internal consumer.

**Constructor:** `get_market_data_cache()` (`backend/services/market_data_cache.py:543`) returns the singleton.

### Public methods

| Method | Location | Description |
|--------|----------|-------------|
| `process_market_data(data)` | line 224 | Validate, cache, fan out a tick. Returns `True` on success. |
| `subscribe(priority, event_type, callback, filter_symbols=None, name="")` | line 305 | Register a callback at one of 4 priorities (`CRITICAL`/`HIGH`/`NORMAL`/`LOW`). Returns subscriber id. |
| `subscribe_critical(callback, filter_symbols=None, name="rms")` | line 326 | Shortcut for CRITICAL-priority LTP subscribers (RMS / stoploss). |
| `unsubscribe(subscriber_id)` | line 335 | Remove a subscriber by id. |
| `get_ltp(symbol, exchange)` | line 345 | Returns `{value, timestamp, volume}` or `None`. |
| `get_ltp_value(symbol, exchange)` | line 354 | Just the LTP float, or `None`. |
| `get_quote(symbol, exchange)` | line 358 | OHLC quote dict (mode 2 ticks). |
| `get_depth(symbol, exchange)` | line 367 | 5-level depth dict (mode 3 ticks). |
| `get_all(symbol, exchange)` | line 376 | Full cached entry. |
| `set_connected(connected, authenticated=False)` | line 383 | Called by the WS proxy on connection-state changes. |
| `is_data_fresh(symbol=None, exchange=None, max_age_seconds=30)` | line 404 | Freshness check. |
| `is_trade_management_safe()` | line 419 | RMS gate — `(safe, reason)` tuple; call before acting on cached prices. |
| `get_health_status()` | line 431 | `HealthStatus` dataclass — feed health, subscriber counts, metrics. |
| `get_metrics()` | line 458 | Cache hit rate, validation errors, total updates, etc. |
| `stop()` | line 532 | Stop the background health thread on shutdown. |

Module-level convenience wrappers (`process_market_data`, `get_ltp`, `get_ltp_value`, `get_quote`, `get_depth`, `subscribe_critical`, `is_data_fresh`, `is_trade_management_safe`, `get_health_status`) all proxy to the singleton.

### Subscriber priorities

```python
class SubscriberPriority(IntEnum):
    CRITICAL = 1   # RMS / trade management — sees every tick first
    HIGH = 2       # Price alerts, monitoring
    NORMAL = 3     # Watchlists, general display
    LOW = 4        # Dashboards, analytics
```

Callbacks fire in priority order, snapshotted under the lock so the cache lock isn't held across user code.

### Tick modes

- `mode=1` LTP — populates `entry["ltp"]`.
- `mode=2` Quote — populates `entry["quote"]` and mirrors LTP into `entry["ltp"]`.
- `mode=3` Depth — populates `entry["depth"]` from `data.depth.{buy, sell}` or fallback `data.{bids, asks}`.

### Validation guards

`_Validator.validate` (`backend/services/market_data_cache.py:102`) enforces:
- `symbol` and `exchange` must be present.
- `LTP > 0` (depth-only ticks before LTP arrives are accepted with a warning).
- Tick timestamp staleness check (warn at `MAX_DATA_AGE_SECONDS = 60`).
- Circuit-breaker on `>20%` price jumps (`MAX_PRICE_CHANGE_PERCENT`).

### Health monitoring

A daemon thread (`MDS-Health`) flips `_connection_status → STALE` and sets `_trade_paused = True` if no ticks arrive for `MAX_DATA_GAP_SECONDS = 30` seconds.

### Redis mirror

Each tick is fire-and-forget mirrored to Redis (`md:{exchange}:{symbol}`) with `REDIS_CACHE_TTL = 60` seconds. Writes use `loop.call_soon_threadsafe(create_task(cache_set_json(...)))` so the tick hot path is never awaited.

---

## Common Patterns

### Dynamic broker loading

Every service that calls a broker API loads the plugin at runtime:

```python
def _import_broker_module(broker_name: str):
    return importlib.import_module(f"backend.broker.{broker_name}.api.order_api")

broker_module = _import_broker_module(broker)
res, response_data, order_id = broker_module.place_order_api(order_data, auth_token)
```

Standard plugin paths used across services:

| Module path | Used by |
|-------------|---------|
| `backend.broker.{name}.api.order_api` | order placement, orderbook, tradebook, positions, holdings, openposition |
| `backend.broker.{name}.api.data` | quotes, multi_quotes, depth, history, intervals |
| `backend.broker.{name}.api.funds` | funds (`get_margin_data`) |
| `backend.broker.{name}.api.margin_api` | margin calculator (`calculate_margin_api`) |
| `backend.broker.{name}.mapping.order_data` | response mapping (`map_*_data`, `transform_*_data`, `calculate_*_statistics`) |
| `backend.broker.{name}.database.master_contract_db` | master contract download, broker-side symbol search |

### Return signature

Every public service function returns:

```python
(success: bool, response_data: dict, http_status_code: int)
```

The API layer takes `response_data` and `http_status_code` and returns a `JSONResponse(content=response_data, status_code=status_code)`.

### Validation constants

Order-path services validate against shared sets in `backend/utils/constants.py`:

```python
VALID_EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "CDS", "BCD",
                   "MCX", "NCDEX", "NSE_INDEX", "BSE_INDEX", "MCX_INDEX"}
VALID_PRODUCT_TYPES = {"CNC", "NRML", "MIS"}
VALID_PRICE_TYPES   = {"MARKET", "LIMIT", "SL", "SL-M"}
VALID_ACTIONS       = {"BUY", "SELL"}
```

Required-field checks are uniformly:

```python
REQUIRED_ORDER_FIELDS = ["symbol", "exchange", "action", "quantity", "pricetype", "product"]
```

### Sync DB queries from sync service code

Several services (`market_data_service`, `option_symbol_service`, `option_chain_service`, `option_greeks_service`, `oi_tracker_service`, `symbol_service`) need to read the `symtoken` table from a synchronous service function. They wrap an async query in a thread:

```python
async def _query_db(query_str, params):
    engine = create_async_engine(get_settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(text(query_str), params)
            return result.fetchall()
    finally:
        await engine.dispose()


def _run_query(query_str, params):
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _query_db(query_str, params)).result()
```

`market_data_service._run_query` is re-exported by other services that need it.

### Sandbox dispatch

Order-path `*_with_auth` functions branch to the sandbox simulator at the top:

```python
if user_id is not None:
    try:
        from backend.services.trading_mode_service import get_trading_mode_sync
        if get_trading_mode_sync() == "sandbox":
            from backend.services.sandbox_service import place_order as sbx_place
            return sbx_place(user_id, order_data)
    except Exception:
        logger.exception("sandbox dispatch failed; falling back to live")
```

Functions that perform this dispatch: `place_order_with_auth`, `modify_order_service`, `cancel_order_service`, `cancel_all_orders_service`, `close_all_positions_service`, `get_orderbook_with_auth`, `get_tradebook_with_auth`, `get_positions_with_auth`, `get_holdings_with_auth`, `get_funds_with_auth`.

### Auth resolution

API endpoints resolve auth once via `get_api_user(request, db)` (`backend/dependencies.py`):

```python
async def _resolve_api_user(request: Request) -> tuple:
    from backend.dependencies import get_api_user, get_db
    async for db in get_db():
        result = await get_api_user(request, db)
        return result  # (user_id, auth_token, broker_name, config)
```

The dependency reads the `apikey` from the JSON body, hashes it with the configured pepper, looks it up in the `api_keys` table, fetches the user's broker session record, and returns the four-tuple. Services never see the API key directly.

### Redis caching keys

Auth and broker context are cached in Redis to avoid DB hits on every request:

| Key prefix | Description | TTL |
|------------|-------------|-----|
| `api_key:{hash}` | API-key → user_id mapping | session-bound |
| `api_ctx:{user_id}` | Cached `(user_id, auth_token, broker, config)` resolution | short |
| `broker_ctx:{user_id}` | Cached broker config + session for the user | short |
| `symtoken:{exchange}:{symbol}` | symtoken row cache (master contract lookup) | long |
| `md:{exchange}:{symbol}` | Live tick mirror from MarketDataCache | 60s (`REDIS_CACHE_TTL`) |

All Redis writes use `backend.utils.redis_client.cache_set_json` with explicit TTLs.

### Strikes cache

`option_symbol_service._STRIKES_CACHE` is a process-local in-memory dict keyed by `(base_symbol, expiry, option_type, exchange)` storing the sorted strike list pulled from `symtoken`. It avoids re-running the distinct-strike query on every option-chain build. Call `clear_strikes_cache()` after a master contract refresh.
