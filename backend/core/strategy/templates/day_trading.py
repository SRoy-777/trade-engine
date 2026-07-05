from typing import List, Optional
from core.strategy.base import BaseStrategy
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class DayTradingTrendFollower(BaseStrategy):
    """Traditional Day Trading strategy using a simple moving average crossover trigger."""

    def __init__(self, 
                 strategy_id: str, 
                 name: str, 
                 symbols: List[str], 
                 sma_period: int = 5,
                 capital_limit: float = 500000.0): # Default: ₹5 Lakh capital limit
        super().__init__(strategy_id, name, symbols, capital_limit)
        
        self.sma_period = sma_period
        # Keep track of rolling close prices to compute SMA
        self.price_history: List[float] = []
        
        self.state = "OUT" # OUT, BUYING, IN_LONG, SELLING
        self.position_qty = 0
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

    async def on_tick(self, packet: MarketPacket) -> None:
        symbol = packet.security_id
        ltp = packet.ltp

        if symbol not in self.symbols:
            return

        # 1. Update rolling history (simulate candle close closes by storing last prices)
        # In a full engine, this is computed at the close of 1-minute or 5-minute candles.
        # For this paper-trading demo, we treat the last N ticks as our historical dataset.
        self.price_history.append(ltp)
        if len(self.price_history) > self.sma_period:
            self.price_history.pop(0)

        # We need enough data points to compute SMA
        if len(self.price_history) < self.sma_period:
            return

        # Compute SMA
        sma = sum(self.price_history) / self.sma_period

        # 2. Logic Execution
        if self.state == "OUT":
            # BUY trigger: price crosses above SMA
            if ltp > sma * 1.001:  # 0.1% buffer to avoid noise whipsaws
                # Standard risk calculation: Risk 1% of capital, with Stop Loss at 1.5% below entry
                risk_amount = self.capital_limit * 0.01
                sl_distance = ltp * 0.015
                calculated_qty = int(risk_amount / sl_distance)
                
                # Cap quantity by capital constraints
                max_qty_by_capital = int(self.capital_limit / ltp)
                self.position_qty = min(calculated_qty, max_qty_by_capital)

                if self.position_qty <= 0:
                    return

                dhan_logger.info(f"[Day Trader] BUY Trigger: LTP: ₹{ltp:.2f} > SMA({self.sma_period}): ₹{sma:.2f}. Qty: {self.position_qty}")
                self.state = "BUYING"
                try:
                    await self.submit_order(
                        symbol=symbol,
                        side="BUY",
                        qty=self.position_qty,
                        order_type="MARKET"
                    )
                except Exception as e:
                    dhan_logger.error(f"[Day Trader] Buy order failed: {e}")
                    self.state = "OUT"

        elif self.state == "IN_LONG":
            # Exit triggers: Price drops below stop loss, hits take profit, or crosses below SMA
            is_sl_hit = ltp <= self.stop_loss
            is_tp_hit = ltp >= self.take_profit
            is_trend_reversed = ltp < sma * 0.999 # 0.1% buffer

            if is_sl_hit or is_tp_hit or is_trend_reversed:
                reason = "Stop Loss" if is_sl_hit else ("Take Profit" if is_tp_hit else "Trend Reversal")
                dhan_logger.info(f"[Day Trader] SELL Trigger ({reason}). Price: ₹{ltp:.2f}. SMA: ₹{sma:.2f}")
                self.state = "SELLING"
                try:
                    await self.submit_order(
                        symbol=symbol,
                        side="SELL",
                        qty=self.position_qty,
                        order_type="MARKET"
                    )
                except Exception as e:
                    dhan_logger.error(f"[Day Trader] Sell order failed: {e}")
                    self.state = "IN_LONG"

    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        if side == "BUY":
            self.entry_price = price
            self.stop_loss = price * 0.985 # 1.5% Stop Loss
            self.take_profit = price * 1.03 # 3.0% Take Profit (1:2 Risk-Reward)
            self.state = "IN_LONG"
            dhan_logger.info(f"[Day Trader] Position opened at ₹{price:.2f}. SL: ₹{self.stop_loss:.2f}, TP: ₹{self.take_profit:.2f}")
        elif side == "SELL":
            self.state = "OUT"
            self.entry_price = 0.0
            self.stop_loss = 0.0
            self.take_profit = 0.0
            dhan_logger.info(f"[Day Trader] Position closed at ₹{price:.2f}. Total Strategy P&L: ₹{self.total_realized_pnl:.2f}")
