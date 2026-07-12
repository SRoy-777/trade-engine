import os
import csv
import yaml
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

    def _load_symbol_mappings(self) -> Dict[str, str]:
        """Loads stock symbol to security ID token mapping from Nifty CSV files."""
        mappings = {}
        for base_dir in [".", ".."]:
            nifty50_path = os.path.join(base_dir, "market_data", "nifty50_security_ids.csv")
            niftynext50_path = os.path.join(base_dir, "market_data", "niftynext50_security_ids.csv")
            
            loaded = False
            for path in [nifty50_path, niftynext50_path]:
                if os.path.exists(path):
                    try:
                        with open(path, mode="r", encoding="utf-8") as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                symbol = row["symbol"].strip().upper()
                                sec_id = row["security_id"].strip()
                                mappings[symbol] = sec_id
                        loaded = True
                    except Exception as e:
                        logger.error(f"Error reading mapping file {path}: {e}")
            if loaded:
                logger.info(f"Successfully loaded {len(mappings)} symbol mappings from {base_dir}/market_data")
                break
        return mappings

    async def start(self, config: Dict[str, Any], ui_callback: Callable[[Dict[str, Any]], None]) -> None:
        """Starts a live multi-symbol strategy execution run with user-defined parameters."""
        await self.stop()

        self._ui_callback = ui_callback
        
        # Load mappings first to avoid scope resolution bugs on fast ticks
        mappings = self._load_symbol_mappings()
        
        # 1. Parse and extract configuration parameters
        self.symbols = [s.strip().upper() for s in config.get("symbols", ["SBIN", "BAJFINANCE", "INFY"])]
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

        # 4. Connect broker fills to notify the UI instantly
        async def on_broker_fill(fill_event: Dict[str, Any]):
            self.broadcast_update()

        self.broker.register_fill_callback(on_broker_fill)

        # 5. Initialize Dhan Market Feed
        self.provider = DhanMarketProvider()
        
        async def on_provider_tick(packet: MarketPacket):
            if not self.active:
                return
            
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
            
            # Feed packet to strategy manager
            if self.manager:
                await self.manager.on_tick(packet)
            
            # Send latest prices & position updates to frontend
            self.broadcast_update(packet)

        self.provider.set_packet_callback(on_provider_tick)
        
        self.active = True
        await self.provider.start()
        
        # Dhan takes a brief moment to authenticate.
        await asyncio.sleep(1.0)
        
        # Subscribe to all stocks and indices using Ticker mode (RequestCode 15)
        # 1 = NSE_EQ segment for stocks, 0 = IDX_I segment for indices
        instruments = []
        
        # Only subscribe to stocks if user explicitly enables live stock feed (requires paid Dhan Data API)
        enable_live_stocks = config.get("enable_live_stocks", False)
        self.enable_live_stocks = enable_live_stocks
        
        if enable_live_stocks:
            for sym in self.symbols:
                token = mappings.get(sym)
                if token:
                    instruments.append((1, token))
                    logger.info(f"[Live Runner] Mapped {sym} to security token {token}")
                else:
                    logger.warning(f"[Live Runner] Unable to map security ID for stock symbol: {sym}")
        else:
            logger.info("[Live Runner] Live stock subscriptions disabled. Index tracking only. (Set enable_live_stocks: true in config if you have Dhan Data API)")
                
        # Add NIFTY 50 (13) and BANK NIFTY (25) index tokens (always enabled)
        instruments.append((0, "13"))
        instruments.append((0, "25"))
        
        if instruments:
            await self.provider.subscribe(request_code=15, instruments=instruments)
            logger.info(f"[Live Runner] Subscribed all instruments to Ticker data: {instruments}")
            
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
                symbols_status[sym] = {
                    "symbol": sym,
                    "range_high": strat.curr_day_high,
                    "range_low": strat.curr_day_low,
                    "range_established": strat.opening_range_set,
                    "trade_taken": strat.trade_taken_today,
                    "active_trade": strat.active_trade is not None,
                    "active_trade_detail": strat.active_trade,
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

live_runner = LiveTradingRunner()

