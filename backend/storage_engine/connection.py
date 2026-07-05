import os
import duckdb
import threading
from config.config import settings
from utils.logger_setup import logger

class DuckDBConnectionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DuckDBConnectionManager, cls).__new__(cls)
                cls._instance._conn = None
        return cls._instance

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            # Ensure the database directory exists
            db_path = settings.DATABASE_PATH
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            
            logger.info(f"Connecting to DuckDB database: {db_path}")
            # DuckDB connection is thread-safe within a single process
            self._conn = duckdb.connect(db_path)
            self._initialize_schema()
        return self._conn

    def _initialize_schema(self):
        logger.info("Initializing DuckDB Silver layer database schemas")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS silver_market_events (
                event_id VARCHAR PRIMARY KEY,
                correlation_id VARCHAR,
                exchange_timestamp TIMESTAMP,
                received_timestamp TIMESTAMP,
                processed_timestamp TIMESTAMP,
                symbol VARCHAR,
                ltp DOUBLE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                source_provider VARCHAR
            );
        """)
        # We can also add an index on symbol and timestamp for fast analytical queries later
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_silver_symbol ON silver_market_events (symbol);")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_silver_timestamp ON silver_market_events (processed_timestamp);")

    def close(self):
        if self._conn:
            logger.info("Closing DuckDB database connection")
            try:
                self._conn.close()
            except Exception as e:
                logger.error(f"Error closing DuckDB: {e}")
            self._conn = None

db_manager = DuckDBConnectionManager()
