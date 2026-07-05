import time
import asyncio
from typing import List, Tuple, Optional
from config.config import settings
from models.market import MarketEvent
from storage_engine.connection import db_manager
from utils.logger_setup import logger

class DuckDBLogger:
    def __init__(self):
        self._buffer: List[Tuple] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._total_inserts = 0
        self._is_running = False
        self._provider_name: Optional[str] = None
        self._session_id: Optional[str] = None

    def start(self, provider_name: str, session_id: str) -> None:
        """Starts the Silver layer database writer."""
        self._provider_name = provider_name
        self._session_id = session_id
        self._is_running = True
        self._buffer.clear()
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("Started DuckDB Silver Logger background worker")

    async def on_market_event(self, event: MarketEvent) -> None:
        """Subscriber callback to buffer incoming parsed market events."""
        if not self._is_running:
            return

        # Prepare tuple matching table schema:
        # event_id, correlation_id, exchange_timestamp, received_timestamp, processed_timestamp,
        # symbol, ltp, open, high, low, close, volume, source_provider
        event_data = (
            event.event_id,
            event.correlation_id,
            event.exchange_timestamp,
            event.received_timestamp,
            event.processed_timestamp,
            event.symbol,
            event.ltp,
            event.open,
            event.high,
            event.low,
            event.close,
            event.volume,
            event.source_provider
        )

        async with self._lock:
            self._buffer.append(event_data)
            
            # Flush immediately if buffer is full
            if len(self._buffer) >= settings.BATCH_FLUSH_SIZE:
                asyncio.create_task(self.flush())

    async def flush(self) -> None:
        """Batch inserts all buffered events into DuckDB using a background thread."""
        if not self._is_running:
            return

        async with self._lock:
            if not self._buffer:
                return
            batch_to_insert = list(self._buffer)
            self._buffer.clear()

        try:
            start_time = time.perf_counter()
            conn = db_manager.connect()

            # Execute insert natively in an executor thread to keep event loop free
            def do_insert():
                # We use parameterized insert
                cursor = conn.cursor()
                cursor.executemany("""
                    INSERT OR REPLACE INTO silver_market_events 
                    (event_id, correlation_id, exchange_timestamp, received_timestamp, processed_timestamp, 
                     symbol, ltp, open, high, low, close, volume, source_provider)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_to_insert)
            
            await asyncio.to_thread(do_insert)
            
            flush_time_ms = (time.perf_counter() - start_time) * 1000
            self._total_inserts += len(batch_to_insert)
            
            logger.debug(f"Flushed {len(batch_to_insert)} events to DuckDB Silver table", extra={
                "provider": self._provider_name,
                "session_id": self._session_id,
                "processing_time_ms": flush_time_ms
            })
        except Exception as e:
            logger.error(f"Error inserting silver market events batch to DuckDB: {e}", extra={
                "session_id": self._session_id
            })

    async def _flush_loop(self):
        """Worker loop that periodically flushes the buffer to DuckDB."""
        while self._is_running:
            try:
                await asyncio.sleep(settings.BATCH_FLUSH_INTERVAL_SECS)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Silver database flush loop: {e}")

    @property
    def total_inserts(self) -> int:
        return self._total_inserts

    async def stop(self) -> None:
        """Stops the database logger and flushes any pending entries."""
        if not self._is_running:
            return
            
        logger.info("Stopping DuckDB Silver Logger")
        self._is_running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # Final flush
        await self.flush()
        self._provider_name = None
        self._session_id = None

duckdb_logger = DuckDBLogger()
