import asyncio
import time
from typing import Optional, Dict, Any
from core.provider import BaseMarketProvider
from models.market import RawPacket, MarketEvent
from market_feed.parser import packet_parser
from event_bus.event_bus import event_bus
from storage_engine.recorder import packet_recorder
from storage_engine.logger import duckdb_logger
from utils.logger_setup import logger

class MarketFeedManager:
    """Orchestrator responsible for routing data from the active provider through the ingestion pipeline."""

    def __init__(self, provider: Optional[BaseMarketProvider] = None):
        self._provider: Optional[BaseMarketProvider] = None
        self._session_id: Optional[str] = None
        
        # Ingress telemetry
        self._packets_received = 0
        self._total_pipeline_time_ms = 0.0
        
        if provider:
            self.set_provider(provider)

    def set_provider(self, provider: BaseMarketProvider) -> None:
        """Binds a market provider to the manager and registers the callback."""
        self._provider = provider
        self._provider.set_packet_callback(self.on_raw_packet)
        logger.info(f"MarketFeedManager bound to provider: {provider.provider_name}")

    @property
    def provider(self) -> Optional[BaseMarketProvider]:
        return self._provider

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def start(self) -> None:
        """Opens pipeline writers and starts the bound provider."""
        if not self._provider:
            raise ValueError("No market provider is currently bound to the manager")

        # Open session records if the provider is starting from a stopped state
        if self._provider.status == "STOPPED":
            self._packets_received = 0
            self._total_pipeline_time_ms = 0.0
            
            # Start Bronze Parquet recorder
            self._session_id = packet_recorder.open_session(self._provider.provider_name)
            # Start Silver DuckDB batch logger
            duckdb_logger.start(self._provider.provider_name, self._session_id)
            
        await self._provider.start()

    async def pause(self) -> None:
        """Pauses the active provider."""
        if self._provider:
            await self._provider.pause()

    async def stop(self) -> None:
        """Stops the active provider and terminates database flush threads."""
        if not self._provider:
            return
            
        await self._provider.stop()
        
        # Flush and close storage session
        await packet_recorder.close_session()
        await duckdb_logger.stop()
        self._session_id = None

    async def set_speed(self, speed: float) -> None:
        """Dynamically adjusts provider feed rate."""
        if self._provider:
            await self._provider.set_speed(speed)

    async def step(self) -> None:
        """Advances the feed by 1 tick."""
        if self._provider:
            # Ensure session is open if stepping from Stopped
            if self._provider.status == "STOPPED":
                self._packets_received = 0
                self._total_pipeline_time_ms = 0.0
                self._session_id = packet_recorder.open_session(self._provider.provider_name)
                duckdb_logger.start(self._provider.provider_name, self._session_id)
                
            await self._provider.step()

    async def on_raw_packet(self, packet: RawPacket) -> None:
        """Pipeline entrypoint callback executed for every incoming provider packet."""
        start_time = time.perf_counter()
        self._packets_received += 1
        
        try:
            # 1. Store Raw Packet (Bronze Layer)
            await packet_recorder.record(packet)

            # 2. Parse Raw Packet to MarketEvent (Silver Layer)
            event = packet_parser.parse(packet)

            # 3. Publish to Event Bus
            await event_bus.publish(event)
            
            pipeline_time_ms = (time.perf_counter() - start_time) * 1000
            self._total_pipeline_time_ms += pipeline_time_ms

        except Exception as e:
            logger.error(f"Failed routing raw packet in ingestion pipeline: {e}")

    @property
    def packets_received(self) -> int:
        return self._packets_received

    @property
    def average_pipeline_time_ms(self) -> float:
        if self._packets_received == 0:
            return 0.0
        return self._total_pipeline_time_ms / self._packets_received

    def get_status(self) -> Dict[str, Any]:
        """Collects combined status details from the manager and provider."""
        status_info = {
            "session_id": self._session_id,
            "packets_received": self._packets_received,
            "avg_pipeline_time_ms": round(self.average_pipeline_time_ms, 3),
            "provider_status": "DISCONNECTED"
        }
        
        if self._provider:
            status_info.update(self._provider.get_status())
            status_info["provider_status"] = self._provider.status
            
        return status_info

# Global feed manager reference
feed_manager = MarketFeedManager()
