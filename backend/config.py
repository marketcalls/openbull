from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Core secrets
    app_secret_key: str
    encryption_pepper: str

    # Database
    database_url: str = "postgresql+asyncpg://postgres:123456@localhost:5432/openbull"

    # Server
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_url: str = "http://127.0.0.1:5173"
    flask_debug: bool = False

    # CORS
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # Brokers
    valid_brokers: str = "upstox,zerodha"

    # Logging
    log_level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "logs"
    log_colors: bool = True
    # File rotation. Total on-disk footprint per log file is bounded by
    # (backup_count + 1) * max_mb. Defaults give a 100 MB hard cap per file
    # (openbull.log and openbull-error.log each), 200 MB total on disk.
    log_file_max_mb: int = 10
    log_file_backup_count: int = 9
    # DB-backed error sink. Worker trims the `error_logs` table down to this
    # many rows after every batch of inserts, bounding table growth.
    error_log_db_max_rows: int = 50000

    # Rate limits
    login_rate_limit_min: str = "5 per minute"
    login_rate_limit_hour: str = "25 per hour"
    api_rate_limit: str = "50 per second"
    order_rate_limit: str = "10 per second"

    # Session
    session_expiry_time: str = "03:00"
    # Set True when serving over HTTPS (production). Leave False for local
    # http://127.0.0.1 dev so the browser actually sends the cookie.
    cookie_secure: bool = False

    # WebSocket Proxy
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 8765
    websocket_url: str = "ws://127.0.0.1:8765"
    zmq_host: str = "127.0.0.1"
    zmq_port: int = 5555

    # Redis (cache backend)
    redis_url: str = "redis://127.0.0.1:6379/0"

    # WebSocket Connection Pooling
    max_symbols_per_websocket: int = 1000
    max_websocket_connections: int = 3
    enable_connection_pooling: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def valid_broker_list(self) -> list[str]:
        return [b.strip() for b in self.valid_brokers.split(",") if b.strip()]

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
