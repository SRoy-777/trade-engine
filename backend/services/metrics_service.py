import asyncio
import time
from typing import Dict, Any, Optional
from market_feed.manager import feed_manager
from event_bus.event_bus import event_bus
from market_feed.parser import packet_parser
from storage_engine.recorder import packet_recorder
from storage_engine.logger import duckdb_logger

class MetricsService:
    """Service responsible for aggregating and tracking real-time pipeline metrics and latency stats."""

    def __init__(self):
        self._prev_packets = 0
        self._prev_events = 0
        self._prev_time = time.perf_counter()
        
        self._packets_per_sec = 0.0
        self._events_per_sec = 0.0
        self._loop_task: Optional[asyncio.Task] = None
        self._is_active = False

    def start(self) -> None:
        """Starts the background telemetry computation worker."""
        if self._is_active:
            return
            
        self._is_active = True
        self._prev_packets = feed_manager.packets_received
        self._prev_events = event_bus.publish_count
        self._prev_time = time.perf_counter()
        self._loop_task = asyncio.create_task(self._metrics_loop())

    async def _metrics_loop(self) -> None:
        """Computes sliding-window throughput rate once per second."""
        while self._is_active:
            try:
                await asyncio.sleep(1.0)
                curr_time = time.perf_counter()
                curr_packets = feed_manager.packets_received
                curr_events = event_bus.publish_count
                
                dt = curr_time - self._prev_time
                if dt > 0:
                    self._packets_per_sec = (curr_packets - self._prev_packets) / dt
                    self._events_per_sec = (curr_events - self._prev_events) / dt
                    
                self._prev_packets = curr_packets
                self._prev_events = curr_events
                self._prev_time = curr_time
            except asyncio.CancelledError:
                break
            except Exception as e:
                pass

    def get_metrics(self) -> Dict[str, Any]:
        """Gathers diagnostic readings from across the system."""
        bronze_buf = len(packet_recorder._buffer) if packet_recorder._is_active else 0
        silver_buf = len(duckdb_logger._buffer) if duckdb_logger._is_running else 0
        
        avg_parse = packet_parser.average_parse_time_ms
        avg_bus = event_bus.average_publish_time_ms
        avg_pipeline = feed_manager.average_pipeline_time_ms
        
        # Calculate replay latency drift if a provider status is active
        provider_status = feed_manager.get_status()
        last_price = provider_status.get("last_price", 0.0)
        last_symbol = provider_status.get("last_symbol", "N/A")
        last_ts = provider_status.get("last_timestamp")
        
        replay_delay_secs = 0.0
        if last_ts:
            try:
                # Parse timestamp and compare with system time
                event_time = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                # Remove timezone offset for utc comparison
                event_time_utc = event_time.replace(tzinfo=None)
                replay_delay_secs = (datetime.utcnow() - event_time_utc).total_seconds()
            except Exception:
                pass

        return {
            "packets_per_sec": round(self._packets_per_sec, 1),
            "events_per_sec": round(self._events_per_sec, 1),
            "bronze_buffer_size": bronze_buf,
            "silver_buffer_size": silver_buf,
            "avg_parser_time_ms": round(avg_parse, 3),
            "avg_event_bus_time_ms": round(avg_bus, 3),
            "avg_pipeline_time_ms": round(avg_pipeline, 3),
            "total_packets": feed_manager.packets_received,
            "total_inserts": duckdb_logger.total_inserts,
            "last_symbol": last_symbol,
            "last_price": last_price,
            "last_timestamp": last_ts or "N/A",
            "replay_delay_secs": round(replay_delay_secs, 2)
        }

    def stop(self) -> None:
        """Stops the telemetry computation worker."""
        if not self._is_active:
            return
            
        self._is_active = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None

metrics_service = MetricsService()
