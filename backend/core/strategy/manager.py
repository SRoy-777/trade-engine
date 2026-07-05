import asyncio
from typing import Dict, Any, List, Optional
from core.strategy.base import BaseStrategy
from core.broker.base import BaseBroker
from core.risk.controller import RiskController
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class StrategyManager:
    """Central coordinator managing multiple parallel strategies, risk gates, and paper brokerage."""

    def __init__(self, broker: BaseBroker, risk_controller: RiskController):
        self.broker = broker
        self.risk_controller = risk_controller
        
        # Registered strategies: strategy_id -> Strategy instance
        self.strategies: Dict[str, BaseStrategy] = {}
        
        # Register broker fill updates back to this manager
        self.broker.register_fill_callback(self._handle_broker_fill)

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Registers a new trading strategy to the manager."""
        strategy.set_manager(self)
        self.strategies[strategy.strategy_id] = strategy
        dhan_logger.info(f"Registered strategy: {strategy.name} (ID: {strategy.strategy_id})")

    async def on_tick(self, packet: MarketPacket) -> None:
        """Dispatches incoming ticks to the broker first (for fills) and then to matching strategies."""
        # 1. Update prices and match orders on paper broker
        await self.broker.on_tick(packet)

        # 2. Update unrealized profit valuations in registered strategies
        symbol = packet.security_id
        price = packet.ltp
        
        for strategy in self.strategies.values():
            if symbol in strategy.symbols:
                strategy.update_unrealized_pnl(symbol, price)
                # Dispatch tick to strategy for logical decision making
                try:
                    await strategy.on_tick(packet)
                except Exception as e:
                    dhan_logger.error(f"Error executing on_tick for strategy {strategy.name}: {e}")

    async def process_order(self, order_request: Dict[str, Any]) -> None:
        """Validates order via RiskController. If passed, forwards to the Broker."""
        strategy_id = order_request["strategy_id"]
        strategy = self.strategies.get(strategy_id)
        if not strategy:
            raise ValueError(f"Unknown strategy ID: {strategy_id}")

        # Fetch current broker portfolio statistics for margin calculations
        portfolio = self.broker.get_portfolio()

        try:
            # 1. Pre-trade Risk Check Gate
            self.risk_controller.validate_order(order_request, portfolio)
            
            # 2. Submit to Broker
            order_id = await self.broker.submit_order(order_request)
            dhan_logger.info(f"Order processed: Strategy {strategy.name} submitted order {order_id}")
            
        except ValueError as risk_error:
            dhan_logger.warning(f"Order Rejected: Risk validation failed: {risk_error}")
            # Raise exception up to strategy to handle rejection
            raise risk_error

    async def _handle_broker_fill(self, fill_event: Dict[str, Any]) -> None:
        """Coordinates fill events, updating strategy portfolios and risk metrics."""
        strategy_id = fill_event["strategy_id"]
        strategy = self.strategies.get(strategy_id)
        if not strategy:
            dhan_logger.error(f"Received fill event for unregistered strategy: {strategy_id}")
            return

        symbol = fill_event["symbol"]
        side = fill_event["side"]
        qty = fill_event["qty"]
        price = fill_event["price"]
        order_id = fill_event["order_id"]

        # 1. Update strategy position portfolio
        strategy.apply_fill(symbol, side, qty, price)

        # 2. Sync running strategy P&L back to the Risk Controller daily cap limit
        self.risk_controller.update_strategy_pnl(strategy_id, strategy.total_realized_pnl)

        # 3. Notify Strategy Callback of successful execution
        try:
            await strategy.on_order_fill(order_id, symbol, side, qty, price)
        except Exception as e:
            dhan_logger.error(f"Error handling on_order_fill for strategy {strategy.name}: {e}")

    def get_all_strategy_status(self) -> List[Dict[str, Any]]:
        """Returns status lists for all active strategies."""
        return [strat.get_status() for strat in self.strategies.values()]
