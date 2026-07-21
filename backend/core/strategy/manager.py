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
        
        # Allocation Settings
        self.allocation_strategy = "SINGLE_STOCK"  # "SINGLE_STOCK" or "PERCENTAGE_RANKED"
        self.priority_ranking: List[str] = []
        self.allocation_weights: List[float] = [0.50, 0.30, 0.20]
        self.total_capital = 100000.0
        
        self.indices: Dict[str, Dict[str, Any]] = {
            "NIFTY_50": {"ltp": 0.0, "open": 0.0},
            "BANK_NIFTY": {"ltp": 0.0, "open": 0.0}
        }
        
        self.is_warming_up = False
        
        # Register broker fill updates back to this manager
        self.broker.register_fill_callback(self._handle_broker_fill)

    def update_allocation_config(self, config: Dict[str, Any]) -> None:
        """Dynamically updates the allocation policy and priority rankings from front-end controls."""
        if "allocation_strategy" in config:
            self.allocation_strategy = str(config["allocation_strategy"]).upper()
        if "priority_ranking" in config:
            self.priority_ranking = list(config["priority_ranking"])
        if "allocation_weights" in config:
            self.allocation_weights = [float(w) for w in config["allocation_weights"]]
        if "total_capital" in config:
            self.total_capital = float(config["total_capital"])
            
        dhan_logger.info(
            f"[Strategy Manager] Allocation config updated: Strategy={self.allocation_strategy}, "
            f"Rankings={self.priority_ranking}, Weights={self.allocation_weights}, TotalCapital=₹{self.total_capital:,.2f}"
        )

    def get_allocated_capital(self, symbol: str) -> float:
        """Returns the dynamic capital limit in INR for a given stock based on ranking rules."""
        if self.allocation_strategy == "SINGLE_STOCK":
            # Check if any OTHER strategy has an active trade open
            for strat in self.strategies.values():
                if strat.symbol != symbol and strat.active_trade is not None:
                    return 0.0
            return self.total_capital
            
        elif self.allocation_strategy == "PERCENTAGE_RANKED":
            if symbol not in self.priority_ranking:
                return 0.0
            try:
                rank_idx = self.priority_ranking.index(symbol)
                if rank_idx < len(self.allocation_weights):
                    return self.total_capital * self.allocation_weights[rank_idx]
            except ValueError:
                pass
            return 0.0
            
        return 0.0

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
                # Dispatch raw tick to strategy ONLY for active trade exits (SL/TP)
                if strategy.active_trade is not None:
                    try:
                        await strategy.on_tick(packet)
                    except Exception as e:
                        dhan_logger.error(f"Error executing on_tick for strategy {strategy.name}: {e}")

    async def on_candle(self, candle: MarketPacket) -> None:
        """Dispatches completed 5-minute candles to strategies for breakout entry decisions."""
        symbol = candle.security_id
        for strategy in self.strategies.values():
            if symbol in strategy.symbols:
                if strategy.active_trade is None:
                    try:
                        await strategy.on_tick(candle)
                    except Exception as e:
                        dhan_logger.error(f"Error executing on_candle for strategy {strategy.name}: {e}")

    async def process_order(self, order_request: Dict[str, Any]) -> None:
        """Validates order via RiskController. If passed, forwards to the Broker."""
        if self.is_warming_up:
            dhan_logger.debug(f"[Strategy Manager] Warmup active. Ignoring order request for {order_request.get('symbol')}.")
            return
            
        strategy_id = order_request["strategy_id"]
        strategy = self.strategies.get(strategy_id)
        if not strategy:
            raise ValueError(f"Unknown strategy ID: {strategy_id}")

        symbol = order_request["symbol"]
        side = order_request["side"].upper()
        portfolio = self.broker.get_portfolio()
        
        # Determine if this is an opening position order (reductions/exits are always allowed)
        current_pos_qty = portfolio.get("positions", {}).get(symbol, {}).get("qty", 0.0)
        is_opening = True
        if side == "SELL" and current_pos_qty > 0:
            is_opening = False
        elif side == "BUY" and current_pos_qty < 0:
            is_opening = False

        if is_opening:
            # 1. Verify dynamic capital allocation limit
            allocated = self.get_allocated_capital(symbol)
            if allocated <= 0:
                raise ValueError(f"Allocation block: Symbol {symbol} has no capital allocation under current priority configuration.")
                
            # 2. Verify Single Stock constraints
            if self.allocation_strategy == "SINGLE_STOCK":
                for strat in self.strategies.values():
                    if strat.symbol != symbol and strat.active_trade is not None:
                        raise ValueError(f"Allocation block: Single Stock rule active. Already in trade for {strat.symbol}.")

        try:
            # 3. Pre-trade Risk Check Gate
            self.risk_controller.validate_order(order_request, portfolio)
            
            # 4. Submit to Broker
            order_id = await self.broker.submit_order(order_request)
            dhan_logger.info(f"Order processed: Strategy {strategy.name} submitted order {order_id}")
            
        except ValueError as risk_error:
            dhan_logger.warning(f"Order Rejected: Risk/Allocation validation failed: {risk_error}")
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
