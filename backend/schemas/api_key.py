"""
Pydantic schemas for API key management.
"""

from pydantic import BaseModel


class ApiKeyResponse(BaseModel):
    status: str
    api_key: str | None = None
    message: str | None = None


class ApiKeyGenerateResponse(BaseModel):
    status: str
    api_key: str
    message: str
