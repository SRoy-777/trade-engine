from abc import ABC, abstractmethod
from typing import Dict, Any
from models.market import MarketEvent

class BaseFilter(ABC):
    """Abstract base class for modular pre-trade strategy filters."""

    @abstractmethod
    def validate(self, event: MarketEvent, context: Dict[str, Any]) -> bool:
        """Returns True if the filter condition passes, False otherwise."""
        pass

class TimeFilter(BaseFilter):
    """Validates that entries occur only within allowed daily market sessions."""

    def validate(self, event: MarketEvent, context: Dict[str, Any]) -> bool:
        event_time = event.exchange_timestamp or event.received_timestamp
        time_str = event_time.strftime("%H:%M")
        
        orb_end = context.get("orb_end", "09:30")
        last_entry = context.get("last_entry", "14:30")
        
        # Robustly handle cases where context parameters are datetime.time or datetime.datetime objects
        if not isinstance(orb_end, str):
            orb_end = getattr(orb_end, "strftime", lambda f: str(orb_end))("%H:%M")
        if not isinstance(last_entry, str):
            last_entry = getattr(last_entry, "strftime", lambda f: str(last_entry))("%H:%M")
            
        # Must be strictly after ORB_END and before LAST_ENTRY_TIME
        is_after_range = time_str > orb_end
        is_before_cutoff = time_str < last_entry
        
        return is_after_range and is_before_cutoff

class VolumeFilter(BaseFilter):
    """Validates that current tick volume exhibits a significant breakout surge."""

    def validate(self, event: MarketEvent, context: Dict[str, Any]) -> bool:
        avg_volume = context.get("avg_volume", 0.0)
        multiplier = context.get("min_volume_multiplier", 1.5)
        
        if avg_volume <= 0:
            return True # Pass validation if history is not yet populated
            
        required_volume = avg_volume * multiplier
        return event.volume > required_volume
