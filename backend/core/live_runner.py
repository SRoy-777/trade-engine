import os
import csv
import yaml
import json
import urllib.request
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Callable, Optional, List
from core.broker.paper import PaperBroker
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.orb import ORBStrategy
from providers.market.dhan.market_provider import DhanMarketProvider
from providers.market.dhan.models import MarketPacket
from utils.logger_setup import logger
from providers.market.dhan.logger import dhan_logger
from providers.market.dhan.config import dhan_settings

class LiveTradingRunner:
    """Manages active live multi-symbol strategy execution, paper broker, and UI updates."""

    def __init__(self):
        self.provider: Optional[DhanMarketProvider] = None
        self.manager: Optional[StrategyManager] = None
        self.broker: Optional[PaperBroker] = None
        self.strategies: Dict[str, ORBStrategy] = {}
        
        # Configuration settings
        self.symbols: List[str] = []
        self.priority_ranking: List[str] = []
        self.allocation_strategy = "SINGLE_STOCK"
        self.allocation_weights: List[float] = [0.5, 0.3, 0.2]
        self.capital = 100000.0
        self.leverage = 5.0
        
        self.indices: Dict[str, Dict[str, Any]] = {
            "NIFTY_50": {"ltp": 0.0, "change_pct": 0.0, "trend": "NEUTRAL", "open": 0.0},
            "BANK_NIFTY": {"ltp": 0.0, "change_pct": 0.0, "trend": "NEUTRAL", "open": 0.0}
        }
        
        self.active = False
        self._ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        
        # Watchdog connection checks
        self.last_tick_times: Dict[str, float] = {}
        self.last_ohlc: Dict[str, Dict[str, Any]] = {}
        self.warning_symbols: List[str] = []
        self.connection_ok = True
        self.enable_live_stocks = False
        self._watchdog_task: Optional[asyncio.Task] = None
        self.current_bars: Dict[str, Dict[str, Any]] = {}
        self.last_cumulative_volumes: Dict[str, int] = {}

    def _load_symbol_mappings(self) -> Dict[str, str]:
        """Loads stock symbol to security ID token mapping from Nifty CSV files."""
        mappings = {}
        for base_dir in [".", ".."]:
            market_data_dir = os.path.join(base_dir, "market_data")
            if os.path.isdir(market_data_dir):
                for file_name in os.listdir(market_data_dir):
                    if file_name.endswith("_security_ids.csv"):
                        path = os.path.join(market_data_dir, file_name)
                        try:
                            with open(path, mode="r", encoding="utf-8") as f:
                                reader = csv.DictReader(f)
                                for row in reader:
                                    symbol = row["symbol"].strip().upper()
                                    sec_id = row["security_id"].strip()
                                    mappings[symbol] = sec_id
                        except Exception as e:
                            logger.error(f"Error reading mapping file {path}: {e}")
                
        if mappings:
            logger.info(f"Successfully loaded {len(mappings)} symbol mappings dynamically.")
        else:
            logger.warning("No symbol mappings loaded from market_data directory.")
            
        return mappings

    async def start(self, config: Dict[str, Any], ui_callback: Callable[[Dict[str, Any]], None]) -> None:
        """Starts a live multi-symbol strategy execution run with user-defined parameters."""
        await self.stop()

        self._ui_callback = ui_callback
        
        # Load mappings first to avoid scope resolution bugs on fast ticks
        mappings = self._load_symbol_mappings()
        
        # 1. Parse and extract configuration parameters
        self.symbols = [s.strip().upper() for s in config.get("symbols", ["SBIN", "BAJFINANCE", "INFY"])]
        
        # Enforce Dhan WebSocket limits (max 100 subscriptions per connection)
        # We reserve 2 slots for indices (Nifty 50 and Bank Nifty) and safety margins, limiting stock symbols to 90
        if len(self.symbols) > 90:
            logger.warning(f"[Live Runner] Dhan WebSocket supports a maximum of 100 subscriptions. Slicing your {len(self.symbols)} symbols to the first 90 to prevent connection drops.")
            self.symbols = self.symbols[:90]
            
        raw_priority = config.get("priority_ranking", self.symbols)
        self.priority_ranking = [s.strip().upper() for s in raw_priority if s.strip().upper() in self.symbols]
        self.allocation_strategy = config.get("allocation_strategy", "SINGLE_STOCK").upper()
        self.allocation_weights = [float(w) for w in config.get("allocation_weights", [0.50, 0.30, 0.20])]
        self.capital = float(config.get("capital", 100000.0))
        self.leverage = float(config.get("leverage", 5.0))

        logger.info(f"[Live Runner] Starting multi-symbol ORB strategy. Symbols: {self.symbols}, Capital: Rs.{self.capital}")

        # 2. Initialize paper broker and risk components
        self.broker = PaperBroker(initial_cash_inr=self.capital, latency_ms=50.0)
        risk = RiskController(
            max_capital_per_trade_inr=self.capital * self.leverage,
            max_daily_loss_inr=self.capital * 0.1,  # 10% daily loss limit
            margin_leverage_multiplier=self.leverage
        )
        self.manager = StrategyManager(self.broker, risk)
        
        # Configure allocation parameters in the Strategy Manager
        self.manager.update_allocation_config({
            "allocation_strategy": self.allocation_strategy,
            "priority_ranking": self.priority_ranking,
            "allocation_weights": self.allocation_weights,
            "total_capital": self.capital
        })

        # 3. Instantiate and register ORB strategy for each symbol
        config_path = "configs/orb.yaml"
        if not os.path.exists(config_path) and os.path.exists(os.path.join("..", config_path)):
            config_path = os.path.join("..", config_path)
            
        self.strategies = {}
        for sym in self.symbols:
            strat = ORBStrategy(config_path)
            strat.strategy_id = f"orb_{sym.lower()}"
            strat.name = f"ORB Strategy ({sym})"
            strat.symbol = sym
            strat.symbols = [sym]
            strat.capital_limit = self.capital
            strat.leverage = self.leverage
            
            self.manager.register_strategy(strat)
            self.strategies[sym] = strat

        # Clear watchdog states
        self.last_tick_times = {sym: datetime.now().timestamp() for sym in self.symbols}
        self.last_ohlc = {sym: {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0} for sym in self.symbols}
        self.warning_symbols = []
        self.connection_ok = True

        # 4a. Restore persisted state from R2 + DuckDB (if enabled)
        self._persistence = None
        if config.get("enable_persistence", False):
            try:
                from core.persistence import PersistenceManager
                self._persistence = PersistenceManager()
                logger.info("[Live Runner] Persistence enabled — restoring state from R2...")

                # Download DuckDB from R2 BEFORE connecting (must run first)
                await self._persistence.restore_from_r2()

                # Reconnect DuckDB so it picks up the downloaded file
                from storage_engine.connection import db_manager
                if db_manager._conn is not None:
                    db_manager.close()
                db_manager.connect()
                self._persistence.invalidate_conn()  # force re-acquire fresh connection

                # Restore cash balance
                saved_cash = self._persistence.load_cash()
                if saved_cash is not None:
                    self.broker._cash = saved_cash
                    logger.info(f"[Live Runner] Cash restored: Rs.{saved_cash:,.2f}")

                # Restore open positions into strategy instances
                open_positions = self._persistence.load_open_positions()
                for sym, pos in open_positions.items():
                    if sym in self.strategies:
                        self.strategies[sym].active_trade = pos
                        self.strategies[sym].trade_taken_today = True
                        logger.info(f"[Live Runner] Restored open position for {sym} — SL/TP monitoring will resume on next tick")

                # Restore trade history into strategy instances
                history_by_symbol = self._persistence.load_trade_history()
                for sym, history in history_by_symbol.items():
                    if sym in self.strategies:
                        self.strategies[sym].trade_history = history

                # Inject persistence manager into each strategy
                for strat in self.strategies.values():
                    strat._persistence = self._persistence

                logger.info("[Live Runner] State restore complete.")
            except Exception as e:
                logger.error(f"[Live Runner] Persistence restore failed (starting fresh): {e}")
                self._persistence = None

        # 4. Connect broker fills to notify the UI instantly
        original_callback = self.broker._fill_callback
        async def on_broker_fill(fill_event: Dict[str, Any]):
            if original_callback:
                await original_callback(fill_event)
            self.broadcast_update()

        self.broker.register_fill_callback(on_broker_fill)

        # 5. Initialize Dhan Market Feed
        self.provider = DhanMarketProvider()
        
        async def on_provider_tick(packet: MarketPacket):
            if not self.active:
                return
            
            packet.is_live_tick = True
            sym = packet.security_id
            
            # Check for Nifty/Bank Nifty index packets (Dhan Security ID: Nifty 50 = "13", Bank Nifty = "25")
            if sym in ("13", "25"):
                idx_key = "NIFTY_50" if sym == "13" else "BANK_NIFTY"
                open_val = packet.open or self.indices[idx_key].get("open") or packet.ltp
                ltp_val = packet.ltp
                pct = ((ltp_val - open_val) / open_val * 100.0) if open_val > 0 else 0.0
                trend = "BULLISH" if pct > 0.05 else "BEARISH" if pct < -0.05 else "NEUTRAL"
                self.indices[idx_key] = {
                    "ltp": ltp_val,
                    "open": open_val,
                    "change_pct": pct,
                    "trend": trend
                }
                if self.manager:
                    self.manager.indices[idx_key] = {
                        "ltp": ltp_val,
                        "open": open_val,
                        "trend": trend
                    }
                self.broadcast_update(packet)
                return
                
            # Re-map token to symbol name for strategy execution checks
            mapped_symbol = next((s for s, tid in mappings.items() if tid == sym), sym)
            
            # Record last tick timestamp for watchdog check
            if mapped_symbol in self.symbols:
                self.last_tick_times[mapped_symbol] = datetime.now().timestamp()
                self.last_ohlc[mapped_symbol] = {
                    "open": packet.open or packet.ltp,
                    "high": packet.high or packet.ltp,
                    "low": packet.low or packet.ltp,
                    "close": packet.close or packet.ltp,
                    "volume": packet.volume or 0
                }
                # Ensure the packet carries the parsed token symbol name
                packet.security_id = mapped_symbol
            
            # Feed packet to strategy manager (for broker real-time exits & PnL updates)
            if self.manager:
                await self.manager.on_tick(packet)
            
            # Aggregate 5-minute candles for strategy breakout check
            if mapped_symbol in self.symbols:
                # Align time to the preceding 5-minute boundary
                bar_minute = (packet.timestamp.minute // 5) * 5
                bar_timestamp = packet.timestamp.replace(minute=bar_minute, second=0, microsecond=0)
                
                if mapped_symbol not in self.current_bars:
                    self.current_bars[mapped_symbol] = {
                        "timestamp": bar_timestamp,
                        "open": packet.ltp,
                        "high": packet.ltp,
                        "low": packet.ltp,
                        "close": packet.ltp,
                        "latest_volume": packet.volume
                    }
                else:
                    bar = self.current_bars[mapped_symbol]
                    if bar_timestamp == bar["timestamp"]:
                        bar["high"] = max(bar["high"], packet.ltp)
                        bar["low"] = min(bar["low"], packet.ltp)
                        bar["close"] = packet.ltp
                        bar["latest_volume"] = packet.volume
                    elif bar_timestamp > bar["timestamp"]:
                        # Finalize completed 5m candle
                        bar_volume = 0
                        if bar["latest_volume"] is not None:
                            prev_cum_vol = self.last_cumulative_volumes.get(mapped_symbol, 0)
                            if prev_cum_vol > 0:
                                bar_volume = bar["latest_volume"] - prev_cum_vol
                            else:
                                bar_volume = bar["latest_volume"]
                                
                            if bar_volume < 0:
                                bar_volume = 0
                            
                            self.last_cumulative_volumes[mapped_symbol] = bar["latest_volume"]
                        
                        completed_candle = MarketPacket(
                            packet_type="Quote",
                            exchange_segment="NSE_EQ",
                            security_id=mapped_symbol,
                            timestamp=bar["timestamp"],
                            open=bar["open"],
                            high=bar["high"],
                            low=bar["low"],
                            close=bar["close"],
                            volume=bar_volume,
                            ltp=bar["close"]
                        )
                        
                        # Feed the 5m candle to the strategy manager
                        if self.manager:
                            logger.info(f"[Live Runner] Feeding completed 5m candle for {mapped_symbol} at {bar['timestamp'].strftime('%H:%M')}: O={bar['open']}, H={bar['high']}, L={bar['low']}, C={bar['close']}, V={bar_volume}")
                            
                            # Temporarily override self.last_ohlc so UI shows the correct 5m bar values
                            self.last_ohlc[mapped_symbol] = {
                                "open": bar["open"],
                                "high": bar["high"],
                                "low": bar["low"],
                                "close": bar["close"],
                                "volume": bar_volume
                            }
                            await self.manager.on_candle(completed_candle)
                            
                        # Start new 5m candle
                        self.current_bars[mapped_symbol] = {
                            "timestamp": bar_timestamp,
                            "open": packet.ltp,
                            "high": packet.ltp,
                            "low": packet.ltp,
                            "close": packet.ltp,
                            "latest_volume": packet.volume
                        }
            
            # Send latest prices & position updates to frontend
            self.broadcast_update(packet)

        self.provider.set_packet_callback(on_provider_tick)
        
        # Pre-load historical 5-minute candles to warm up strategy indicators
        await self._warm_up_strategies(mappings)
        
        self.active = True
        await self.provider.start()
        
        # Dhan takes a brief moment to authenticate.
        await asyncio.sleep(1.0)
        
        # Subscribe to stocks in Quote mode (17) and indices in Ticker mode (15)
        # 1 = NSE_EQ segment for stocks, 0 = IDX_I segment for indices
        stock_instruments = []
        index_instruments = []
        
        # Only subscribe to stocks if user explicitly enables live stock feed (requires paid Dhan Data API)
        enable_live_stocks = config.get("enable_live_stocks", False)
        self.enable_live_stocks = enable_live_stocks
        
        if enable_live_stocks:
            for sym in self.symbols:
                token = mappings.get(sym)
                if token:
                    stock_instruments.append((1, token))
                    logger.info(f"[Live Runner] Mapped {sym} to security token {token}")
                else:
                    logger.warning(f"[Live Runner] Unable to map security ID for stock symbol: {sym}")
        else:
            logger.info("[Live Runner] Live stock subscriptions disabled. Index tracking only. (Set enable_live_stocks: true in config if you have Dhan Data API)")
                
        # Add NIFTY 50 (13) and BANK NIFTY (25) index tokens (always enabled)
        index_instruments.append((0, "13"))
        index_instruments.append((0, "25"))
        
        if stock_instruments:
            await self.provider.subscribe(request_code=17, instruments=stock_instruments)
            logger.info(f"[Live Runner] Subscribed stocks to Quote data (Code=17): {stock_instruments}")
            
        if index_instruments:
            await self.provider.subscribe(request_code=15, instruments=index_instruments)
            logger.info(f"[Live Runner] Subscribed indices to Ticker data (Code=15): {index_instruments}")
            
        # Start watchdog execution thread
        self._watchdog_task = asyncio.create_task(self.watchdog_loop())
        logger.info(f"[Live Runner] Strategy runner and watchdog loop successfully active.")

    def update_strategy_config(self, config: Dict[str, Any]) -> None:
        """Dynamically updates rankings, weights, and strategies in real-time."""
        if "priority_ranking" in config:
            self.priority_ranking = [s.strip().upper() for s in config["priority_ranking"] if s.strip().upper() in self.symbols]
        if "allocation_strategy" in config:
            self.allocation_strategy = str(config["allocation_strategy"]).upper()
        if "allocation_weights" in config:
            self.allocation_weights = [float(w) for w in config["allocation_weights"]]
        if "capital" in config:
            self.capital = float(config["capital"])
        if "leverage" in config:
            self.leverage = float(config["leverage"])
        if "enable_live_stocks" in config:
            self.enable_live_stocks = bool(config["enable_live_stocks"])
            
        if self.manager:
            self.manager.update_allocation_config({
                "allocation_strategy": self.allocation_strategy,
                "priority_ranking": self.priority_ranking,
                "allocation_weights": self.allocation_weights,
                "total_capital": self.capital
            })
            
            for strat in self.strategies.values():
                strat.capital_limit = self.capital
                strat.leverage = self.leverage
                
        # Persist updated parameters back to configs/orb.yaml on disk
        config_path = "configs/orb.yaml"
        if not os.path.exists(config_path) and os.path.exists(os.path.join("..", config_path)):
            config_path = os.path.join("..", config_path)
            
        try:
            current_config = {}
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    current_config = yaml.safe_load(f) or {}
            
            current_config["enable_live_stocks"] = self.enable_live_stocks
            # Keep Excel filename reference if symbols is configured to point to it on disk
            if isinstance(current_config.get("symbols"), str) and current_config["symbols"].endswith(".xlsx"):
                pass
            else:
                current_config["symbols"] = self.symbols
            current_config["priority_ranking"] = self.priority_ranking
            current_config["allocation_strategy"] = self.allocation_strategy
            current_config["allocation_weights"] = self.allocation_weights
            current_config["capital"] = self.capital
            current_config["leverage"] = self.leverage
            
            with open(config_path, "w") as f:
                yaml.safe_dump(current_config, f, default_flow_style=False)
            logger.info(f"[Live Runner] Successfully persisted config to {config_path}")
        except Exception as err:
            logger.error(f"[Live Runner] Failed to persist config to {config_path}: {err}")
            
        dhan_logger.info(f"[Live Runner] Dynamically updated configuration settings.")
        self.broadcast_update()

    async def watchdog_loop(self) -> None:
        """Periodically checks individual symbols data feeds and overall Dhan connection status."""
        while self.active:
            try:
                await asyncio.sleep(5)
                now = datetime.now().timestamp()
                
                # Check for regular NSE market hours (9:15 to 15:30) in IST
                ist_tz = timezone(timedelta(hours=5, minutes=30))
                current_time = datetime.now(ist_tz).strftime("%H:%M")
                is_market_hours = "09:15" <= current_time <= "15:30"
                
                # Verify WebSocket connection health
                if self.provider:
                    prov_status = self.provider.get_status()
                    self.connection_ok = prov_status.get("connected", False)
                else:
                    self.connection_ok = False
                
                for sym in self.symbols:
                    last_time = self.last_tick_times.get(sym)
                    # If tick has not arrived or has expired for > 30 seconds
                    if last_time and (now - last_time > 30.0) and is_market_hours:
                        warning_msg = f"{sym} live data cannot be tracked"
                        if sym not in self.warning_symbols:
                            self.warning_symbols.append(sym)
                            dhan_logger.warning(f"[Watchdog Alert] {warning_msg}. Suspending breakout entries.")
                            
                            # Disable new trade triggers on the strategy
                            strat = self.strategies.get(sym)
                            if strat:
                                strat.is_active = False
                    else:
                        # Clear warning state and resume strategy execution
                        if sym in self.warning_symbols:
                            self.warning_symbols.remove(sym)
                            dhan_logger.info(f"[Watchdog Restore] Resumed data feed for {sym}. Re-activating entries.")
                            strat = self.strategies.get(sym)
                            if strat:
                                strat.is_active = True
                
                self.broadcast_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Live Runner watchdog timer thread: {e}")

    def compile_telemetry_message(self, packet: Optional[MarketPacket] = None) -> Dict[str, Any]:
        """Compiles the complete consolidated strategy telemetry pulse message."""
        portfolio = self.broker.get_portfolio() if self.broker else {}

        # 1. Compile active positions statistics
        active_positions = {}
        if self.strategies:
            for sym, strat in self.strategies.items():
                strat_status = strat.get_status()
                for pos_sym, pos in strat_status.get("positions", {}).items():
                    if pos.get("qty", 0.0) != 0:
                        active_positions[pos_sym] = {
                            "symbol": pos_sym,
                            "qty": pos["qty"],
                            "avg_price": pos["avg_price"],
                            "realized_pnl": pos["realized_pnl"],
                            "unrealized_pnl": pos["unrealized_pnl"],
                            "total_pnl": pos["realized_pnl"] + pos["unrealized_pnl"],
                            "leverage": self.leverage,
                            "capital_utilized": (abs(pos["qty"]) * pos["avg_price"]) / self.leverage
                        }

        # 2. Compile and sort trade history details
        all_trades = []
        if self.strategies:
            for sym, strat in self.strategies.items():
                for t in strat.trade_history:
                    t_copy = dict(t)
                    t_copy["Capital_Utilized"] = (t["Qty"] * t["Entry_Price"]) / self.leverage
                    t_copy["Leverage"] = self.leverage
                    all_trades.append(t_copy)
                    
            all_trades.sort(key=lambda x: x.get("Entry_Time", ""), reverse=True)

        # 3. Aggregate tracking indicators
        symbols_status = {}
        if self.strategies:
            for sym, strat in self.strategies.items():
                ohlc = self.last_ohlc.get(sym, {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0})
                
                # Create a JSON-serializable copy of active_trade if present
                active_detail = None
                if strat.active_trade is not None:
                    active_detail = dict(strat.active_trade)
                    for k, v in active_detail.items():
                        if isinstance(v, datetime):
                            active_detail[k] = v.isoformat()
                            
                symbols_status[sym] = {
                    "symbol": sym,
                    "range_high": strat.curr_day_high,
                    "range_low": strat.curr_day_low,
                    "range_established": strat.opening_range_set,
                    "trade_taken": strat.trade_taken_today,
                    "active_trade": strat.active_trade is not None,
                    "active_trade_detail": active_detail,
                    "is_active": strat.is_active,
                    "warning": sym in self.warning_symbols,
                    "last_ltp": self.broker._last_prices.get(sym, 0.0) if self.broker else 0.0,
                    "offline": not self.enable_live_stocks,
                    "open": ohlc.get("open", 0.0),
                    "high": ohlc.get("high", 0.0),
                    "low": ohlc.get("low", 0.0),
                    "close": ohlc.get("close", 0.0),
                    "volume": ohlc.get("volume", 0)
                }

        # Calculate PnL stats
        total_realized_pnl = sum(strat.total_realized_pnl for strat in self.strategies.values()) if self.strategies else 0.0
        total_unrealized_pnl = sum(sum(pos["unrealized_pnl"] for pos in strat.positions.values()) for strat in self.strategies.values()) if self.strategies else 0.0

        latest_event = None
        if packet:
            latest_event = {
                "event_id": f"event_{datetime.now(timezone.utc).timestamp()}",
                "correlation_id": "live_correlation",
                "symbol": packet.security_id,
                "ltp": packet.ltp,
                "open": packet.open or packet.ltp,
                "high": packet.high or packet.ltp,
                "low": packet.low or packet.ltp,
                "close": packet.close or packet.ltp,
                "volume": packet.volume or 0,
                "exchange_timestamp": packet.timestamp.isoformat() if packet.timestamp else None,
                "received_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "processed_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }

        return {
            "type": "telemetry_pulse",
            "metrics": {
                "packets_per_sec": 1 if packet else 0,
                "events_per_sec": 1 if packet else 0,
                "bronze_buffer_size": 0,
                "silver_buffer_size": 0,
                "avg_parser_time_ms": 0.05,
                "avg_event_bus_time_ms": 0.02,
                "avg_pipeline_time_ms": 0.07,
                "total_packets": 100,
                "total_inserts": 0,
                "last_symbol": packet.security_id if packet else (self.symbols[0] if self.symbols else ""),
                "last_price": packet.ltp if packet else 0.0,
                "last_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "replay_delay_secs": 0.0
            },
            "status": {
                "provider_name": "Dhan Live Feed",
                "status": "RUNNING" if self.active else "STOPPED",
                "speed": 1.0,
                "mode": "LIVE_STRATEGY",
                "packets_processed": 100,
                "last_symbol": packet.security_id if packet else (self.symbols[0] if self.symbols else ""),
                "last_price": packet.ltp if packet else 0.0,
                "last_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "elapsed_time_secs": 10,
                "session_id": "live_session",
                "provider_status": "RUNNING" if (self.active and self.connection_ok) else "DISCONNECTED",
                "connection_ok": self.connection_ok,
                "warning_symbols": self.warning_symbols
            },
            "latest_event": latest_event,
            "strategy_report": {
                "pnl_inr": total_realized_pnl + total_unrealized_pnl,
                "realized_pnl_inr": total_realized_pnl,
                "unrealized_pnl_inr": total_unrealized_pnl,
                "positions": active_positions,
                "cash_inr": portfolio.get("cash_inr", self.capital),
                "net_asset_value_inr": portfolio.get("net_asset_value_inr", self.capital),
                "total_fees_paid_inr": portfolio.get("total_fees_paid_inr", 0.0)
            },
            "symbols_status": symbols_status,
            "trade_history": all_trades,
            "indices": self.indices,
            "configuration": {
                "symbols": self.symbols,
                "priority_ranking": self.priority_ranking,
                "allocation_strategy": self.allocation_strategy,
                "allocation_weights": self.allocation_weights,
                "capital": self.capital,
                "leverage": self.leverage,
                "enable_live_stocks": self.enable_live_stocks
            }
        }

    def broadcast_update(self, packet: Optional[MarketPacket] = None):
        """Sends the latest system statuses, NAV, cash, and log metrics to UI broadcaster."""
        if not self._ui_callback:
            return
        update_msg = self.compile_telemetry_message(packet)
        self._ui_callback(update_msg)

    async def stop(self) -> None:
        """Stops active strategy execution."""
        self.active = False
        
        # Shut down connection watchdog
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
            
        if self.provider:
            logger.info("[Live Runner] Stopping live feed provider...")
            await self.provider.stop()
            self.provider = None
            
        self.manager = None
        self.broker = None
        self.strategies = {}
        self.symbols = []
        self.priority_ranking = []

    async def _warm_up_strategies(self, mappings: Dict[str, str]) -> None:
        """Fetches historical 5-minute candles from Dhan charts API on startup to warm up strategy indicators."""
        access_token = dhan_settings.ACCESS_TOKEN
        if not access_token:
            logger.warning("[Live Runner] ACCESS_TOKEN not found. Skipping strategy warmup.")
            return

        logger.info("[Live Runner] Starting historical indicator warmup from Dhan charts API...")
        
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(ist_tz)
        
        # Fetch last 4 days of data to cover any weekend/holidays for a 10-period SMA
        from_date = (now_ist - timedelta(days=4)).date()
        to_date = now_ist.date()
        
        # 1. Warm up Nifty 50 Index first
        nifty_candles = []
        try:
            payload = {
                "securityId": "13",
                "exchangeSegment": "IDX_I",
                "instrument": "INDEX",
                "expiryCode": 0,
                "oi": False,
                "interval": "5",
                "fromDate": from_date.strftime("%Y-%m-%d"),
                "toDate": to_date.strftime("%Y-%m-%d")
            }
            url = "https://api.dhan.co/v2/charts/intraday"
            headers = {
                "Content-Type": "application/json",
                "access-token": access_token
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            loop = asyncio.get_running_loop()
            def fetch():
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))
            
            res_data = await loop.run_in_executor(None, fetch)
            if "timestamp" in res_data:
                times = res_data["timestamp"]
                opens = res_data["open"]
                highs = res_data["high"]
                lows = res_data["low"]
                closes = res_data["close"]
                volumes = res_data.get("volume", [0] * len(times))
                for i in range(len(times)):
                    dt = datetime.fromtimestamp(times[i], tz=timezone.utc).astimezone(ist_tz).replace(tzinfo=None)
                    if dt < now_ist.replace(tzinfo=None):
                        nifty_candles.append({
                            "timestamp": dt,
                            "open": opens[i],
                            "high": highs[i],
                            "low": lows[i],
                            "close": closes[i],
                            "volume": int(volumes[i])
                        })
                nifty_candles.sort(key=lambda x: x["timestamp"])
                logger.info(f"[Live Runner] Loaded {len(nifty_candles)} historical candles for NIFTY_50 index warmup.")
        except Exception as e:
            logger.error(f"[Live Runner] Error downloading historical NIFTY_50 index data for warmup: {e}")

        # 2. Warm up stock strategies
        stock_candles_by_symbol = {}
        for sym in self.symbols:
            security_id = mappings.get(sym)
            if not security_id:
                logger.warning(f"[Live Runner] Warmup skipped: security ID mapping not found for symbol {sym}")
                continue
                
            logger.info(f"[Live Runner] Downloading historical 5m candles for {sym}...")
            try:
                payload = {
                    "securityId": security_id,
                    "exchangeSegment": "NSE_EQ",
                    "instrument": "EQUITY",
                    "expiryCode": 0,
                    "oi": False,
                    "interval": "5",
                    "fromDate": from_date.strftime("%Y-%m-%d"),
                    "toDate": to_date.strftime("%Y-%m-%d")
                }
                url = "https://api.dhan.co/v2/charts/intraday"
                headers = {
                    "Content-Type": "application/json",
                    "access-token": access_token
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                loop = asyncio.get_running_loop()
                def fetch_stock():
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode("utf-8"))
                
                res_data = await loop.run_in_executor(None, fetch_stock)
                candles = []
                if "timestamp" in res_data:
                    times = res_data["timestamp"]
                    opens = res_data["open"]
                    highs = res_data["high"]
                    lows = res_data["low"]
                    closes = res_data["close"]
                    volumes = res_data["volume"]
                    for i in range(len(times)):
                        dt = datetime.fromtimestamp(times[i], tz=timezone.utc).astimezone(ist_tz).replace(tzinfo=None)
                        if dt < now_ist.replace(tzinfo=None):
                            candles.append({
                                "timestamp": dt,
                                "open": opens[i],
                                "high": highs[i],
                                "low": lows[i],
                                "close": closes[i],
                                "volume": int(volumes[i])
                            })
                    candles.sort(key=lambda x: x["timestamp"])
                    stock_candles_by_symbol[sym] = candles
                    logger.info(f"[Live Runner] Loaded {len(candles)} historical candles for {sym}.")
                await asyncio.sleep(0.1)  # Rate limiting safety margin
            except Exception as e:
                logger.error(f"[Live Runner] Error downloading historical stock data for warmup for {sym}: {e}")

        # 3. Merge and feed all candles chronologically to the Strategy Manager
        timeline = []
        for c in nifty_candles:
            timeline.append(("13", c))
        for sym, candles in stock_candles_by_symbol.items():
            for c in candles:
                timeline.append((sym, c))
                
        timeline.sort(key=lambda x: (x[1]["timestamp"], x[0]))
        
        logger.info(f"[Live Runner] Replaying {len(timeline)} historical ticks chronologically for strategy state warmup...")
        if self.manager:
            self.manager.is_warming_up = True
        
        import logging
        orb_logger = logging.getLogger("orb")
        prev_level = orb_logger.level
        orb_logger.setLevel(logging.WARNING)
        
        for identifier, c in timeline:
            if identifier == "13":
                open_val = c["open"]
                ltp_val = c["close"]
                pct = ((ltp_val - open_val) / open_val * 100.0) if open_val > 0 else 0.0
                trend = "BULLISH" if pct > 0.05 else "BEARISH" if pct < -0.05 else "NEUTRAL"
                self.indices["NIFTY_50"] = {
                    "ltp": ltp_val,
                    "open": open_val,
                    "change_pct": pct,
                    "trend": trend
                }
                if self.manager:
                    self.manager.indices["NIFTY_50"] = {
                        "ltp": ltp_val,
                        "open": open_val,
                        "trend": trend
                    }
            else:
                packet = MarketPacket(
                    packet_type="Quote",
                    exchange_segment="NSE_EQ",
                    security_id=identifier,
                    timestamp=c["timestamp"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c["volume"],
                    ltp=c["close"]
                )
                if self.manager:
                    self.last_tick_times[identifier] = datetime.now().timestamp()
                    self.last_ohlc[identifier] = {
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"]
                    }
                    await self.manager.on_candle(packet)
                    
        if self.manager:
            self.manager.is_warming_up = False
        orb_logger.setLevel(prev_level)
        logger.info("[Live Runner] Strategy state warmup completed successfully.")

live_runner = LiveTradingRunner()

