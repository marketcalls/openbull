"""ASGI middlewares.

Currently houses :class:`RequestLoggingMiddleware`, which:
  * generates a short request id for every HTTP request (or trusts an
    inbound ``X-Request-ID`` header if the client supplied one),
  * stores that id in the :mod:`backend.utils.request_context` contextvar
    so every log line emitted during the request carries it,
  * emits one INFO log per request with method / path / status / latency,
  * echoes the id back on the response as ``X-Request-ID``.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.utils.logging import get_logger
from backend.utils.request_context import new_request_id, request_id_var

_HEADER = "X-Request-ID"
_HEADER_MAX = 64
logger = get_logger("openbull.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        inbound = request.headers.get(_HEADER)
        rid = inbound if inbound and len(inbound) <= _HEADER_MAX else new_request_id()
        token = request_id_var.set(rid)

        start = time.perf_counter()
        response: Response | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_HEADER] = rid
            return response
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            try:
                logger.info(
                    "%s %s -> %d in %.1fms",
                    request.method,
                    request.url.path,
                    status_code,
                    elapsed_ms,
                )
            finally:
                request_id_var.reset(token)


__all__ = ["RequestLoggingMiddleware"]
