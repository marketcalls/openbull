"""
Pydantic schemas for account-related API requests (funds, holdings, etc.).
"""

from pydantic import BaseModel, Field


class AccountRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")


class FundsResponse(BaseModel):
    status: str
    data: dict | None = None


class HoldingsResponse(BaseModel):
    status: str
    data: dict | None = None


class PositionsResponse(BaseModel):
    status: str
    data: list | None = None


class OrderbookResponse(BaseModel):
    status: str
    data: dict | None = None


class TradebookResponse(BaseModel):
    status: str
    data: list | None = None
