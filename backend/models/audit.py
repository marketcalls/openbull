from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func

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
