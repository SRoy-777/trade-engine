import os
import csv
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import urllib.request
import json
from utils.logger_setup import logger

class HistoricalDataProvider(ABC):
    """Abstract base class for loading and streaming historical market data."""

    @abstractmethod
    async def load_data(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        """Loads and prepares the dataset for the specified symbol and time window."""
        pass

    @abstractmethod
    async def get_next_tick(self) -> Optional[Dict[str, Any]]:
        """Yields the next sequential market tick/bar, or None if EOF is reached."""
        pass

    @abstractmethod
    def is_tick_level(self) -> bool:
        """Returns True if the data represents tick-level data, False if 1-minute/bar OHLC."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Closes any open resource handles."""
        pass


class CSVHistoricalProvider(HistoricalDataProvider):
    """Streams data from local CSV files."""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._file = None
        self._reader = None
        self._symbol = None
        self._start_time = None
        self._end_time = None
        self._is_tick = False

    async def load_data(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        self._symbol = symbol
        self._start_time = start_time
        self._end_time = end_time
        
        if not os.path.exists(self._file_path):
            raise FileNotFoundError(f"Historical CSV file not found at: {self._file_path}")
            
        self._file = open(self._file_path, mode='r', newline='', encoding='utf-8')
        self._reader = csv.DictReader(self._file)
        
        # Look at the first row to detect if it's tick-level or bar-level
        first_row = next(self._reader, None)
        if first_row:
            has_ohlc = all(k in first_row for k in ["open", "high", "low", "close"])
            self._is_tick = not has_ohlc
            # Reset file pointer
            self._file.seek(0)
            self._reader = csv.DictReader(self._file)

    async def get_next_tick(self) -> Optional[Dict[str, Any]]:
        if not self._reader:
            return None
        try:
            while True:
                row = next(self._reader)
                
                # Check symbol if present
                row_symbol = row.get("symbol")
                if row_symbol and row_symbol.upper() != self._symbol.upper():
                    continue

                # Parse timestamp
                ts_str = row.get("timestamp") or row.get("exchange_timestamp")
                if not ts_str:
                    continue
                
                # Clean timestamp strings
                clean_ts = ts_str.replace("Z", "+00:00")
                try:
                    ts = datetime.fromisoformat(clean_ts)
                except ValueError:
                    try:
                        ts = datetime.strptime(clean_ts.split("+")[0], "%Y-%m-%dT%H:%M:%S.%f")
                    except ValueError:
                        ts = datetime.strptime(clean_ts.split("+")[0], "%Y-%m-%dT%H:%M:%S")

                # Filter by window
                if self._start_time and ts < self._start_time:
                    continue
                if self._end_time and ts > self._end_time:
                    continue

                # Get prices
                close_p = float(row.get("close", 0.0)) if row.get("close") else 0.0
                ltp = float(row.get("ltp", close_p)) if row.get("ltp") else close_p
                open_p = float(row.get("open", ltp)) if row.get("open") else ltp
                high_p = float(row.get("high", ltp)) if row.get("high") else ltp
                low_p = float(row.get("low", ltp)) if row.get("low") else ltp
                volume = int(float(row.get("volume", 0))) if row.get("volume") else 0
                
                # Bids & Asks
                bid = float(row.get("bid", ltp)) if row.get("bid") else None
                ask = float(row.get("ask", ltp)) if row.get("ask") else None

                return {
                    "timestamp": ts,
                    "symbol": self._symbol,
                    "ltp": ltp,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": volume,
                    "bid": bid,
                    "ask": ask
                }
        except StopIteration:
            return None
        except Exception as e:
            logger.error(f"Error reading row in CSVHistoricalProvider: {e}")
            return None

    def is_tick_level(self) -> bool:
        return self._is_tick

    async def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._reader = None


class ParquetHistoricalProvider(HistoricalDataProvider):
    """Streams data from local Parquet files using PyArrow."""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._pf = None
        self._row_group_idx = 0
        self._row_idx = 0
        self._current_group_data: List[Dict[str, Any]] = []
        self._symbol = None
        self._start_time = None
        self._end_time = None
        self._is_tick = False

    async def load_data(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        import pyarrow.parquet as pq
        self._symbol = symbol
        self._start_time = start_time
        self._end_time = end_time
        
        if not os.path.exists(self._file_path):
            raise FileNotFoundError(f"Historical Parquet file not found at: {self._file_path}")
            
        self._pf = pq.ParquetFile(self._file_path)
        self._row_group_idx = 0
        self._row_idx = 0
        
        # Inspect schema to check if it has open/high/low/close
        schema = self._pf.schema.names
        has_ohlc = all(k in schema for k in ["open", "high", "low", "close"])
        self._is_tick = not has_ohlc
        self._load_next_row_group()

    def _load_next_row_group(self) -> None:
        if not self._pf:
            return
        if self._row_group_idx < self._pf.num_row_groups:
            table = self._pf.read_row_group(self._row_group_idx)
            self._current_group_data = table.to_pylist()
            self._row_group_idx += 1
            self._row_idx = 0
        else:
            self._current_group_data = []

    async def get_next_tick(self) -> Optional[Dict[str, Any]]:
        if not self._pf:
            return None
            
        while True:
            if self._row_idx >= len(self._current_group_data):
                self._load_next_row_group()
                if not self._current_group_data:
                    return None  # EOF
                    
            row = self._current_group_data[self._row_idx]
            self._row_idx += 1
            
            # Filter symbol if present
            row_symbol = row.get("symbol")
            if row_symbol and row_symbol.upper() != self._symbol.upper():
                continue
                
            # Parse timestamp
            ts_val = row.get("timestamp") or row.get("received_timestamp")
            if not ts_val:
                continue
                
            if isinstance(ts_val, (int, float)):
                ts = datetime.fromtimestamp(ts_val)
            elif isinstance(ts_val, str):
                ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            else:
                ts = ts_val  # already datetime/timestamp object
                
            # Filter by window
            if self._start_time and ts < self._start_time:
                continue
            if self._end_time and ts > self._end_time:
                continue

            close_p = float(row.get("close", 0.0)) if row.get("close") is not None else 0.0
            ltp = float(row.get("ltp", close_p)) if row.get("ltp") is not None else close_p
            open_p = float(row.get("open", ltp)) if row.get("open") is not None else ltp
            high_p = float(row.get("high", ltp)) if row.get("high") is not None else ltp
            low_p = float(row.get("low", ltp)) if row.get("low") is not None else ltp
            volume = int(row.get("volume", 0))
            
            bid = float(row.get("bid", ltp)) if row.get("bid") is not None else None
            ask = float(row.get("ask", ltp)) if row.get("ask") is not None else None

            return {
                "timestamp": ts,
                "symbol": self._symbol,
                "ltp": ltp,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "bid": bid,
                "ask": ask
            }

    def is_tick_level(self) -> bool:
        return self._is_tick

    async def close(self) -> None:
        self._pf = None
        self._current_group_data = []


class DhanHistoricalProvider(HistoricalDataProvider):
    """Streams data fetched from Dhan's Historical Charts/Candles APIs."""

    def __init__(self, access_token: str, client_id: str):
        self._access_token = access_token
        self._client_id = client_id
        self._candles: List[Dict[str, Any]] = []
        self._idx = 0
        self._symbol = None

    async def load_data(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        self._symbol = symbol
        self._candles = []
        self._idx = 0
        
        url = "https://api.dhan.co/charts/historical"
        headers = {
            "Content-Type": "application/json",
            "access-token": self._access_token
        }
        
        payload = {
            "symbol": symbol,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryDate": "",
            "fromDate": start_time.strftime("%Y-%m-%d"),
            "toDate": end_time.strftime("%Y-%m-%d"),
            "candleResolution": "1"
        }
        
        try:
            req = urllib.request.Request(
                url, 
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3.0) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                if "data" in res_data:
                    c_data = res_data["data"]
                    for i in range(len(c_data.get("t", []))):
                        ts = datetime.fromtimestamp(c_data["t"][i])
                        self._candles.append({
                            "timestamp": ts,
                            "symbol": symbol,
                            "ltp": float(c_data["c"][i]),
                            "open": float(c_data["o"][i]),
                            "high": float(c_data["h"][i]),
                            "low": float(c_data["l"][i]),
                            "close": float(c_data["c"][i]),
                            "volume": int(c_data["v"][i]),
                            "bid": None,
                            "ask": None
                        })
        except Exception as err:
            logger.warning(f"Failed to fetch historical data from Dhan API: {err}. Falling back to offline generated mock.")
            self._generate_offline_mock(symbol, start_time, end_time)

    def _generate_offline_mock(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        import random
        random.seed(42)
        current_date = start_time
        base_price = 1041.0 if "SBIN" in symbol.upper() else 980.0
        
        while current_date <= end_time:
            if current_date.weekday() < 5:
                price = base_price
                for minute in range(376):
                    ts = current_date.replace(hour=9, minute=15) + timedelta(minutes=minute)
                    price = price * (1 + random.normalvariate(0.00002, 0.0005))
                    self._candles.append({
                        "timestamp": ts,
                        "symbol": symbol,
                        "ltp": round(price * 20) / 20,
                        "open": round(price * 20) / 20,
                        "high": round(price * 20) / 20,
                        "low": round(price * 20) / 20,
                        "close": round(price * 20) / 20,
                        "volume": random.randint(1000, 5000),
                        "bid": None,
                        "ask": None
                    })
            current_date += timedelta(days=1)

    async def get_next_tick(self) -> Optional[Dict[str, Any]]:
        if self._idx < len(self._candles):
            candle = self._candles[self._idx]
            self._idx += 1
            return candle
        return None

    def is_tick_level(self) -> bool:
        return False

    async def close(self) -> None:
        self._candles = []
        self._idx = 0


class RecordedReplayHistoricalProvider(HistoricalDataProvider):
    """Streams data recorded in DuckDB."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = None
        self._cursor = None
        self._symbol = None
        self._is_tick = True

    async def load_data(self, symbol: str, start_time: datetime, end_time: datetime) -> None:
        import duckdb
        self._symbol = symbol
        self._conn = duckdb.connect(self._db_path)
        
        tables = [r[0] for r in self._conn.execute("SHOW TABLES").fetchall()]
        if "market_events" not in tables:
            raise ValueError("DuckDB database does not contain 'market_events' table.")
            
        query = """
            SELECT exchange_timestamp, ltp, open, high, low, close, volume 
            FROM market_events 
            WHERE symbol = ? AND exchange_timestamp BETWEEN ? AND ?
            ORDER BY exchange_timestamp ASC
        """
        self._cursor = self._conn.execute(query, [symbol, start_time, end_time])

    async def get_next_tick(self) -> Optional[Dict[str, Any]]:
        if not self._cursor:
            return None
        row = self._cursor.fetchone()
        if not row:
            return None
            
        ts, ltp, open_p, high_p, low_p, close_p, volume = row
        return {
            "timestamp": ts,
            "symbol": self._symbol,
            "ltp": float(ltp),
            "open": float(open_p),
            "high": float(high_p),
            "low": float(low_p),
            "close": float(close_p),
            "volume": int(volume),
            "bid": None,
            "ask": None
        }

    def is_tick_level(self) -> bool:
        return self._is_tick

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            self._cursor = None
