import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.config import get_settings
from backend.database import engine, Base
from backend.exceptions import OpenBullException, openbull_exception_handler
from backend.utils.plugin_loader import load_all_plugins
from backend.utils.httpx_client import close_httpx_client

settings = get_settings()

# Logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("openbull")

# Rate limiter
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")


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
    await engine.dispose()
    logger.info("OpenBull shut down")


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


# Register routers
from backend.routers.auth import router as auth_router
from backend.routers.broker_config import router as broker_config_router
from backend.routers.broker_oauth import router as broker_oauth_router

app.include_router(auth_router)
app.include_router(broker_config_router)
app.include_router(broker_oauth_router)

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


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "app": "OpenBull", "version": "0.1.0"}


# Serve React frontend in production (from frontend/dist/)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve index.html for all non-API routes (SPA catch-all)
        index_file = frontend_dist / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse(status_code=404, content={"detail": "Frontend not built"})
