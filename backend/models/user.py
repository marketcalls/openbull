from sqlalchemy import Column, Integer, String, Boolean, DateTime, func

from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
