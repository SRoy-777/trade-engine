from typing import Dict, Any, Optional
from providers.market.dhan.logger import dhan_logger

class RiskController:
    """Pre-trade risk validator checking margins, drawdowns, and limits before execution."""

    def __init__(self, 
                 max_capital_per_trade_inr: float = 10000000.0, # Default: 1 Crore INR limit per order
                 max_daily_loss_inr: float = 50000.0,          # Default: ₹50,000 max daily loss
                 margin_leverage_multiplier: float = 5.0):     # Default: 5x intraday paper margin
        self.max_capital_per_trade_inr = max_capital_per_trade_inr
        self.max_daily_loss_inr = max_daily_loss_inr
        self.margin_leverage = margin_leverage_multiplier
        
        # Keep track of daily realized P&L per strategy to enforce daily loss limits
        # strategy_id -> daily_realized_pnl
        self._daily_strategy_pnl: Dict[str, float] = {}

    def update_strategy_pnl(self, strategy_id: str, pnl: float) -> None:
        """Updates the running P&L tracker for a strategy to enforce daily caps."""
        self._daily_strategy_pnl[strategy_id] = pnl

    def validate_order(self, order_request: Dict[str, Any], broker_portfolio: Dict[str, Any]) -> bool:
        """Checks pre-trade risk parameters. Raises ValueError on failure."""
        strategy_id = order_request["strategy_id"]
        symbol = order_request["symbol"]
        side = order_request["side"]
        qty = order_request["qty"]
        price = order_request["price"]
        order_type = order_request["order_type"]
        
        dhan_logger.info(f"[Risk Check] Inspecting {side} order for {qty} shares of {symbol} (Strategy={strategy_id})")

        # 1. Enforce Daily Loss Limit
        current_strategy_pnl = self._daily_strategy_pnl.get(strategy_id, 0.0)
        if current_strategy_pnl <= -self.max_daily_loss_inr:
            dhan_logger.warning(f"[Risk Block] Strategy {strategy_id} has exceeded daily loss cap (₹{current_strategy_pnl:.2f} <= -₹{self.max_daily_loss_inr:.2f}). Order blocked.")
            raise ValueError(f"Strategy daily loss limit exceeded: P&L is ₹{current_strategy_pnl:.2f}")

        # 2. Estimate Price for Market Order if Price is not specified
        execution_price = price
        if not execution_price or execution_price <= 0:
            # Fall back to last traded price from broker portfolio or assume a baseline
            execution_price = broker_portfolio.get("last_prices", {}).get(symbol, 1.0)

        # 3. Calculate Trade Value in INR
        order_value = execution_price * qty
        
        # 4. Enforce Single-Trade Capital Exposure Limit
        if order_value > self.max_capital_per_trade_inr:
            dhan_logger.warning(f"[Risk Block] Order size ₹{order_value:,.2f} exceeds max trade exposure cap of ₹{self.max_capital_per_trade_inr:,.2f}")
            raise ValueError(f"Order value ₹{order_value:.2f} exceeds single trade limit of ₹{self.max_capital_per_trade_inr:.2f}")

        # 5. Cash & Margin Leverage Verification (only for buys or short sells)
        available_cash = broker_portfolio.get("cash_inr", 0.0)
        buying_power = available_cash * self.margin_leverage
        
        # Note: In paper trading, we assume simple margin calculations
        # Selling an existing long position reduces exposure, so it does not draw buying power
        current_position = broker_portfolio.get("positions", {}).get(symbol, {}).get("qty", 0.0)
        
        is_opening_trade = True
        if side == "SELL" and current_position > 0:
            is_opening_trade = False  # Liquidation/reducing position
        elif side == "BUY" and current_position < 0:
            is_opening_trade = False  # Cover short position
            
        if is_opening_trade:
            required_margin = order_value # Intraday leverage applied below
            if required_margin > buying_power:
                dhan_logger.warning(f"[Risk Block] Insufficient cash for order. Cash: ₹{available_cash:,.2f}, Required margin with {self.margin_leverage}x leverage: ₹{required_margin/self.margin_leverage:,.2f}")
                raise ValueError(f"Insufficient funds. Cash: ₹{available_cash:.2f}. Required margin: ₹{required_margin/self.margin_leverage:.2f}")

        dhan_logger.info(f"[Risk Passed] Order for {qty} shares of {symbol} validated successfully")
        return True
