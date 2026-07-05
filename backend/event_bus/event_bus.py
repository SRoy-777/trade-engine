import asyncio
import time
from typing import Callable, Awaitable, List, Dict, Tuple
from models.market import MarketEvent
from utils.logger_setup import logger

# Subscriber signature: takes a MarketEvent and returns an awaitable
SubscriberType = Callable[[MarketEvent], Awaitable[None]]

class PrioritizedEventBus:
    def __init__(self):
        # Maps priority level (int) to list of subscribers
        self._subscribers: Dict[int, List[SubscriberType]] = {}
        self._lock = asyncio.Lock()
        self._publish_count = 0
        self._total_publish_time_ms = 0.0

    async def subscribe(self, callback: SubscriberType, priority: int = 10) -> None:
        """Registers a subscriber with a given priority (lower numbers execute first)."""
        async with self._lock:
            if priority not in self._subscribers:
                self._subscribers[priority] = []
            if callback not in self._subscribers[priority]:
                self._subscribers[priority].append(callback)
            logger.info(f"Subscribed {callback.__name__ if hasattr(callback, '__name__') else str(callback)} to EventBus with priority {priority}")

    async def unsubscribe(self, callback: SubscriberType) -> None:
        """Removes a subscriber from all priority levels."""
        async with self._lock:
            for priority, subscribers in list(self._subscribers.items()):
                if callback in subscribers:
                    subscribers.remove(callback)
                    if not subscribers:
                        del self._subscribers[priority]
            logger.info(f"Unsubscribed callback from EventBus")

    async def publish(self, event: MarketEvent) -> None:
        """Publishes an event to all subscribers sequentially by priority level, but concurrently within the same level."""
        start_time = time.perf_counter()
        self._publish_count += 1

        async with self._lock:
            # Sort priorities ascending
            sorted_priorities = sorted(self._subscribers.keys())
            # Capture snapshot to avoid concurrent modification issues
            priority_groups = {p: list(self._subscribers[p]) for p in sorted_priorities}

        async def run_subscriber(sub: SubscriberType):
            try:
                sub_start = time.perf_counter()
                await sub(event)
                sub_duration = (time.perf_counter() - sub_start) * 1000
                logger.debug(f"Subscriber {sub.__name__ if hasattr(sub, '__name__') else 'callback'} completed in {sub_duration:.3f}ms")
            except Exception as e:
                logger.error(
                    f"Error in subscriber execution: {e}",
                    extra={
                        "event_id": event.event_id,
                        "correlation_id": event.correlation_id,
                        "provider": event.source_provider
                    }
                )

        # Execute level by level
        for priority in sorted_priorities:
            subs = priority_groups[priority]
            if not subs:
                continue
            
            # Execute all subscribers in this priority level concurrently
            await asyncio.gather(*(run_subscriber(sub) for sub in subs), return_exceptions=True)

        duration_ms = (time.perf_counter() - start_time) * 1000
        self._total_publish_time_ms += duration_ms

    @property
    def average_publish_time_ms(self) -> float:
        if self._publish_count == 0:
            return 0.0
        return self._total_publish_time_ms / self._publish_count

    @property
    def publish_count(self) -> int:
        return self._publish_count

event_bus = PrioritizedEventBus()
