from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func

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
