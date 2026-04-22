"""Per-request context propagated via contextvars.

The logging subsystem reads `request_id_var` to stamp every log record
with the id of the HTTP request that produced it, so a line emitted
from deep inside broker code can still be correlated with the original
request in the access log.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

# Default sentinel makes log lines outside a request still readable.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    """Generate a short request id (first 12 hex chars of a uuid4)."""
    return uuid.uuid4().hex[:12]


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def get_request_id() -> str:
    return request_id_var.get()
