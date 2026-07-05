from core.strategy.base import BaseStrategy
from core.strategy.manager import StrategyManager
from core.strategy.templates.day_trading import DayTradingTrendFollower
from core.strategy.templates.hft_scalper import HftMicroScalper

__all__ = [
    "BaseStrategy",
    "StrategyManager",
    "DayTradingTrendFollower",
    "HftMicroScalper"
]
