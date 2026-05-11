"""Public webhook receiver for TradingView (and any other URL-token caller).

  POST /webhook/strategy/{webhook_token}
  Body: {"action": "start"|"stop", "mode": "sandbox"|"live"}

Deliberately separate from ``/web/strategy/*`` so:
  * no session-cookie auth runs (the URL is the credential)
  * the path can be redacted by the reverse proxy in access logs without
    affecting the rest of the app's routes
  * rate limiting / IP allow-list / dedupe live in one place — the
    handler does not share state with authenticated routes

Every request — accepted or rejected — produces one ``sm_webhook_event``
row. HTTP responses are intentionally generic ("Authentication failed",
"Rate limit exceeded") so the surface doesn't leak strategy existence or
ownership to scanners.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse

from backend.strategy.webhook_handler import handle_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/strategy", tags=["strategy-webhook"])


@router.post("/{webhook_token}")
async def receive_webhook(
    request: Request,
    webhook_token: str = Path(..., min_length=10, max_length=128),
):
    """Receive a TradingView webhook for one strategy.

    Returns ``200 {"status":"ok",...}`` on success or audit-only rejection
    (dedupe / cooling-off — the engine wasn't touched but we still
    accepted the request). Other rejections return non-2xx with a generic
    error message.
    """
    raw_body = await request.body()
    client_ip = request.client.host if request.client else None
    # Honor X-Forwarded-For when behind a reverse proxy (leftmost = origin)
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        client_ip = fwd.split(",")[0].strip() or client_ip
    user_agent = request.headers.get("User-Agent")

    outcome = await handle_webhook(
        token=webhook_token,
        raw_body=raw_body,
        ip=client_ip,
        user_agent=user_agent,
    )
    return JSONResponse(content=outcome.body, status_code=outcome.status_code)
