from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, func

from backend.database import Base


class BrokerAuth(Base):
    __tablename__ = "broker_auth"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_name = Column(String(50), nullable=False)
    access_token = Column(Text, nullable=False)  # Fernet encrypted
    feed_token = Column(Text, nullable=True)  # Fernet encrypted
    broker_user_id = Column(String(255), nullable=True)
    is_revoked = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "broker_name", name="uq_broker_auth_user_broker"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    api_key_hash = Column(Text, nullable=False)  # Argon2
    api_key_encrypted = Column(Text, nullable=False)  # Fernet
    created_at = Column(DateTime(timezone=True), server_default=func.now())
