from sqlalchemy import Column, Integer, String, Float, Index

from backend.database import Base


class SymToken(Base):
    __tablename__ = "symtoken"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(100), nullable=False, index=True)
    brsymbol = Column(String(100), nullable=False, index=True)
    name = Column(String(100), nullable=True)
    exchange = Column(String(20), nullable=False, index=True)
    brexchange = Column(String(20), nullable=True)
    token = Column(String(50), nullable=True, index=True)
    expiry = Column(String(20), nullable=True)
    strike = Column(Float, nullable=True)
    lotsize = Column(Integer, nullable=True)
    instrumenttype = Column(String(20), nullable=True)
    tick_size = Column(Float, nullable=True)

    __table_args__ = (
        Index("idx_symtoken_symbol_exchange", "symbol", "exchange"),
        Index("idx_symtoken_brsymbol_exchange", "brsymbol", "exchange"),
    )
