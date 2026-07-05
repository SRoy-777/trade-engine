import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Callable, Optional
from core.broker.paper import PaperBroker
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from providers.market.dhan.market_provider import DhanMarketProvider
from providers.market.dhan.models import MarketPacket
from utils.logger_setup import logger

class LiveTradingRunner:
    """Manages active live strategy execution, paper broker, and UI updates."""

    def __init__(self):
        self.provider: Optional[DhanMarketProvider] = None
        self.manager: Optional[StrategyManager] = None
        self.broker: Optional[PaperBroker] = None
        self.strategy: Optional[Any] = None
        self.active = False
        self.active_symbol = ""
        self._ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    async def start(self, config: Dict[str, Any], ui_callback: Callable[[Dict[str, Any]], None]) -> None:
        """Starts a live strategy execution run with user-defined parameters."""
        await self.stop()

        symbol = config.get("symbol", "TATAMOTORS").upper().strip()
        capital = float(config.get("capital", 60000.0))
        target_profit = float(config.get("target_profit", 500.0))
        ticks_target = int(config.get("ticks_target", 100))

        logger.info(f"[Live Runner] Initializing live HFT strategy on {symbol}. Capital: Rs.{capital}, Profit target: Rs.{target_profit}")

        # 1. Initialize broker & risk
        self.broker = PaperBroker(initial_cash_inr=capital, latency_ms=50.0)
        risk = RiskController(
            max_capital_per_trade_inr=capital * 4.0,  # 4x capital limit
            max_daily_loss_inr=capital * 0.1,         # 10% daily loss limit
            margin_leverage_multiplier=5.0
        )
        self.manager = StrategyManager(self.broker, risk)

        # 2. No strategy registered (cleanup complete)
        self.strategy = None
        self.active_symbol = symbol
        self._ui_callback = ui_callback

        # 3. Connect broker fills to notify the UI instantly
        async def on_broker_fill(fill_event: Dict[str, Any]):
            # Notify UI of execution fill logs
            self.broadcast_update()

        self.broker.register_fill_callback(on_broker_fill)

        # 4. Initialize Dhan Market Feed
        self.provider = DhanMarketProvider()
        
        async def on_provider_tick(packet: MarketPacket):
            if not self.active:
                return
            
            # Feed packet to strategy manager
            await self.manager.on_tick(packet)
            
            # Send latest prices & position updates to frontend
            self.broadcast_update(packet)

        self.provider.set_packet_callback(on_provider_tick)
        
        self.active = True
        await self.provider.start()
        logger.info(f"[Live Runner] Live feed connected. Subscribed to: {symbol}")

    def broadcast_update(self, packet: Optional[MarketPacket] = None):
        """Sends the latest system statuses, NAV, cash, and log metrics to UI broadcaster."""
        if not self._ui_callback:
            return

        portfolio = self.broker.get_portfolio() if self.broker else {}
        status = self.strategy.get_status() if self.strategy else {}

        latest_event = None
        if packet:
            latest_event = {
                "event_id": f"event_{datetime.now(timezone.utc).timestamp()}",
                "correlation_id": "live_correlation",
                "symbol": packet.security_id,
                "ltp": packet.ltp,
                "open": packet.ltp,
                "high": packet.ltp,
                "low": packet.ltp,
                "close": packet.ltp,
                "volume": packet.volume or 0,
                "exchange_timestamp": packet.timestamp.isoformat() if packet.timestamp else None,
                "received_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "processed_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }

        update_msg = {
            "type": "telemetry_pulse",
            "metrics": {
                "packets_per_sec": 1 if packet else 0,
                "events_per_sec": 1 if packet else 0,
                "bronze_buffer_size": 0,
                "silver_buffer_size": 0,
                "avg_parser_time_ms": 0.05,
                "avg_event_bus_time_ms": 0.02,
                "avg_pipeline_time_ms": 0.07,
                "total_packets": 100, # Mock total ticks
                "total_inserts": 0,
                "last_symbol": self.active_symbol,
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
                "last_symbol": self.active_symbol,
                "last_price": packet.ltp if packet else 0.0,
                "last_timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "elapsed_time_secs": 10,
                "session_id": "live_session",
                "provider_status": "RUNNING" if self.active else "STOPPED"
            },
            "latest_event": latest_event,
            "strategy_report": {
                "pnl_inr": status.get("total_pnl_inr", 0.0),
                "realized_pnl_inr": status.get("realized_pnl_inr", 0.0),
                "unrealized_pnl_inr": status.get("unrealized_pnl_inr", 0.0),
                "positions": status.get("positions", {}),
                "cash_inr": portfolio.get("cash_inr", 0.0),
                "net_asset_value_inr": portfolio.get("net_asset_value_inr", 0.0),
                "total_fees_paid_inr": portfolio.get("total_fees_paid_inr", 0.0)
            }
        }
        self._ui_callback(update_msg)

    async def stop(self) -> None:
        """Stops active strategy execution."""
        self.active = False
        if self.provider:
            logger.info("[Live Runner] Stopping live feed provider...")
            await self.provider.stop()
            self.provider = None
        self.manager = None
        self.broker = None
        self.strategy = None
        self.active_symbol = ""

live_runner = LiveTradingRunner()
