import asyncio
from typing import List, Optional
from core.strategy.base import BaseStrategy
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class HftMicroScalper(BaseStrategy):
    """HFT micro-scalper targeting absolute INR profit goals across quick tick intervals."""

    def __init__(self, 
                 strategy_id: str, 
                 name: str, 
                 symbols: List[str], 
                 target_profit_inr: float = 1000.0,
                 ticks_target: int = 2,
                 capital_limit: float = 20000000.0): # Default: ₹2 Crore capital cap for large scalping margins
        super().__init__(strategy_id, name, symbols, capital_limit)
        
        self.target_profit = target_profit_inr
        self.ticks_target = ticks_target
        self.tick_size = 0.05 # Standard tick size in India (NSE/BSE) is ₹0.05
        
        # Calculate target price change
        self.target_price_change = self.ticks_target * self.tick_size # e.g. 2 * 0.05 = 0.10
        self.stop_loss_price_change = 3 * self.target_price_change # e.g. 6 ticks = 0.30
        
        # Calculate required position size (shares)
        # Position Size = target_profit / price_change
        self.position_size = int(self.target_profit / self.target_price_change)
        
        # Order/State tracking
        self.active_order_id: Optional[str] = None
        self.entry_price: float = 0.0
        self.tp_price: float = 0.0
        self.sl_price: float = 0.0
        self.state = "IDLE" # IDLE, BUYING, IN_POSITION, SELLING
        
        dhan_logger.info(
            f"[HFT Scalper] Initialized {self.name}. "
            f"Target: ₹{self.target_profit} in {self.ticks_target} ticks. "
            f"Calculated Position Size: {self.position_size} shares. "
            f"Stop Loss: {self.ticks_target * 3} ticks (₹{self.position_size * self.stop_loss_price_change:.2f} risk)."
        )

    async def on_tick(self, packet: MarketPacket) -> None:
        symbol = packet.security_id
        ltp = packet.ltp

        # Ensure we only track symbols bound to this strategy
        if symbol not in self.symbols:
            return

        # 1. If IDLE, initiate entry trigger
        if self.state == "IDLE":
            # For demonstration, we enter immediately on the first available tick
            dhan_logger.info(f"[HFT Scalper] Triggering entry BUY. Price: ₹{ltp:.2f}. Size: {self.position_size}")
            self.state = "BUYING"
            try:
                await self.submit_order(
                    symbol=symbol,
                    side="BUY",
                    qty=self.position_size,
                    order_type="MARKET"
                )
            except Exception as e:
                dhan_logger.error(f"[HFT Scalper] Entry order submission failed: {e}")
                self.state = "IDLE"
            return

        # 2. If in position, monitor prices for TP or SL levels
        if self.state == "IN_POSITION":
            # Check Take Profit
            if ltp >= self.tp_price:
                dhan_logger.info(
                    f"[HFT Scalper] Target Profit hit! LTP: ₹{ltp:.2f} >= TP: ₹{self.tp_price:.2f}. "
                    f"Exiting position for ₹{self.target_profit:.2f} profit."
                )
                self.state = "SELLING"
                await self.submit_order(
                    symbol=symbol,
                    side="SELL",
                    qty=self.position_size,
                    order_type="MARKET"
                )
            # Check Stop Loss
            elif ltp <= self.sl_price:
                dhan_logger.warning(
                    f"[HFT Scalper] Stop Loss hit! LTP: ₹{ltp:.2f} <= SL: ₹{self.sl_price:.2f}. "
                    f"Exiting position to prevent drawdown (expected loss: -₹{self.target_profit * 3:.2f})."
                )
                self.state = "SELLING"
                await self.submit_order(
                    symbol=symbol,
                    side="SELL",
                    qty=self.position_size,
                    order_type="MARKET"
                )

    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        dhan_logger.info(f"[HFT Scalper] Fill update: {side} {qty} shares of {symbol} at ₹{price:.2f}")
        
        if side == "BUY":
            # We bought. Define targets
            self.entry_price = price
            self.tp_price = price + self.target_price_change
            self.sl_price = price - self.stop_loss_price_change
            self.state = "IN_POSITION"
            dhan_logger.info(
                f"[HFT Scalper] Entered long position. Entry: ₹{price:.2f}, "
                f"Take Profit target: ₹{self.tp_price:.2f}, "
                f"Stop Loss exit: ₹{self.sl_price:.2f}"
            )
        elif side == "SELL":
            # Position closed
            self.state = "IDLE"
            self.entry_price = 0.0
            self.tp_price = 0.0
            self.sl_price = 0.0
            dhan_logger.info(f"[HFT Scalper] Scalp cycle closed. Total Strategy P&L: ₹{self.total_realized_pnl:.2f}")
