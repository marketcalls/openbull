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
from backend.api.margin import router as margin_router
from backend.api.basket_order import router as basket_order_router
from backend.api.split_order import router as split_order_router
from backend.api.optionsymbol import router as optionsymbol_router
from backend.api.optionchain import router as optionchain_router
from backend.api.syntheticfuture import router as syntheticfuture_router
from backend.api.optionsorder import router as optionsorder_router
from backend.api.optionsmultiorder import router as optionsmultiorder_router
from backend.api.optiongreeks import router as optiongreeks_router
from backend.api.oitracker import router as oitracker_router
from backend.api.maxpain import router as maxpain_router
from backend.api.ivchart import router as ivchart_router
from backend.api.analyzer import router as analyzer_router

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
api_v1_router.include_router(margin_router)
api_v1_router.include_router(basket_order_router)
api_v1_router.include_router(split_order_router)
api_v1_router.include_router(optionsymbol_router)
api_v1_router.include_router(optionchain_router)
api_v1_router.include_router(syntheticfuture_router)
api_v1_router.include_router(optionsorder_router)
api_v1_router.include_router(optionsmultiorder_router)
api_v1_router.include_router(optiongreeks_router)
api_v1_router.include_router(oitracker_router)
api_v1_router.include_router(maxpain_router)
api_v1_router.include_router(ivchart_router)
api_v1_router.include_router(analyzer_router)
