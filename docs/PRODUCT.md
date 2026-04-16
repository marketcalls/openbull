# OpenBull Product Overview

## What is OpenBull?

OpenBull is a single-user options trading platform for Indian brokers. It provides a unified API layer across Upstox and Zerodha, enabling seamless integration with trading tools, custom scripts, and AI agents.

## Core Capabilities

### 1. Unified Broker API (31 endpoints)

A single REST API that works identically regardless of which broker is connected. Switch from Upstox to Zerodha without changing a line of client code.

**Order Management (10 endpoints):**
PlaceOrder, PlaceSmartOrder, BasketOrder, SplitOrder, OptionsOrder, OptionsMultiOrder, ModifyOrder, CancelOrder, CancelAllOrder, ClosePosition

**Account & Books (8 endpoints):**
Funds, Margin, OrderBook, TradeBook, PositionBook, Holdings, OrderStatus, OpenPosition

**Market Data (5 endpoints):**
Quotes, MultiQuotes, Depth, History, Intervals

**Symbol Services (3 endpoints):**
Symbol, Search, Expiry (with instrumenttype filter)

**Options Analytics (4 endpoints):**
OptionSymbol, OptionChain, OptionGreeks, SyntheticFuture

**Health:** Ping

### 2. Options-First Design

Built for options traders:
- **OptionSymbol**: Resolve "NIFTY ATM CE" to "NIFTY28APR2624250CE" automatically using live LTP
- **OptionChain**: Full CE+PE chain with LTP, OI, volume around ATM
- **OptionGreeks**: Black-76 implied volatility, delta, gamma, theta, vega computed on-demand
- **SyntheticFuture**: Calculate synthetic future price and basis from ATM CE+PE
- **OptionsOrder**: Place orders using offset (ATM/ITM3/OTM5) instead of exact symbols
- **OptionsMultiOrder**: Execute Iron Condor, Straddle, Spread as a single API call with BUY-first execution for margin efficiency
- **Margin**: Pre-trade margin calculator with hedging benefit for multi-leg strategies

### 3. Real-Time WebSocket Streaming

ZeroMQ-based architecture for low-latency market data:
- **LTP mode**: Last traded price with 50ms throttle
- **Quote mode**: OHLCV + OI + volume
- **Depth mode**: 5-level bid/ask market depth
- Supports Upstox protobuf feed and Zerodha KiteTicker binary protocol
- Auto-reconnect with exponential backoff and health monitoring

### 4. Plug-and-Play Broker System

Each broker lives in `backend/broker/{name}/` with a standardized structure:
```
broker/{name}/
  api/          # REST: auth, orders, funds, data, margin
  mapping/      # Format translation (OpenBull <-> broker)
  streaming/    # WebSocket adapter
  database/     # Master contract download
  plugin.json   # Metadata
```

Adding a new broker = implement the modules, drop in the folder, add to `VALID_BROKERS`.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async), Python 3.12+ |
| Database | PostgreSQL 15+ (SQLAlchemy async) |
| Frontend | React 19, Vite, TypeScript, ShadcnUI, TanStack Query |
| Security | Argon2 (passwords), Fernet (encryption), JWT (httpOnly cookies), CSP, CORS |
| Streaming | ZeroMQ PUB/SUB, websocket-client, protobuf |
| Package Mgr | uv (Python), npm (Node) |

## Supported Brokers

| Broker | REST API | WebSocket | Master Contract |
|--------|----------|-----------|-----------------|
| Upstox | Full | Protobuf v3 | Auto-download |
| Zerodha | Full | KiteTicker binary | Auto-download |

## Supported Exchanges

NSE, BSE, NFO, BFO, CDS, BCD, MCX, NCDEX, NSE_INDEX, BSE_INDEX, MCX_INDEX

## Authentication

- **Web UI**: JWT in httpOnly cookies with IST-aware session expiry (default 3:00 AM)
- **External API**: API key in request body (`apikey`) or header (`X-API-KEY`)
- **WebSocket**: API key sent in authenticate message
- **Broker OAuth**: OAuth2 redirect flow per broker

## Who Is It For?

- **Algo traders**: Build Python/Node strategies that place orders via REST API
- **Options traders**: Use offset-based ordering and Greeks without manual symbol lookup
- **Tool builders**: Integrate with TradingView, Amibroker, Excel via the standard API
- **AI agents**: Clean JSON API surface for LLM-driven trading workflows
