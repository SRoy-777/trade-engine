from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class RawPacket(BaseModel):
    packet_id: str
    provider: str
    received_timestamp: datetime
    raw_payload: str

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )

class MarketEvent(BaseModel):
    event_id: str
    correlation_id: str  # References packet_id
    exchange_timestamp: Optional[datetime] = None
    received_timestamp: datetime
    processed_timestamp: datetime
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    source_provider: str

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )
