from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field

class RawPacket(BaseModel):
    """Container for raw incoming WebSocket bytes."""
    data: bytes
    received_at: datetime = Field(default_factory=datetime.utcnow)

class MarketPacket(BaseModel):
    """Standardised parsed live market data tick."""
    packet_type: str  # "Ticker", "Quote", "Depth", "OI", "PrevClose"
    exchange_segment: str  # String mapped (e.g., "NSE_EQ")
    security_id: str  # Token ID
    ltp: float  # Last Traded Price
    volume: Optional[int] = None  # Cumulative volume
    timestamp: datetime  # Time of the tick (LTT converted to datetime)
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    raw_fields: Dict[str, Any] = Field(default_factory=dict) # Catch-all for diagnostics

class SubscriptionRequest(BaseModel):
    """Schema representing an outbound instrument subscription payload."""
    request_code: int  # 15 for Ticker, 17 for Quote, 21 for Full
    instruments: List[Tuple[int, str]]  # List of (exchange_segment_code, security_id)

class SubscriptionResponse(BaseModel):
    """Status feedback for subscription actions."""
    status: str
    message: str
    request_code: int

class ConnectionStatus(BaseModel):
    """Operational connection states."""
    connected: bool
    last_connected_at: Optional[datetime] = None
    reconnect_attempts: int = 0
    current_mode: str = "DISCONNECTED"

class Heartbeat(BaseModel):
    """Heartbeat container."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
