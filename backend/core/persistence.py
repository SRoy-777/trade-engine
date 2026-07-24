"""
Trade State Persistence Manager
================================
Handles durable storage of paper trading state to DuckDB (local) and
Cloudflare R2 (remote backup).  All methods are safe to call from
asyncio background tasks — they never block the trade execution path.

Architecture:
  - DuckDB  : fast local writes on each trade event
  - R2      : backup of the .db file; downloaded on startup, uploaded
              after every write so restarts recover full state

Usage (injected by live_runner.py):
  persistence = PersistenceManager()
  await persistence.restore_from_r2()       # on startup
  asyncio.create_task(persistence.on_entry(symbol, active_trade, cash))
  asyncio.create_task(persistence.on_exit(trade_record, cash))
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# R2 / S3 client (lazy-initialised so import never fails if boto3 missing)
# ---------------------------------------------------------------------------

def _make_r2_client():
    """Create a boto3 S3 client pointed at Cloudflare R2."""
    try:
        import boto3
        account_id  = os.environ.get("R2_ACCOUNT_ID", "")
        access_key  = os.environ.get("R2_ACCESS_KEY_ID", "")
        secret_key  = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        if not (account_id and access_key and secret_key):
            logger.error("[Persistence] R2 credentials not set — R2 sync DISABLED. "
                         "Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY env vars.")
            return None
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        logger.info(f"[Persistence] R2 client initialised. Endpoint: {endpoint}")
        return client
    except ImportError:
        logger.error("[Persistence] boto3 not installed — R2 sync DISABLED.")
        return None
    except Exception as e:
        logger.error(f"[Persistence] Failed to initialise R2 client: {e}")
        return None


# ---------------------------------------------------------------------------
# PersistenceManager
# ---------------------------------------------------------------------------

class PersistenceManager:
    """
    Manages DuckDB writes and R2 sync for paper trading state.
    Safe to use from asyncio — all blocking I/O runs in a thread executor.
    """

    R2_DB_KEY = "state/trade_engine.db"          # path inside the R2 bucket

    def __init__(self):
        self._bucket  = os.environ.get("R2_BUCKET_NAME", "trades-trade-engine")
        self._r2      = _make_r2_client()
        self._db_path = self._resolve_db_path()
        self._conn    = None   # obtained lazily from the singleton manager
        logger.info(f"[Persistence] DB path resolved to: {self._db_path} | "
                    f"R2 enabled: {self._r2 is not None} | Bucket: {self._bucket}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_db_path(self) -> str:
        """Find the DuckDB file path — always prefer settings to stay in sync with the app."""
        try:
            from config.config import settings
            path = settings.DATABASE_PATH
            logger.info(f"[Persistence] Using DATABASE_PATH from settings: {path}")
            return path
        except Exception:
            pass
        # Fallback chain
        for candidate in [
            os.environ.get("DATABASE_PATH", ""),
            "storage/trade_engine.db",
        ]:
            if candidate:
                return candidate
        return "storage/trade_engine.db"

    def _get_conn(self):
        """Obtain the shared DuckDB connection (reuse existing singleton)."""
        if self._conn is not None:
            return self._conn
        try:
            from storage_engine.connection import db_manager
            self._conn = db_manager.connect()
        except Exception as e:
            logger.error(f"[Persistence] Cannot get DuckDB connection: {e}")
            self._conn = None
        return self._conn

    def _now_ist(self) -> datetime:
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.now(ist).replace(tzinfo=None)

    def invalidate_conn(self):
        """Call this after db_manager is reconnected so we pick up the new connection."""
        self._conn = None

    # ------------------------------------------------------------------
    # R2 sync (blocking — always call via run_in_executor)
    # ------------------------------------------------------------------

    def _upload_db_sync(self):
        """Upload the local DuckDB file to R2 (blocking, no DuckDB calls here)."""
        if not self._r2:
            logger.error("[Persistence] _upload_db_sync called but R2 client is None")
            return
        try:
            db_path = self._db_path
            if not os.path.exists(db_path):
                logger.error(f"[Persistence] DB file not found for upload: {db_path} "
                             f"(CWD: {os.getcwd()})")
                return
            size = os.path.getsize(db_path)
            with open(db_path, "rb") as f:
                self._r2.put_object(Bucket=self._bucket, Key=self.R2_DB_KEY, Body=f)
            logger.info(f"[Persistence] DuckDB ({size} bytes) synced to R2 at "
                        f"s3://{self._bucket}/{self.R2_DB_KEY}")
        except Exception as e:
            logger.error(f"[Persistence] R2 upload FAILED: {type(e).__name__}: {e}")

    def _download_db_sync(self):
        """Download the DuckDB file from R2 on startup (blocking)."""
        if not self._r2:
            logger.info("[Persistence] R2 not configured — using local DB.")
            return
        try:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._r2.download_file(self._bucket, self.R2_DB_KEY, self._db_path)
            size = os.path.getsize(self._db_path)
            logger.info(f"[Persistence] Restored DuckDB ({size} bytes) from R2 → {self._db_path}")
        except Exception as e:
            logger.info(f"[Persistence] No existing DB on R2 (first run OK): {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def restore_from_r2(self):
        """Download the DuckDB file from R2 on startup BEFORE connecting to DuckDB."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._download_db_sync)

    async def _sync_to_r2(self):
        """
        Checkpoint DuckDB (in asyncio thread, safe) then upload file in executor.
        Called after every write to ensure R2 is always up to date.
        """
        # CHECKPOINT must run on the same thread as other DuckDB operations
        try:
            conn = self._get_conn()
            if conn:
                conn.execute("CHECKPOINT")
        except Exception as e:
            logger.warning(f"[Persistence] CHECKPOINT failed (upload will still attempt): {e}")

        # File upload is blocking I/O — run in executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._upload_db_sync)

    # ------------------------------------------------------------------
    # Trade event hooks (called by orb.py after memory is updated)
    # ------------------------------------------------------------------

    async def on_entry(self, symbol: str, active_trade: Dict[str, Any], cash: float):
        """
        Persist an open position to DuckDB and sync to R2.
        Called as a background task after active_trade is set in memory.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                logger.error("[Persistence] on_entry: DuckDB connection is None — skipping")
                return
            now = self._now_ist()
            direction = "LONG" if active_trade.get("side") == "BUY" else "SHORT"

            # DuckDB upsert syntax (ON CONFLICT DO UPDATE SET)
            conn.execute("""
                INSERT INTO paper_positions
                    (symbol, direction, entry_time, entry_price, qty,
                     stop_loss, take_profit, setup, entry_fees, order_id, session_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    direction   = EXCLUDED.direction,
                    entry_time  = EXCLUDED.entry_time,
                    entry_price = EXCLUDED.entry_price,
                    qty         = EXCLUDED.qty,
                    stop_loss   = EXCLUDED.stop_loss,
                    take_profit = EXCLUDED.take_profit,
                    setup       = EXCLUDED.setup,
                    entry_fees  = EXCLUDED.entry_fees,
                    order_id    = EXCLUDED.order_id,
                    session_date = EXCLUDED.session_date
            """, [
                symbol,
                direction,
                active_trade.get("entry_time", now),
                active_trade.get("entry_price", 0.0),
                active_trade.get("qty", 0),
                active_trade.get("stop_loss", 0.0),
                active_trade.get("take_profit", 0.0),
                active_trade.get("setup", "ORB"),
                active_trade.get("entry_fees", 0.0),
                active_trade.get("order_id", ""),
                now.date(),
            ])
            await self._persist_cash(cash, conn)
            logger.info(f"[Persistence] Open position saved to DuckDB: {symbol} {direction} "
                        f"entry=Rs.{active_trade.get('entry_price', 0):.2f}")
            await self._sync_to_r2()
        except Exception as e:
            logger.error(f"[Persistence] on_entry FAILED for {symbol}: {type(e).__name__}: {e}")

    async def on_exit(self, trade_record: Dict[str, Any], cash: float):
        """
        Persist a completed trade and remove the open position from DuckDB.
        Called as a background task after trade_history.append() in memory.
        """
        try:
            conn = self._get_conn()
            if conn is None:
                logger.error("[Persistence] on_exit: DuckDB connection is None — skipping")
                return
            now = self._now_ist()
            conn.execute("""
                INSERT INTO paper_trades
                    (trade_id, session_date, symbol, direction, setup,
                     entry_time, entry_price, qty, exit_time, exit_price,
                     gross_pnl, fees, net_pnl, exit_reason, hold_mins)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                trade_record.get("Trade_ID", 0),
                now.date(),
                trade_record.get("Symbol", ""),
                trade_record.get("Direction", ""),
                trade_record.get("Setup", ""),
                trade_record.get("Entry_Time", now),
                trade_record.get("Entry_Price", 0.0),
                trade_record.get("Qty", 0),
                trade_record.get("Exit_Time", now),
                trade_record.get("Exit_Price", 0.0),
                trade_record.get("Gross_PnL", 0.0),
                trade_record.get("Fees", 0.0),
                trade_record.get("Net_PnL", 0.0),
                trade_record.get("Exit_Reason", ""),
                trade_record.get("Hold_Time_Mins", 0),
            ])
            # Remove from open positions
            conn.execute("DELETE FROM paper_positions WHERE symbol = ?",
                         [trade_record.get("Symbol", "")])
            await self._persist_cash(cash, conn)
            logger.info(f"[Persistence] Closed trade saved to DuckDB: "
                        f"{trade_record.get('Symbol')} "
                        f"Net PnL: Rs.{trade_record.get('Net_PnL', 0):.2f}")
            await self._sync_to_r2()
        except Exception as e:
            logger.error(f"[Persistence] on_exit FAILED: {type(e).__name__}: {e}")

    async def _persist_cash(self, cash: float, conn):
        """Update cash balance row (upsert)."""
        conn.execute("""
            INSERT INTO paper_portfolio (id, cash_inr, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                cash_inr   = EXCLUDED.cash_inr,
                updated_at = EXCLUDED.updated_at
        """, [cash, self._now_ist()])

    # ------------------------------------------------------------------
    # Startup restore helpers (called by live_runner.py)
    # ------------------------------------------------------------------

    def load_open_positions(self) -> Dict[str, Dict[str, Any]]:
        """Read paper_positions → dict keyed by symbol in active_trade format."""
        result = {}
        try:
            conn = self._get_conn()
            if conn is None:
                return result
            rows = conn.execute("""
                SELECT symbol, direction, entry_time, entry_price, qty,
                       stop_loss, take_profit, setup, entry_fees, order_id
                FROM paper_positions
            """).fetchall()
            for row in rows:
                symbol, direction, entry_time, entry_price, qty, sl, tp, setup, fees, order_id = row
                side = "BUY" if direction == "LONG" else "SELL"
                result[symbol] = {
                    "order_id": order_id or "",
                    "side": side,
                    "qty": qty,
                    "entry_price": entry_price,
                    "entry_time": entry_time if isinstance(entry_time, datetime)
                                  else datetime.fromisoformat(str(entry_time)),
                    "setup": setup,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "initial_risk": abs(entry_price - sl),
                    "max_price": entry_price,
                    "min_price": entry_price,
                    "entry_fees": fees,
                    "trigger_volume": 0,
                    "prev_candle_dir": "UNKNOWN",
                    "trade_trend": "UNKNOWN",
                    "trade_type": "ORB_BREAKOUT",
                }
            if result:
                logger.info(f"[Persistence] Restored {len(result)} open position(s): {list(result.keys())}")
        except Exception as e:
            logger.error(f"[Persistence] load_open_positions FAILED: {type(e).__name__}: {e}")
        return result

    def load_trade_history(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read paper_trades → dict of lists keyed by symbol."""
        result: Dict[str, List[Dict[str, Any]]] = {}
        try:
            conn = self._get_conn()
            if conn is None:
                return result
            rows = conn.execute("""
                SELECT trade_id, symbol, direction, setup, entry_time, entry_price,
                       qty, exit_time, exit_price, gross_pnl, fees, net_pnl,
                       exit_reason, hold_mins
                FROM paper_trades
                ORDER BY entry_time ASC
            """).fetchall()
            for row in rows:
                (tid, sym, direction, setup, entry_time, entry_price,
                 qty, exit_time, exit_price, gross_pnl, fees, net_pnl,
                 exit_reason, hold_mins) = row
                record = {
                    "Trade_ID": tid,
                    "Symbol": sym,
                    "Direction": direction,
                    "Setup": setup,
                    "Entry_Time": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                    "Entry_Price": entry_price,
                    "Qty": qty,
                    "Exit_Time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time),
                    "Exit_Price": exit_price,
                    "Gross_PnL": gross_pnl,
                    "Fees": fees,
                    "Net_PnL": net_pnl,
                    "Exit_Reason": exit_reason,
                    "Hold_Time_Mins": hold_mins,
                    "Entry_Candle_Volume": 0,
                    "Prev_Candle_Direction": "UNKNOWN",
                    "Trade_Trend": "UNKNOWN",
                    "Trade_Type": "ORB_BREAKOUT",
                }
                result.setdefault(sym, []).append(record)
            total = sum(len(v) for v in result.values())
            if total:
                logger.info(f"[Persistence] Restored {total} historical trade(s) from DuckDB.")
        except Exception as e:
            logger.error(f"[Persistence] load_trade_history FAILED: {type(e).__name__}: {e}")
        return result

    def load_cash(self) -> Optional[float]:
        """Read the last saved cash balance from paper_portfolio."""
        try:
            conn = self._get_conn()
            if conn is None:
                return None
            row = conn.execute(
                "SELECT cash_inr FROM paper_portfolio WHERE id = 1"
            ).fetchone()
            if row:
                logger.info(f"[Persistence] Restored cash balance: Rs.{row[0]:,.2f}")
                return row[0]
        except Exception as e:
            logger.error(f"[Persistence] load_cash FAILED: {type(e).__name__}: {e}")
        return None
