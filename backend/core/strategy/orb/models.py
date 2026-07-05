from datetime import datetime
from pydantic import BaseModel, Field

class TradingSignal(BaseModel):
    """Signal generated when the opening range breakout conditions are satisfied."""
    signal_id: str
    timestamp: datetime
    symbol: str
    entry_price: float
    stop_loss: float
    target: float
    reason: str
    strategy_name: str = "Opening Range Breakout"
    risk_reward: float

class TradeRecord(BaseModel):
    """Historical trade analytics stored after signal position exit."""
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    holding_time_secs: float
    pnl: float # Points P&L (exit_price - entry_price)
    mfe: float # Maximum Favourable Excursion
    mae: float # Maximum Adverse Excursion
    exit_reason: str # Stop Loss, Target, or Square Off
