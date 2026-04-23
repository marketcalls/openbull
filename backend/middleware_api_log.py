"""
Auth-gated API request logger.

Captures request + response for every HTTP call whose dependency chain set
``request.state.user_id`` (i.e. an auth dependency succeeded). Unauthenticated
noise — attacker floods, expired cookies, invalid API keys — never reaches
the ``api_logs`` table. This mirrors openalgo's ``order_logs`` policy of
logging only successful authenticated calls.

The middleware runs *inside* :class:`RequestLoggingMiddleware` so it inherits
the request id, but outside route handlers so it wraps the full request
body capture. It enqueues rows on a bounded thread-safe queue managed by
:mod:`backend.utils.api_log_writer`; the actual DB insert happens on a daemon
thread.

Bodies are redacted (``api_key``, ``password``, tokens, etc.) and truncated
at 64 KB per side. Non-JSON bodies are not stored verbatim — we log their
size instead — so binary uploads don't bloat the table.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.utils.api_log_writer import enqueue as enqueue_log
from backend.utils.request_context import request_id_var

logger = logging.getLogger(__name__)

# ---- Config ----------------------------------------------------------------

MAX_BODY_BYTES = 64 * 1024  # 64 KB per side

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api_secret",
    "password",
    "current_password",
    "new_password",
    "access_token",
    "refresh_token",
    "totp",
    "pin",
    "otp",
}

SKIP_PREFIXES = (
    "/health",
    # Meta: the log viewer's own API must not be logged (would flood itself).
    "/web/logs",
    # WebSocket test page polls these every 5s; logging would drown out real calls.
    "/api/websocket/health",
    "/api/websocket/metrics",
)


# ---- Redaction helpers -----------------------------------------------------

def _redact(obj: Any) -> Any:
    """Return a deep-redacted copy where any key in SENSITIVE_KEYS is masked."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _summarise_body(raw: bytes, content_type: str | None) -> str | None:
    """Return a JSON-safe string for storage, or a marker for unsupported types."""
    if not raw:
        return None
    ct = (content_type or "").lower()
    if "application/json" in ct:
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            redacted = _redact(parsed)
            payload = json.dumps(redacted, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            payload = raw[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
        return payload[:MAX_BODY_BYTES]
    if "text/" in ct or "application/x-www-form-urlencoded" in ct:
        return raw[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
    # Binary — record size only, never the bytes
    return f"[binary {len(raw)} bytes, content-type={ct or 'unknown'}]"


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()[:64]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    client = request.client
    return client.host[:64] if client else None


# ---- Middleware ------------------------------------------------------------

class ApiLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method

        # Hard skip — never attempt to capture these, so we don't pay the body-clone cost.
        if method == "OPTIONS" or any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        # Capture request body up-front; Starlette caches it on request._body
        # so downstream handlers still parse normally.
        try:
            req_body = await request.body()
        except Exception:
            req_body = b""

        start = time.perf_counter()
        response: Response
        err_msg: str | None = None
        try:
            response = await call_next(request)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {str(exc)[:400]}"
            # If handler raised, try to log the failed auth'd request too, then re-raise.
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._maybe_enqueue(
                request=request,
                method=method,
                path=path,
                status_code=500,
                duration_ms=elapsed_ms,
                req_body=req_body,
                resp_body=b"",
                resp_content_type=None,
                error=err_msg,
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Capture response body for logging. We iterate body_iterator and
        # rebuild the response so downstream still sees the bytes.
        resp_body_bytes = b""
        resp_content_type = response.headers.get("content-type")
        try:
            async for chunk in response.body_iterator:
                resp_body_bytes += chunk
                if len(resp_body_bytes) > MAX_BODY_BYTES * 2:
                    # Cheap safeguard: stop collecting way past the cap
                    break
        except Exception:
            # Streaming response can't be replayed — skip body capture,
            # still log metadata.
            resp_body_bytes = b""

        new_response = Response(
            content=resp_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=resp_content_type,
        )

        self._maybe_enqueue(
            request=request,
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=elapsed_ms,
            req_body=req_body,
            resp_body=resp_body_bytes,
            resp_content_type=resp_content_type,
            error=err_msg,
        )

        return new_response

    # ---- helpers -------------------------------------------------------

    def _maybe_enqueue(
        self,
        *,
        request: Request,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        req_body: bytes,
        resp_body: bytes,
        resp_content_type: str | None,
        error: str | None,
    ) -> None:
        """Only enqueue if the request was authenticated. Never raises."""
        try:
            user_id = getattr(request.state, "user_id", None)
            auth_method = getattr(request.state, "auth_method", None)
            if user_id is None:
                # Attacker floods, expired sessions, invalid API keys all skip here.
                return

            req_content_type = request.headers.get("content-type")
            ua = request.headers.get("user-agent", "")[:500]

            # Capture the trading mode at request time so users can filter
            # live vs. sandbox traffic on /logs. Failures here are harmless;
            # the column is nullable.
            mode: str | None = None
            try:
                from backend.services.trading_mode_service import get_trading_mode_sync

                mode = get_trading_mode_sync()
            except Exception:
                mode = None

            row = {
                "user_id": int(user_id),
                "auth_method": (auth_method or "unknown")[:20],
                "mode": mode[:10] if mode else None,
                "method": method[:8],
                "path": path[:500],
                "status_code": int(status_code),
                "duration_ms": float(duration_ms),
                "client_ip": _client_ip(request),
                "user_agent": ua or None,
                "request_id": request_id_var.get() or None,
                "request_body": _summarise_body(req_body, req_content_type),
                "response_body": _summarise_body(resp_body, resp_content_type),
                "error": (error or None) and error[:500],
            }
            enqueue_log(row)
        except Exception:
            # Logging must never break the request. Swallow everything.
            logger.debug("api-log enqueue failed", exc_info=True)


__all__ = ["ApiLogMiddleware"]
