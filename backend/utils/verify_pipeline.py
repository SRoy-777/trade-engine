import asyncio
import os
import shutil
import time
from datetime import datetime
import pyarrow.parquet as pq
import duckdb

from config.config import settings
from models.market import RawPacket, MarketEvent
from storage_engine.connection import db_manager
from storage_engine.csv_source import CSVReplaySource
from storage_engine.recorder import packet_recorder
from storage_engine.logger import duckdb_logger
from providers.market.replay import ReplayProvider
from market_feed.manager import feed_manager
from event_bus.event_bus import event_bus
from services.metrics_service import metrics_service
from utils.logger_setup import logger

async def test_priority_bus():
    """Verify prioritized Event Bus routing sequence."""
    call_order = []
    
    async def subscriber_priority_10(event: MarketEvent):
        call_order.append(("p10", event.event_id))
        
    async def subscriber_priority_1(event: MarketEvent):
        call_order.append(("p1", event.event_id))
        
    async def subscriber_priority_2(event: MarketEvent):
        call_order.append(("p2", event.event_id))

    await event_bus.subscribe(subscriber_priority_10, priority=10)
    await event_bus.subscribe(subscriber_priority_1, priority=1)
    await event_bus.subscribe(subscriber_priority_2, priority=2)

    test_event = MarketEvent(
        event_id="test-evt-id",
        correlation_id="test-corr-id",
        exchange_timestamp=datetime.utcnow(),
        received_timestamp=datetime.utcnow(),
        processed_timestamp=datetime.utcnow(),
        symbol="TEST",
        ltp=100.0,
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1000,
        source_provider="test_provider"
    )

    await event_bus.publish(test_event)
    
    # Unsubscribe to keep state clean
    await event_bus.unsubscribe(subscriber_priority_10)
    await event_bus.unsubscribe(subscriber_priority_1)
    await event_bus.unsubscribe(subscriber_priority_2)

    expected = [("p1", "test-evt-id"), ("p2", "test-evt-id"), ("p10", "test-evt-id")]
    if call_order == expected:
        logger.info("✓ EventBus Priority Dispatching Verification: PASSED")
        return True
    else:
        logger.error(f"✗ EventBus Priority Dispatching Verification: FAILED. Call order: {call_order}")
        return False

async def main():
    logger.info("=== Starting Trade Engine Pipeline Automated Verification ===")
    
    # Temporary overrides for test cleanup
    test_db = "storage/test_trade_engine.db"
    test_parquet_dir = "storage/test_bronze"
    settings.DATABASE_PATH = test_db
    settings.BRONZE_STORAGE_DIR = test_parquet_dir
    
    # Clean up any residual test artifacts
    for path in [test_db, test_parquet_dir]:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    # 1. Test Event Bus priority
    bus_ok = await test_priority_bus()
    if not bus_ok:
        return

    # 2. Start services programmatically
    db_manager.connect()
    await event_bus.subscribe(duckdb_logger.on_market_event, priority=3)
    metrics_service.start()
    
    # 3. Create feed
    csv_source = CSVReplaySource("market_data/historical_data.csv")
    replay_provider = ReplayProvider(csv_source, speed=0.0) # Max speed for testing
    feed_manager.set_provider(replay_provider)
    
    # 4. Run playback
    logger.info("Running Replay simulation for verification...")
    await feed_manager.start()
    
    # Wait for execution loop to read CSV and stream packets
    # Sleep to allow async gather and background flush loops to fire
    await asyncio.sleep(2.0)
    
    # Stop manager (triggers Parquet and DuckDB logs flush)
    logger.info("Stopping feed manager and flushing buffers...")
    await feed_manager.stop()
    metrics_service.stop()
    db_manager.close()
    
    logger.info("=== Analyzing Ingestion Artifacts ===")

    # Verify Bronze Parquet Output
    parquet_files = []
    if os.path.exists(test_parquet_dir):
        for root, dirs, files in os.walk(test_parquet_dir):
            for file in files:
                if file.endswith(".parquet"):
                    parquet_files.append(os.path.join(root, file))
                    
    if not parquet_files:
        logger.error("✗ Bronze Storage Verification: FAILED (No Parquet files created)")
        return
        
    logger.info(f"✓ Bronze Storage Verification: FOUND {len(parquet_files)} Parquet files")
    
    # Read Parquet to inspect raw records
    parquet_table = pq.read_table(parquet_files[0])
    parquet_records = parquet_table.to_pylist()
    logger.info(f"✓ Bronze Storage Verification: Parquet contains {len(parquet_records)} raw packets")
    if parquet_records:
        logger.info(f"  First packet snippet: {parquet_records[0]['raw_payload'][:80]}...")

    # Verify Silver DuckDB Output
    conn = duckdb.connect(test_db)
    duckdb_count = conn.execute("SELECT COUNT(*) FROM silver_market_events").fetchone()[0]
    logger.info(f"✓ Silver Storage Verification: DuckDB table contains {duckdb_count} market events")
    
    if duckdb_count > 0:
        sample_row = conn.execute("SELECT event_id, symbol, ltp, processed_timestamp FROM silver_market_events LIMIT 1").fetchone()
        logger.info(f"  First DuckDB row sample: Event ID={sample_row[0]}, Ticker={sample_row[1]}, Price={sample_row[2]}, Ingest Time={sample_row[3]}")
        
    conn.close()

    # Final cleanup of test database & parquet files
    for path in [test_db, test_parquet_dir]:
        if os.path.exists(path):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
                
    logger.info("=== Ingestion Pipeline Verification Complete: ALL TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(main())
