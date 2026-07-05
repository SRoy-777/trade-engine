import os
import time
import asyncio
from datetime import datetime
import pyarrow as pa
import pyarrow.parquet as pq
from typing import List, Dict, Optional
from config.config import settings
from models.market import RawPacket
from utils.logger_setup import logger

class PacketRecorder:
    def __init__(self):
        self._buffer: List[Dict] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._writer: Optional[pq.ParquetWriter] = None
        self._current_file: Optional[str] = None
        self._session_id: Optional[str] = None
        self._provider_name: Optional[str] = None
        self._is_active = False

        # PyArrow Schema for Bronze layer
        self._schema = pa.schema([
            ('packet_id', pa.string()),
            ('provider', pa.string()),
            ('received_timestamp', pa.timestamp('us')),
            ('raw_payload', pa.string())
        ])

    def open_session(self, provider_name: str) -> str:
        """Opens a new Bronze recording session and initializes the Parquet writer."""
        self._provider_name = provider_name
        self._session_id = f"session_{provider_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Build path: storage/bronze/YYYY-MM-DD/
        date_str = datetime.utcnow().strftime('%Y-%m-%d')
        dir_path = os.path.join(settings.BRONZE_STORAGE_DIR, date_str)
        os.makedirs(dir_path, exist_ok=True)

        self._current_file = os.path.join(dir_path, f"{self._session_id}.parquet")
        logger.info(f"Opening Bronze recording session. File: {self._current_file}", extra={
            "session_id": self._session_id, "provider": provider_name
        })

        self._writer = pq.ParquetWriter(self._current_file, self._schema, compression='snappy')
        self._is_active = True
        
        # Start background flush loop
        self._buffer.clear()
        self._flush_task = asyncio.create_task(self._flush_loop())
        return self._session_id

    async def record(self, packet: RawPacket) -> None:
        """Buffers raw packet for flushing."""
        if not self._is_active:
            return
            
        async with self._lock:
            self._buffer.append({
                'packet_id': packet.packet_id,
                'provider': packet.provider,
                'received_timestamp': packet.received_timestamp,
                'raw_payload': packet.raw_payload
            })

            # Check if buffer size limit is reached
            if len(self._buffer) >= settings.BATCH_FLUSH_SIZE:
                # Run flush as a separate task to avoid blocking the main stream ingestion
                asyncio.create_task(self.flush())

    async def flush(self) -> None:
        """Flushes the current in-memory buffer to the Parquet file."""
        if not self._is_active or not self._writer:
            return

        async with self._lock:
            if not self._buffer:
                return

            batch_to_write = list(self._buffer)
            self._buffer.clear()

        try:
            start_time = time.perf_counter()
            # Convert python dicts to pyarrow table
            table = pa.Table.from_pylist(batch_to_write, schema=self._schema)
            self._writer.write_table(table)
            flush_time_ms = (time.perf_counter() - start_time) * 1000
            
            logger.debug(f"Flushed {len(batch_to_write)} raw packets to Parquet", extra={
                "provider": self._provider_name,
                "session_id": self._session_id,
                "processing_time_ms": flush_time_ms
            })
        except Exception as e:
            logger.error(f"Error flushing Bronze Parquet batch: {e}", extra={
                "session_id": self._session_id
            })

    async def _flush_loop(self):
        """Periodic flush worker running in the background."""
        while self._is_active:
            try:
                await asyncio.sleep(settings.BATCH_FLUSH_INTERVAL_SECS)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Bronze flush loop: {e}")

    async def close_session(self) -> None:
        """Closes the session, flushes remaining packets, and shuts down the Parquet writer."""
        if not self._is_active:
            return

        logger.info(f"Closing Bronze recording session: {self._session_id}")
        self._is_active = False

        # Cancel periodic flush task
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        # Final flush
        await self.flush()

        # Close writer
        if self._writer:
            try:
                self._writer.close()
            except Exception as e:
                logger.error(f"Error closing ParquetWriter: {e}")
            self._writer = None

        self._session_id = None
        self._current_file = None
        self._provider_name = None

packet_recorder = PacketRecorder()
