from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.config import get_settings
from backend.database import engine, Base
from backend.exceptions import OpenBullException, openbull_exception_handler
from backend.limiter import limiter
from backend.middleware import RequestLoggingMiddleware
from backend.utils.logging import get_logger, setup_logging
from backend.utils.plugin_loader import load_all_plugins
from backend.utils.httpx_client import close_httpx_client
from backend.utils.redis_client import close_redis

settings = get_settings()

# Centralized logging — installs console + rotating file handlers, sensitive-
# data redaction, request-id stamping, and a DB sink for WARNING+ records.
setup_logging(settings)
logger = get_logger("openbull")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("OpenBull starting up...")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")

    # Load broker plugins
    plugins = load_all_plugins()
    logger.info("Loaded %d broker plugins: %s", len(plugins), list(plugins.keys()))

    # Load symbol cache if symtoken table has data
    try:
        from backend.broker.upstox.mapping.order_data import _load_symbol_cache
        await _load_symbol_cache()
    except Exception as e:
        logger.info("Symbol cache not loaded (will load after master contract download): %s", e)

    # Start WebSocket proxy server in background
    import asyncio
    from backend.websocket_proxy.server import start_ws_proxy, shutdown_ws_proxy
    ws_task = asyncio.create_task(
        start_ws_proxy(settings.websocket_host, settings.websocket_port)
    )
    logger.info(
        "WebSocket proxy starting on ws://%s:%d", settings.websocket_host, settings.websocket_port
    )

    yield

    # Shutdown
    await shutdown_ws_proxy()
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass
    close_httpx_client()
    await close_redis()
    await engine.dispose()
    logger.info("OpenBull shut down")
    # Flush DB error sink before the process exits so in-flight queued
    # WARNING/ERROR records have a chance to persist.
    import logging as _std_logging
    _std_logging.shutdown()


app = FastAPI(
    title="OpenBull",
    description="Options Trading Platform for Indian Brokers",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(OpenBullException, openbull_exception_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # CSP
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: ws:; "
        "frame-ancestors 'none'"
    )
    return response


# Request-id + access log. Added last so it is the outermost wrapper —
# the request_id contextvar is set before any other middleware runs,
# and the latency line covers the full request lifecycle.
app.add_middleware(RequestLoggingMiddleware)


# Register routers
from backend.routers.auth import router as auth_router
from backend.routers.broker_config import router as broker_config_router
from backend.routers.broker_oauth import router as broker_oauth_router
from backend.routers.error_logs import router as error_logs_router

app.include_router(auth_router)
app.include_router(broker_config_router)
app.include_router(broker_oauth_router)
app.include_router(error_logs_router)

# Phase 3: Symbol search and master contract download
from backend.routers.symbols import router as symbols_router

app.include_router(symbols_router)

# Phase 4: Core trading web routes
from backend.routers.dashboard import router as dashboard_router
from backend.routers.orderbook import router as orderbook_router
from backend.routers.tradebook import router as tradebook_router
from backend.routers.positions import router as positions_router
from backend.routers.holdings import router as holdings_router

app.include_router(dashboard_router)
app.include_router(orderbook_router)
app.include_router(tradebook_router)
app.include_router(positions_router)
app.include_router(holdings_router)

# Phase 5: API key management and external API
from backend.routers.api_key import router as api_key_router
from backend.api import api_v1_router

app.include_router(api_key_router)
app.include_router(api_v1_router)

# WebSocket support endpoints (config, health, cached ticks)
from backend.routers.websocket import router as websocket_router

app.include_router(websocket_router)


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "app": "OpenBull", "version": "0.1.0"}
