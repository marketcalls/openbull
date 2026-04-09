"""
Ping endpoint - health check for the external API.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/ping")
async def ping():
    """Simple health check endpoint."""
    return {"status": "success", "message": "pong"}
