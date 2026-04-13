"""
External API router - /api/v1 prefix.
All external endpoints require API key authentication via get_api_user dependency.
"""

from fastapi import APIRouter

from backend.api.ping import router as ping_router
from backend.api.place_order import router as place_order_router
from backend.api.funds import router as funds_router
from backend.api.orderbook import router as orderbook_router
from backend.api.tradebook import router as tradebook_router
from backend.api.positions import router as positions_router
from backend.api.holdings import router as holdings_router
from backend.api.orderstatus import router as orderstatus_router
from backend.api.openposition import router as openposition_router
from backend.api.symbol import router as symbol_router
from backend.api.search import router as search_router
from backend.api.expiry import router as expiry_router
from backend.api.intervals import router as intervals_router
from backend.api.quotes import router as quotes_router
from backend.api.multiquotes import router as multiquotes_router
from backend.api.depth import router as depth_router
from backend.api.history import router as history_router

api_v1_router = APIRouter(prefix="/api/v1", tags=["api-v1"])

api_v1_router.include_router(ping_router)
api_v1_router.include_router(place_order_router)
api_v1_router.include_router(funds_router)
api_v1_router.include_router(orderbook_router)
api_v1_router.include_router(tradebook_router)
api_v1_router.include_router(positions_router)
api_v1_router.include_router(holdings_router)
api_v1_router.include_router(orderstatus_router)
api_v1_router.include_router(openposition_router)
api_v1_router.include_router(symbol_router)
api_v1_router.include_router(search_router)
api_v1_router.include_router(expiry_router)
api_v1_router.include_router(intervals_router)
api_v1_router.include_router(quotes_router)
api_v1_router.include_router(multiquotes_router)
api_v1_router.include_router(depth_router)
api_v1_router.include_router(history_router)
