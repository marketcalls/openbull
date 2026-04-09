"""
Pydantic schemas for order-related API requests and responses.
"""

from pydantic import BaseModel, Field


class PlaceOrderRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")
    symbol: str = Field(..., description="Trading symbol (e.g. NIFTY24JAN24000CE)")
    exchange: str = Field(..., description="Exchange (NSE, NFO, BSE, BFO, MCX, CDS, etc.)")
    action: str = Field(..., description="BUY or SELL")
    quantity: str = Field(..., description="Order quantity")
    pricetype: str = Field(..., description="MARKET, LIMIT, SL, or SL-M")
    product: str = Field(..., description="CNC, NRML, or MIS")
    price: str = Field(default="0", description="Price for LIMIT/SL orders")
    trigger_price: str = Field(default="0", description="Trigger price for SL/SL-M orders")
    disclosed_quantity: str = Field(default="0", description="Disclosed quantity")
    strategy: str = Field(default="", description="Strategy name for tracking")
    position_size: str = Field(default="0", description="Target position size for smart orders")


class PlaceSmartOrderRequest(PlaceOrderRequest):
    position_size: str = Field(..., description="Target net position size")


class ModifyOrderRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")
    orderid: str = Field(..., description="Order ID to modify")
    quantity: str = Field(..., description="New quantity")
    price: str = Field(..., description="New price")
    pricetype: str = Field(..., description="MARKET, LIMIT, SL, or SL-M")
    trigger_price: str = Field(default="0", description="New trigger price")
    disclosed_quantity: str = Field(default="0", description="New disclosed quantity")


class CancelOrderRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")
    orderid: str = Field(..., description="Order ID to cancel")


class CancelAllOrdersRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")


class CloseAllPositionsRequest(BaseModel):
    apikey: str = Field(..., description="OpenBull API key")


class OrderResponse(BaseModel):
    status: str
    orderid: str | None = None
    message: str | None = None
