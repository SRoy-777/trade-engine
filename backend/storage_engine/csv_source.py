import csv
import os
from typing import Optional, Dict, Any
from core.replay_source import ReplaySource
from utils.logger_setup import logger

class CSVReplaySource(ReplaySource):
    """Concrete implementation of ReplaySource that streams data from CSV files."""
    
    def __init__(self, file_path: str):
        self._file_path = file_path
        self._file = None
        self._reader = None

    async def open(self) -> None:
        if not os.path.exists(self._file_path):
            raise FileNotFoundError(f"Replay CSV file not found at: {self._file_path}")
        logger.info(f"Opening CSV replay source: {self._file_path}")
        self._file = open(self._file_path, mode='r', newline='', encoding='utf-8')
        self._reader = csv.DictReader(self._file)

    async def read_next(self) -> Optional[Dict[str, Any]]:
        if not self._reader or not self._file:
            return None
        
        try:
            # Read single row
            row = next(self._reader)
            
            # Map and cast fields
            return {
                "timestamp": row.get("timestamp") or row.get("exchange_timestamp"),
                "symbol": row.get("symbol", "UNKNOWN"),
                "ltp": float(row.get("ltp", 0.0)) if row.get("ltp") else 0.0,
                "open": float(row.get("open", 0.0)) if row.get("open") else 0.0,
                "high": float(row.get("high", 0.0)) if row.get("high") else 0.0,
                "low": float(row.get("low", 0.0)) if row.get("low") else 0.0,
                "close": float(row.get("close", 0.0)) if row.get("close") else 0.0,
                "volume": int(float(row.get("volume", 0))) if row.get("volume") else 0,
            }
        except StopIteration:
            return None
        except Exception as e:
            logger.error(f"Error parsing row in CSV source: {e}")
            return None

    async def close(self) -> None:
        if self._file:
            logger.info("Closing CSV replay source file handle")
            try:
                self._file.close()
            except Exception as e:
                logger.error(f"Error closing CSV file: {e}")
            self._file = None
            self._reader = None

    def reset(self) -> None:
        if self._file:
            logger.info("Resetting CSV replay source cursor")
            self._file.seek(0)
            self._reader = csv.DictReader(self._file)
