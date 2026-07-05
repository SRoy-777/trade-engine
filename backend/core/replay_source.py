from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class ReplaySource(ABC):
    """Abstract interface for historical market data replay sources."""

    @abstractmethod
    async def open(self) -> None:
        """Opens and prepares the underlying resource connection (e.g., CSV file, Parquet reader)."""
        pass

    @abstractmethod
    async def read_next(self) -> Optional[Dict[str, Any]]:
        """Reads the next sequential market tick. Returns a dictionary payload, or None if EOF is reached."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Closes the resource handle."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Resets the cursor to the beginning of the dataset."""
        pass
