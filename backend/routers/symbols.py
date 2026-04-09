"""
Symbol search and master contract download routes.
"""

import logging
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.dependencies import get_broker_context, BrokerContext
from backend.services.symbol_service import download_master_contracts, search_symbols

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/symbols", tags=["symbols"])


@router.get("/search")
async def symbol_search(
    q: str = Query(..., min_length=1, max_length=50, description="Symbol search query"),
    exchange: str = Query(..., min_length=1, max_length=20, description="Exchange (NSE, NFO, etc.)"),
    ctx: BrokerContext = Depends(get_broker_context),
):
    """Search for symbols in the master contract database."""
    results = search_symbols(query=q, exchange=exchange, broker_name=ctx.broker_name)
    return {"status": "success", "data": results}


@router.post("/download")
async def trigger_master_download(
    ctx: BrokerContext = Depends(get_broker_context),
):
    """Trigger master contract download in background.

    The download is broker-specific and runs in a background thread.
    """
    broker_name = ctx.broker_name
    auth_token = ctx.auth_token

    def _background_download():
        try:
            result = download_master_contracts(broker_name, auth_token=auth_token)
            logger.info("Master contract download result for %s: %s", broker_name, result)
        except Exception as e:
            logger.error("Background master contract download failed: %s", e)

    thread = Thread(target=_background_download, daemon=True)
    thread.start()

    return {"status": "success", "message": f"Master contract download started for {broker_name}"}
