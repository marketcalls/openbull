"""Strategy module security helpers — webhook tokens, hashing, redaction."""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)

WEBHOOK_TOKEN_PREFIX = "obwh_"
# 32 bytes of entropy → 43 chars URL-safe base64 (no padding from token_urlsafe).
_WEBHOOK_TOKEN_BYTES = 32

_REDACT_KEYS = frozenset(
    {"secret", "token", "api_key", "apikey", "password", "authorization",
     "webhook_token", "webhook_secret", "auth_token"}
)
_REDACTED = "[REDACTED]"


def generate_webhook_token() -> tuple[str, str]:
    """Return ``(plaintext, sha256_hex_hash)`` for a fresh webhook token.

    Plaintext is shown to the user once on create/rotate; the hash is the
    only thing persisted. SHA-256 is used (not argon2) because the input
    has 256+ bits of entropy by construction — a slow KDF is unnecessary
    and breaks indexed lookup.
    """
    plaintext = WEBHOOK_TOKEN_PREFIX + secrets.token_urlsafe(_WEBHOOK_TOKEN_BYTES)
    return plaintext, hash_webhook_token(plaintext)


def hash_webhook_token(token: str) -> str:
    """Deterministic SHA-256 hex digest used for indexed lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redact_payload(payload: Any) -> Any:
    """Recursively replace sensitive keys with ``[REDACTED]``.

    Used before persisting webhook bodies to ``sm_webhook_event.payload``
    and before any structured logging that might include user-supplied JSON.
    """
    if isinstance(payload, dict):
        return {
            k: (_REDACTED if k.lower() in _REDACT_KEYS else redact_payload(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload
