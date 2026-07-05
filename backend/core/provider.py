from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Dict, Any
from models.market import RawPacket

class BaseMarketProvider(ABC):
    """Abstract base class for all market feed data providers."""
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier name for the provider (e.g., 'replay', 'dhan')."""
        pass

    @property
    @abstractmethod
    def status(self) -> str:
        """Returns current playback status: 'STOPPED', 'RUNNING', or 'PAUSED'."""
        pass

    @property
    @abstractmethod
    def speed(self) -> float:
        """Returns current playback speed multiplier."""
        pass

    @abstractmethod
    def set_packet_callback(self, callback: Callable[[RawPacket], Awaitable[None]]) -> None:
        """Registers a callback to receive RawPacket payloads from this provider."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start streaming or playback."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop streaming or playback."""
        pass

    @abstractmethod
    async def pause(self) -> None:
        """Pause playback (only applicable in replay mode)."""
        pass

    @abstractmethod
    async def set_speed(self, speed: float) -> None:
        """Dynamically updates the playback speed multiplier."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic details and telemetry from the provider."""
        pass


class BaseExecutionProvider(ABC):
    """Abstract base class for all trade execution routing providers (Broker/Paper)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier name for the execution provider (e.g., 'paper', 'dhan')."""
        pass

    @abstractmethod
    async def execute_order(self, order_request: Dict[str, Any]) -> Dict[str, Any]:
        """Submits a trade execution request to the broker or local matching sandbox."""
        pass
