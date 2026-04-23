from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)

from backend.database import Base


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    device_info = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False)  # 'success' or 'failed'
    login_type = Column(String(20), nullable=True)  # 'password', 'oauth'
    broker = Column(String(50), nullable=True)
    failure_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ActiveSession(Base):
    __tablename__ = "active_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token = Column(String(64), unique=True, nullable=False)
    device_info = Column(String(500), nullable=True)
    ip_address = Column(String(45), nullable=True)
    broker = Column(String(50), nullable=True)
    login_time = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now())


class ErrorLog(Base):
    """Persistent sink for WARNING+ log records.

    Populated by `backend.utils.logging.DBErrorLogHandler` from a background
    thread using a sync SQLAlchemy engine, so application code never waits
    on this insert and a DB outage cannot deadlock logging.
    """

    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    level = Column(String(20), nullable=False, index=True)
    logger = Column(String(200), nullable=True)
    message = Column(Text, nullable=True)
    module = Column(String(200), nullable=True)
    func_name = Column(String(200), nullable=True)
    lineno = Column(Integer, nullable=True)
    request_id = Column(String(50), nullable=True, index=True)
    exc_text = Column(Text, nullable=True)


class ApiLog(Base):
    """One row per *authenticated* HTTP request.

    Written from a background thread by ``backend.utils.api_log_writer``. The
    middleware only enqueues records after a successful auth dependency has
    attached ``user_id`` to ``request.state`` — unauthenticated noise (attacker
    floods, bad API keys, expired cookies) never reaches this table.

    The worker trims the table to ``api_log_db_max_rows`` after every N
    inserts, so growth is bounded regardless of traffic volume.
    """

    __tablename__ = "api_logs"

    id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_id = Column(Integer, nullable=True)
    auth_method = Column(String(20), nullable=True)  # "session" | "api_key"
    # Trading mode the request was routed under at capture time. "live" means
    # the request hit the broker API; "sandbox" means it was simulated. Helps
    # users filter noisy paper-trading sessions out of the main log view.
    mode = Column(String(10), nullable=True)
    method = Column(String(8), nullable=False)
    path = Column(String(500), nullable=False)
    status_code = Column(Integer, nullable=False)
    duration_ms = Column(Float, nullable=False)
    client_ip = Column(String(64), nullable=True)
    user_agent = Column(String(500), nullable=True)
    request_id = Column(String(50), nullable=True)
    request_body = Column(Text, nullable=True)
    response_body = Column(Text, nullable=True)
    error = Column(String(500), nullable=True)

    __table_args__ = (
        Index("idx_api_logs_created_at", "created_at"),
        Index("idx_api_logs_status_code", "status_code"),
        Index("idx_api_logs_path", "path"),
        Index("idx_api_logs_user_created", "user_id", "created_at"),
        Index("idx_api_logs_mode_created", "mode", "created_at"),
    )
