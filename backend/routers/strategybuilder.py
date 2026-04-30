"""
Strategy Builder live-snapshot endpoint.

Session-authed (cookie-based) under ``/web/strategybuilder/*``. The single
endpoint here, ``POST /snapshot``, takes a leg set and returns spot +
per-leg LTP + IV + Greeks + position-aggregated Greeks in one shot. The
frontend hits it on every leg-set change and on a Refresh button — it
deliberately does NOT subscribe to underlying ticks via WebSocket because
spot updates would flood the payoff chart with re-renders for no benefit
(the same design call openalgo made).

Why a POST with a body instead of a chain of GET params: leg lists can be
4-10 items, each with 6 fields, and embedding that in a query string is
both ugly and runs into URL length limits when symbols are long.
"""

from __future__ import annotations

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.dependencies import BrokerContext, get_broker_context
from backend.services.multi_strike_oi_service import get_multi_strike_oi_data
from backend.services.strategy_builder_service import get_strategy_snapshot
from backend.services.strategy_chart_service import get_strategy_chart_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/strategybuilder", tags=["strategybuilder"])


class SnapshotLeg(BaseModel):
    """Minimal leg shape needed to price + solve Greeks.

    ``entry_price`` is optional — when supplied (e.g. by the Strategy
    Portfolio page reloading a saved strategy), the response also carries
    ``unrealized_pnl`` per leg and in totals.
    """

    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(..., min_length=1)
    action: Literal["BUY", "SELL"]
    lots: int = Field(..., gt=0)
    lot_size: int = Field(..., gt=0)
    exchange: Optional[str] = None  # overrides ``options_exchange`` per-leg
    entry_price: Optional[float] = None


class SnapshotRequest(BaseModel):
    underlying: str = Field(..., min_length=1)
    exchange: Optional[str] = None  # spot exchange (NSE_INDEX / NSE / MCX / ...)
    options_exchange: Optional[str] = "NFO"  # default leg exchange
    interest_rate: Optional[float] = None  # %; per-exchange default if omitted
    expiry_time: Optional[str] = None  # "HH:MM"; per-exchange default if omitted
    legs: List[SnapshotLeg] = Field(..., min_length=1)


@router.post("/snapshot")
async def snapshot(
    payload: SnapshotRequest,
    ctx: BrokerContext = Depends(get_broker_context),
):
    """One-shot live pricing + Greeks for a strategy leg set.

    Requires an authenticated user with an active broker session — the
    endpoint hits the broker for the underlying spot and a multi-quotes
    fan-out for every leg, then folds the results through the pure-math
    Black-76 implementation in ``option_greeks_service``.
    """
    legs_payload = [leg.model_dump(mode="json") for leg in payload.legs]

    success, response_data, status_code = get_strategy_snapshot(
        legs=legs_payload,
        underlying=payload.underlying,
        exchange=payload.exchange,
        options_exchange=payload.options_exchange,
        interest_rate=payload.interest_rate,
        expiry_time=payload.expiry_time,
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
    )

    if not success:
        raise HTTPException(
            status_code=status_code,
            detail=response_data.get("message", "Snapshot failed"),
        )
    return response_data


class ChartLeg(BaseModel):
    """Same shape as :class:`SnapshotLeg` — kept distinct so the chart
    endpoint can evolve its schema independently (e.g. drop ``entry_price``
    or add a chart-specific filter) without breaking the snapshot
    contract."""

    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(..., min_length=1)
    action: Literal["BUY", "SELL"]
    lots: int = Field(..., gt=0)
    lot_size: int = Field(..., gt=0)
    exchange: Optional[str] = None
    entry_price: Optional[float] = None


class ChartRequest(BaseModel):
    underlying: str = Field(..., min_length=1)
    exchange: Optional[str] = None
    options_exchange: Optional[str] = "NFO"
    interval: str = Field(..., min_length=1)
    days: int = Field(5, ge=1, le=60)
    include_underlying: bool = True
    legs: List[ChartLeg] = Field(..., min_length=1)


@router.post("/chart")
async def strategy_chart(
    payload: ChartRequest,
    ctx: BrokerContext = Depends(get_broker_context),
):
    """Historical combined-premium time series for the given leg set.

    Drives the Strategy Chart tab. Returns the underlying overlay (when
    available), per-leg series, and the combined-premium / PnL series in
    one round-trip. Timestamps where any leg lacks a close are dropped so
    the curve never dips spuriously on a stale broker candle.
    """
    legs_payload = [leg.model_dump(mode="json") for leg in payload.legs]

    success, response_data, status_code = get_strategy_chart_data(
        legs=legs_payload,
        underlying=payload.underlying,
        exchange=payload.exchange,
        options_exchange=payload.options_exchange,
        interval=payload.interval,
        days=payload.days,
        include_underlying=payload.include_underlying,
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
    )

    if not success:
        raise HTTPException(
            status_code=status_code,
            detail=response_data.get("message", "Chart fetch failed"),
        )
    return response_data


class MultiStrikeOILeg(BaseModel):
    """Leg shape for the multi-strike OI endpoint. Strike / option_type /
    expiry_date are pass-through metadata used purely for legend labels —
    the OI data itself comes from broker history keyed on ``symbol``."""

    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(..., min_length=1)
    action: Literal["BUY", "SELL"]
    exchange: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[Literal["CE", "PE"]] = None
    expiry_date: Optional[str] = None


class MultiStrikeOIRequest(BaseModel):
    underlying: str = Field(..., min_length=1)
    exchange: Optional[str] = None
    options_exchange: Optional[str] = "NFO"
    interval: str = Field(..., min_length=1)
    days: int = Field(5, ge=1, le=60)
    include_underlying: bool = True
    legs: List[MultiStrikeOILeg] = Field(..., min_length=1)


@router.post("/multi-strike-oi")
async def multi_strike_oi(
    payload: MultiStrikeOIRequest,
    ctx: BrokerContext = Depends(get_broker_context),
):
    """Per-leg historical OI series + underlying close for the Strategy
    Builder's Multi-Strike OI tab.

    Each option leg is fetched separately (deduped by symbol+exchange) and
    returned as its own series so the chart can overlay them with
    independent colours and toggle visibility per leg without re-fetching.
    """
    legs_payload = [leg.model_dump(mode="json") for leg in payload.legs]

    # Service expects ``lots``/``lot_size`` for parity with the chart endpoint
    # but we don't actually use them for OI display — fill defaults so the
    # validator inside the service doesn't reject the call.
    for leg in legs_payload:
        leg.setdefault("lots", 1)
        leg.setdefault("lot_size", 1)

    success, response_data, status_code = get_multi_strike_oi_data(
        legs=legs_payload,
        underlying=payload.underlying,
        exchange=payload.exchange,
        options_exchange=payload.options_exchange,
        interval=payload.interval,
        days=payload.days,
        include_underlying=payload.include_underlying,
        auth_token=ctx.auth_token,
        broker=ctx.broker_name,
        config=ctx.broker_config,
    )

    if not success:
        raise HTTPException(
            status_code=status_code,
            detail=response_data.get("message", "Multi-strike OI fetch failed"),
        )
    return response_data
