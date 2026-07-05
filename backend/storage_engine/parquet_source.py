import os
from typing import Optional, Dict, Any, List
import pyarrow.parquet as pq
from core.replay_source import ReplaySource
from utils.logger_setup import logger

class ParquetReplaySource(ReplaySource):
    """Concrete implementation of ReplaySource that streams data from Parquet files group-by-group."""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._pf: Optional[pq.ParquetFile] = None
        self._row_group_idx = 0
        self._row_idx = 0
        self._current_group_data: List[Dict[str, Any]] = []

    async def open(self) -> None:
        if not os.path.exists(self._file_path):
            raise FileNotFoundError(f"Replay Parquet file not found at: {self._file_path}")
        logger.info(f"Opening Parquet replay source: {self._file_path}")
        self._pf = pq.ParquetFile(self._file_path)
        self._row_group_idx = 0
        self._row_idx = 0
        self._load_next_row_group()

    def _load_next_row_group(self) -> None:
        if not self._pf:
            return

        if self._row_group_idx < self._pf.num_row_groups:
            logger.debug(f"Loading Parquet row group {self._row_group_idx} of {self._pf.num_row_groups}")
            table = self._pf.read_row_group(self._row_group_idx)
            # Convert PyArrow Table to list of dictionaries
            self._current_group_data = table.to_pylist()
            self._row_group_idx += 1
            self._row_idx = 0
        else:
            self._current_group_data = []

    async def read_next(self) -> Optional[Dict[str, Any]]:
        if not self._pf:
            return None

        if self._row_idx >= len(self._current_group_data):
            self._load_next_row_group()
            if not self._current_group_data:
                return None  # EOF reached

        row = self._current_group_data[self._row_idx]
        self._row_idx += 1
        
        # Standardize the format (e.g. handle timestamp objects from parquet)
        timestamp = row.get("received_timestamp") or row.get("timestamp")
        if timestamp and not isinstance(timestamp, str):
            # Convert datetime to string representation if needed
            if hasattr(timestamp, "isoformat"):
                timestamp = timestamp.isoformat()
            else:
                timestamp = str(timestamp)

        # Map back to standard tick dictionary structure
        return {
            "timestamp": timestamp,
            "symbol": row.get("symbol", "UNKNOWN"),
            "ltp": float(row.get("ltp", 0.0)) if row.get("ltp") is not None else float(row.get("raw_payload", "{}").count("ltp")), # fallback
            "open": float(row.get("open", 0.0)) if row.get("open") is not None else 0.0,
            "high": float(row.get("high", 0.0)) if row.get("high") is not None else 0.0,
            "low": float(row.get("low", 0.0)) if row.get("low") is not None else 0.0,
            "close": float(row.get("close", 0.0)) if row.get("close") is not None else 0.0,
            "volume": int(row.get("volume", 0)) if row.get("volume") is not None else 0,
            # If replaying a recorded raw session, we can extract details from raw_payload
            "raw_payload": row.get("raw_payload")
        }

    async def close(self) -> None:
        if self._pf:
            logger.info("Closing Parquet replay source file handle")
            self._pf = None
            self._current_group_data = []

    def reset(self) -> None:
        if self._pf:
            logger.info("Resetting Parquet replay source cursor")
            self._row_group_idx = 0
            self._row_idx = 0
            self._load_next_row_group()
        else:
            self._current_group_data = []
