from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Dict, Any
from providers.market.dhan.models import MarketPacket

class BaseBroker(ABC):
    """Abstract base class for brokerage simulation and live integration models."""

    @abstractmethod
    async def submit_order(self, order_request: Dict[str, Any]) -> str:
        """Submits a buy/sell order request to the broker. Returns a unique order ID."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending limit order. Returns True if successfully cancelled."""
        pass

    @abstractmethod
    def register_fill_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Registers a callback to receive notifications whenever orders get filled."""
        pass

    @abstractmethod
    def get_portfolio(self) -> Dict[str, Any]:
        """Returns cash balances, margins, holdings, and portfolio valuations in INR."""
        pass

    @abstractmethod
    async def on_tick(self, packet: MarketPacket) -> None:
        """Feeds a market tick to update limit orders matching and unrealized valuations."""
        pass
