"""Shared SlowAPI limiter instance.

Defined in its own module so both `backend.main` (which registers the
exception handler) and route modules (which use `@limiter.limit(...)`
decorators) can import it without circular dependencies.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_settings.redis_url,
)
