from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB

from backend.database import Base


class BrokerConfig(Base):
    __tablename__ = "broker_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_name = Column(String(50), nullable=False)
    api_key = Column(Text, nullable=False)  # Fernet encrypted
    api_secret = Column(Text, nullable=False)  # Fernet encrypted
    redirect_url = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=False, index=True)
    extra_config = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "broker_name", name="uq_broker_config_user_broker"),
    )
