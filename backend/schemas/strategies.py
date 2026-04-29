"""
Pydantic request/response schemas for the saved-strategies CRUD endpoints.

Mirrors :class:`backend.models.strategies.Strategy`. The leg shape is
declared explicitly here rather than left as ``dict[str, Any]`` so the
Strategy Builder frontend can rely on a typed contract and we get 422s on
malformed payloads instead of corrupted JSONB.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrategyLeg(BaseModel):
    """One option leg inside a saved strategy.

    All fields except ``action`` / ``option_type`` / ``strike`` / ``lots`` are
    optional so partially-built legs can round-trip through the API without
    losing in-progress UI state.
    """

    id: Optional[str] = None
    action: Literal["BUY", "SELL"]
    option_type: Literal["CE", "PE"]
    strike: float
    lots: int = Field(..., gt=0)
    lot_size: Optional[int] = Field(None, gt=0)
    expiry_date: Optional[str] = None
    symbol: Optional[str] = None
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    status: Literal["open", "closed", "expired"] = "open"
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None


class StrategyCreate(BaseModel):
    """Body of POST /web/strategies."""

    name: str = Field(..., min_length=1, max_length=200)
    underlying: str = Field(..., min_length=1, max_length=50)
    exchange: str = Field(..., min_length=1, max_length=20)
    expiry_date: Optional[str] = Field(None, max_length=20)
    mode: Literal["live", "sandbox"] = "live"
    legs: List[StrategyLeg] = Field(..., min_length=1)
    notes: Optional[str] = None


class StrategyUpdate(BaseModel):
    """Body of PUT /web/strategies/{id}.

    All fields optional — only the supplied keys are updated. Setting
    ``status="closed"`` also stamps ``closed_at`` server-side.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    underlying: Optional[str] = Field(None, min_length=1, max_length=50)
    exchange: Optional[str] = Field(None, min_length=1, max_length=20)
    expiry_date: Optional[str] = Field(None, max_length=20)
    status: Optional[Literal["active", "closed", "expired"]] = None
    legs: Optional[List[StrategyLeg]] = None
    notes: Optional[str] = None


class StrategyOut(BaseModel):
    """Response shape for both GET (list/single) and write endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    underlying: str
    exchange: str
    expiry_date: Optional[str]
    mode: str
    status: str
    legs: List[StrategyLeg]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
