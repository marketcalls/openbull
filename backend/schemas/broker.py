from typing import Optional

from pydantic import BaseModel


class BrokerConfigCreate(BaseModel):
    broker_name: str
    api_key: str
    api_secret: str
    redirect_url: str
    client_id: Optional[str] = None


class BrokerConfigResponse(BaseModel):
    broker_name: str
    api_key_masked: str
    api_secret_masked: str
    redirect_url: str
    is_active: bool
    client_id: Optional[str] = None


class BrokerListItem(BaseModel):
    name: str
    display_name: str
    supported_exchanges: list[str]
    is_configured: bool = False
    is_active: bool = False
    oauth_type: str = ""


class AngelLoginPayload(BaseModel):
    clientcode: str
    broker_pin: str
    totp_code: str
