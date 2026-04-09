from pydantic import BaseModel


class BrokerConfigCreate(BaseModel):
    broker_name: str
    api_key: str
    api_secret: str
    redirect_url: str


class BrokerConfigResponse(BaseModel):
    broker_name: str
    api_key_masked: str
    api_secret_masked: str
    redirect_url: str
    is_active: bool


class BrokerListItem(BaseModel):
    name: str
    display_name: str
    supported_exchanges: list[str]
    is_configured: bool = False
    is_active: bool = False
