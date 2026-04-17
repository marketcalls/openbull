"""
API key management router - GET/POST /web/apikey
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, get_current_user
from backend.models.user import User
from backend.models.auth import ApiKey
from backend.security import generate_api_key, hash_api_key, encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web", tags=["api-key"])


@router.get("/apikey")
async def get_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current API key for the user (decrypted display copy)."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id)
    )
    api_key_record = result.scalar_one_or_none()

    if api_key_record:
        try:
            decrypted_key = decrypt_value(api_key_record.api_key_encrypted)
            return {
                "status": "success",
                "api_key": decrypted_key,
                "message": "API key retrieved",
            }
        except Exception:
            logger.error("Failed to decrypt API key for user %d", user.id)
            return {
                "status": "error",
                "api_key": None,
                "message": "Failed to decrypt API key. Please generate a new one.",
            }
    else:
        return {
            "status": "success",
            "api_key": None,
            "message": "No API key found. Generate one to use the external API.",
        }


@router.post("/apikey")
async def generate_new_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key for the user. Replaces any existing key."""
    # Generate new key
    new_key = generate_api_key()
    key_hash = hash_api_key(new_key)
    key_encrypted = encrypt_value(new_key)

    # Check for existing key
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Replace existing key
        existing.api_key_hash = key_hash
        existing.api_key_encrypted = key_encrypted
        logger.info("Replaced API key for user %d", user.id)
    else:
        # Create new record
        db.add(ApiKey(
            user_id=user.id,
            api_key_hash=key_hash,
            api_key_encrypted=key_encrypted,
        ))
        logger.info("Created API key for user %d", user.id)

    await db.commit()

    # Invalidate API key caches
    from backend.dependencies import invalidate_all_caches
    await invalidate_all_caches()

    return {
        "status": "success",
        "api_key": new_key,
        "message": "New API key generated. Keep it safe - it cannot be recovered.",
    }
