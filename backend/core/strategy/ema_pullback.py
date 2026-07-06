import yaml
import os
from datetime import datetime, time
from typing import List, Dict, Any, Optional
from core.strategy.base import BaseStrategy
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class EMAPullbackStrategy(BaseStrategy):
    """EMA Pullback Trend Strategy implementing 1-minute trend pullbacks to EMA100 and EMA200."""

    def __init__(self, config_path: str):
        # Resolve config path relative to workspace or absolute
        if not os.path.isabs(config_path):
            config_path = os.path.abspath(config_path)
            
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        strategy_id = "ema_pullback"
        name = "EMA Pullback Trend Strategy"
        self.symbol = self.config.get("symbol", "SBIN")
        capital = float(self.config.get("capital", 60000.0))

        super().__init__(strategy_id=strategy_id, name=name, symbols=[self.symbol], capital_limit=capital)

        # Load parameters
        self.timeframe = self.config.get("timeframe", "1m")
        self.leverage = float(self.config.get("leverage", 5.0))
        self.ema_fast_len = int(self.config.get("ema_fast", 50))
        self.ema_medium_len = int(self.config.get("ema_medium", 100))
        self.ema_slow_len = int(self.config.get("ema_slow", 200))
        self.entry_distance_percent = float(self.config.get("entry_distance_percent", 0.15))
        self.risk_reward = float(self.config.get("risk_reward", 2.0))
        self.square_off_str = self.config.get("square_off", "15:15")
        self.entry_start_str = self.config.get("entry_start", "09:20")
        self.entry_end_str = self.config.get("entry_end", "15:00")
        self.sl_buffer_ticks = int(self.config.get("sl_buffer_ticks", 2))
        self.trailing_step_rr = float(self.config.get("trailing_step_rr", 0.5))
        self.trend_confirmation_bars = int(self.config.get("trend_confirmation_bars", 3))
        self.enable_setup_a = bool(self.config.get("enable_setup_a", True))
        self.enable_setup_b = bool(self.config.get("enable_setup_b", True))
        self.enable_trailing_sl = bool(self.config.get("enable_trailing_sl", True))

        # Parse time boundaries
        self.square_off_time = datetime.strptime(self.square_off_str, "%H:%M").time()
        self.entry_start_time = datetime.strptime(self.entry_start_str, "%H:%M").time()
        self.entry_end_time = datetime.strptime(self.entry_end_str, "%H:%M").time()

        self.available_buying_power = capital * self.leverage

        # Historical data lists
        self.closes: List[float] = []
        self.opens: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.timestamps: List[datetime] = []

        # Indicators
        self.ema_fast_vals: List[Optional[float]] = []
        self.ema_medium_vals: List[Optional[float]] = []
        self.ema_slow_vals: List[Optional[float]] = []

        # Trend states
        self.bullish_trend_bars = 0
        self.bearish_trend_bars = 0

        # State machine
        self.active_trade: Optional[Dict[str, Any]] = None
        self.pending_entry: Optional[Dict[str, Any]] = None

        # Trade records
        self.trade_history: List[Dict[str, Any]] = []

    def _calculate_all_ema(self, prices: List[float], period: int) -> List[Optional[float]]:
        ema: List[Optional[float]] = [None] * len(prices)
        if len(prices) < period:
            return ema
        sma = sum(prices[:period]) / period
        ema[period - 1] = sma
        alpha = 2 / (period + 1)
        for i in range(period, len(prices)):
            prev_ema = ema[i - 1]
            if prev_ema is not None:
                ema[i] = prices[i] * alpha + prev_ema * (1 - alpha)
        return ema

    def _calc_next_ema(self, price: float, prev_ema: float, period: int) -> float:
        alpha = 2 / (period + 1)
        return price * alpha + prev_ema * (1 - alpha)

    async def on_tick(self, packet: MarketPacket) -> None:
        """Processes each 1-minute OHLC tick."""
        if packet.security_id != self.symbol:
            return

        current_dt = packet.timestamp
        current_time = current_dt.time()

        if (packet.open is None or packet.close is None or 
            packet.high is None or packet.low is None or 
            packet.timestamp is None):
            return

        # Update historical closes
        self.closes.append(packet.close)
        self.opens.append(packet.open)
        self.highs.append(packet.high)
        self.lows.append(packet.low)
        self.timestamps.append(packet.timestamp)

        # Update EMAs
        if len(self.closes) >= self.ema_slow_len:
            if len(self.ema_slow_vals) == 0 or self.ema_slow_vals[-1] is None:
                # Cold start: Compute EMAs for all past closes
                self.ema_fast_vals = self._calculate_all_ema(self.closes, self.ema_fast_len)
                self.ema_medium_vals = self._calculate_all_ema(self.closes, self.ema_medium_len)
                self.ema_slow_vals = self._calculate_all_ema(self.closes, self.ema_slow_len)
            else:
                # Warm start: Calculate next step incrementally
                prev_fast = self.ema_fast_vals[-1]
                prev_med = self.ema_medium_vals[-1]
                prev_slow = self.ema_slow_vals[-1]
                if prev_fast is not None and prev_med is not None and prev_slow is not None:
                    self.ema_fast_vals.append(self._calc_next_ema(packet.close, prev_fast, self.ema_fast_len))
                    self.ema_medium_vals.append(self._calc_next_ema(packet.close, prev_med, self.ema_medium_len))
                    self.ema_slow_vals.append(self._calc_next_ema(packet.close, prev_slow, self.ema_slow_len))
                else:
                    self.ema_fast_vals.append(None)
                    self.ema_medium_vals.append(None)
                    self.ema_slow_vals.append(None)
        else:
            self.ema_fast_vals.append(None)
            self.ema_medium_vals.append(None)
            self.ema_slow_vals.append(None)

        # Update Trend confirmation counters
        if len(self.ema_slow_vals) > 0 and self.ema_slow_vals[-1] is not None:
            fast = self.ema_fast_vals[-1]
            med = self.ema_medium_vals[-1]
            slow = self.ema_slow_vals[-1]

            if fast is not None and med is not None and slow is not None:
                if fast > med > slow:
                    self.bullish_trend_bars += 1
                    self.bearish_trend_bars = 0
                elif fast < med < slow:
                    self.bearish_trend_bars += 1
                    self.bullish_trend_bars = 0
                else:
                    self.bullish_trend_bars = 0
                    self.bearish_trend_bars = 0
            else:
                self.bullish_trend_bars = 0
                self.bearish_trend_bars = 0
        else:
            self.bullish_trend_bars = 0
            self.bearish_trend_bars = 0

        # --- Active Trade Management ---
        if self.active_trade is not None:
            # If an exit order is already pending, don't submit another exit
            if self.active_trade.get("exit_order_pending"):
                return

            # Check for square off time
            if current_time >= self.square_off_time:
                dhan_logger.info(f"[Strategy] Square-off time reached. Closing position...")
                await self._close_position(packet, "Square Off")
                return

            # Check SL, TP, and Trailing Stop
            is_long = self.active_trade["side"] == "BUY"
            sl = float(self.active_trade["stop_loss"])
            tp = float(self.active_trade["take_profit"])

            if is_long:
                # Stopped out check
                if packet.low <= sl:
                    exit_price = min(packet.open, sl)
                    await self._close_position(packet, "Stop Loss", exit_price)
                    return
                # Target Profit check
                elif packet.high >= tp:
                    exit_price = max(packet.open, tp)
                    await self._close_position(packet, "Take Profit", exit_price)
                    return
                # Trailing stop update based on peak price
                if self.enable_trailing_sl and packet.high > self.active_trade["max_price"]:
                    self.active_trade["max_price"] = packet.high
                    initial_risk = self.active_trade["initial_risk"]
                    peak_r = (packet.high - self.active_trade["entry_price"]) / initial_risk
                    if peak_r >= 1.0:
                        steps = int((peak_r - 1.0) / self.trailing_step_rr)
                        new_sl = self.active_trade["entry_price"] + steps * self.trailing_step_rr * initial_risk
                        if new_sl > self.active_trade["stop_loss"]:
                            self.active_trade["stop_loss"] = new_sl
            else:
                # Stopped out check
                if packet.high >= sl:
                    exit_price = max(packet.open, sl)
                    await self._close_position(packet, "Stop Loss", exit_price)
                    return
                # Target Profit check
                elif packet.low <= tp:
                    exit_price = min(packet.open, tp)
                    await self._close_position(packet, "Take Profit", exit_price)
                    return
                # Trailing stop update based on trough price
                if self.enable_trailing_sl and packet.low < self.active_trade["min_price"]:
                    self.active_trade["min_price"] = packet.low
                    initial_risk = self.active_trade["initial_risk"]
                    peak_r = (self.active_trade["entry_price"] - packet.low) / initial_risk
                    if peak_r >= 1.0:
                        steps = int((peak_r - 1.0) / self.trailing_step_rr)
                        new_sl = self.active_trade["entry_price"] - steps * self.trailing_step_rr * initial_risk
                        if new_sl < self.active_trade["stop_loss"]:
                            self.active_trade["stop_loss"] = new_sl

            return

        # --- Entry Logic ---
        if self.pending_entry is not None:
            # We are waiting for the pending entry order to fill. Skip new entries.
            return

        # Check trading hours
        if not (self.entry_start_time <= current_time <= self.entry_end_time):
            return

        # Ensure indicators are fully calculated
        if len(self.ema_slow_vals) == 0 or self.ema_slow_vals[-1] is None or self.ema_medium_vals[-1] is None:
            return

        EMA100 = self.ema_medium_vals[-1]
        EMA200 = self.ema_slow_vals[-1]
        if EMA100 is None or EMA200 is None:
            return

        # Check previous candle confirmation
        if len(self.closes) >= 2:
            prev_close = self.closes[-2]
            prev_open = self.opens[-2]
            prev_bullish_or_neutral = prev_close >= prev_open
            prev_bearish_or_neutral = prev_close <= prev_open
        else:
            prev_bullish_or_neutral = False
            prev_bearish_or_neutral = False

        # 1. Bullish Trend Entries
        if self.bullish_trend_bars >= self.trend_confirmation_bars:
            dist100 = self.entry_distance_percent / 100.0 * EMA100
            dist200 = self.entry_distance_percent / 100.0 * EMA200

            # Setup A: Pullback near EMA100 (bullish candle, low is near EMA100, above EMA200)
            near_ema100 = (EMA100 - dist100) <= packet.low <= (EMA100 + dist100)
            bullish_candle = packet.close > packet.open
            above_ema200 = packet.low > EMA200

            if self.enable_setup_a and prev_bullish_or_neutral and near_ema100 and bullish_candle and above_ema200:
                await self._enter_position(packet, "BUY", "Setup A", EMA200)
                return

            # Setup B: Fall below EMA100, pullback near EMA200
            below_ema100 = packet.low < EMA100
            near_ema200 = (EMA200 - dist200) <= packet.low <= (EMA200 + dist200)

            if self.enable_setup_b and prev_bullish_or_neutral and below_ema100 and near_ema200 and bullish_candle:
                await self._enter_position(packet, "BUY", "Setup B", EMA200)
                return

        # 2. Bearish Trend Entries
        elif self.bearish_trend_bars >= self.trend_confirmation_bars:
            dist100 = self.entry_distance_percent / 100.0 * EMA100
            dist200 = self.entry_distance_percent / 100.0 * EMA200

            # Setup A: Rally near EMA100 (bearish candle, high is near EMA100, below EMA200)
            near_ema100 = (EMA100 - dist100) <= packet.high <= (EMA100 + dist100)
            bearish_candle = packet.close < packet.open
            below_ema200 = packet.high < EMA200

            if self.enable_setup_a and prev_bearish_or_neutral and near_ema100 and bearish_candle and below_ema200:
                await self._enter_position(packet, "SELL", "Setup A", EMA200)
                return

            # Setup B: Rise above EMA100, rally near EMA200
            above_ema100 = packet.high > EMA100
            near_ema200 = (EMA200 - dist200) <= packet.high <= (EMA200 + dist200)

            if self.enable_setup_b and prev_bearish_or_neutral and above_ema100 and near_ema200 and bearish_candle:
                await self._enter_position(packet, "SELL", "Setup B", EMA200)
                return

    async def _enter_position(self, packet: MarketPacket, side: str, setup_name: str, ema200: float) -> None:
        close_price = packet.close
        if close_price is None or close_price <= 0:
            return

        # Dynamically size based on available cash in broker portfolio
        portfolio = {}
        if self.manager:
            portfolio = self.manager.broker.get_portfolio()
        current_cash = portfolio.get("cash_inr", self.capital_limit)
        buying_power = current_cash * self.leverage
        
        qty = int(buying_power / close_price)
        if qty <= 0:
            return

        # Calculate previous candle direction
        if len(self.closes) >= 2:
            prev_close = self.closes[-2]
            prev_open = self.opens[-2]
            if prev_close > prev_open:
                prev_dir = "BULLISH"
            elif prev_close < prev_open:
                prev_dir = "BEARISH"
            else:
                prev_dir = "NEUTRAL"
        else:
            prev_dir = "UNKNOWN"

        trade_trend = "BULLISH" if self.bullish_trend_bars > 0 else "BEARISH"
        trade_type = "BULLISH" if side == "BUY" else "BEARISH"

        # Record pending entry details
        self.pending_entry = {
            "side": side,
            "setup": setup_name,
            "ema200": ema200,
            "trigger_time": packet.timestamp,
            "trigger_volume": packet.volume,
            "prev_candle_dir": prev_dir,
            "trade_trend": trade_trend,
            "trade_type": trade_type
        }

        dhan_logger.info(f"[Strategy] Entry Triggered: {side} {qty} {self.symbol} on {setup_name} at close ₹{close_price:.2f}")
        await self.submit_order(self.symbol, side, qty, price=close_price, order_type="MARKET")

    async def _close_position(self, packet: MarketPacket, reason: str, override_price: Optional[float] = None) -> None:
        if self.active_trade is None or self.active_trade.get("exit_order_pending"):
            return

        exit_price = override_price or packet.close
        if exit_price is None or exit_price <= 0:
            return

        side = "SELL" if self.active_trade["side"] == "BUY" else "BUY"
        qty = self.active_trade["qty"]

        self.active_trade["exit_order_pending"] = True
        self.active_trade["exit_reason"] = reason
        self.active_trade["exit_trigger_time"] = packet.timestamp

        # Set temporary exit_price for reference (actual price is recorded in on_order_fill)
        self.active_trade["temp_exit_price"] = exit_price

        dhan_logger.info(f"[Strategy] Exit Triggered: {reason} order submitted for {qty} shares at ₹{exit_price:.2f}")
        # Note: If PaperBroker accepts LIMIT orders, we submit with price=override_price.
        # But standard MARKET order is the most robust and handles slippage naturally.
        await self.submit_order(self.symbol, side, qty, price=exit_price, order_type="MARKET")

    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        """Callback received from PaperBroker when order executes."""
        if symbol != self.symbol:
            return

        # 1. Entry Order Fill
        if self.active_trade is None and self.pending_entry is not None:
            # Map entry fields
            setup_name = self.pending_entry["setup"]
            ema200 = self.pending_entry["ema200"]
            trigger_time = self.pending_entry["trigger_time"]

            # Calculate Stop Loss
            tick_size = 0.05
            if side == "BUY":
                stop_loss = ema200 - self.sl_buffer_ticks * tick_size
                initial_risk = abs(price - stop_loss)
                take_profit = price + initial_risk * self.risk_reward
            else:
                stop_loss = ema200 + self.sl_buffer_ticks * tick_size
                initial_risk = abs(price - stop_loss)
                take_profit = price - initial_risk * self.risk_reward

            # Round to nearest tick size
            stop_loss = round(stop_loss * 20) / 20
            take_profit = round(take_profit * 20) / 20

            self.active_trade = {
                "order_id": order_id,
                "side": side,
                "qty": qty,
                "entry_price": price,
                "entry_time": self.timestamps[-1],
                "setup": setup_name,
                "initial_risk": initial_risk,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "max_price": price,
                "min_price": price,
                "entry_fees": 0.0, # Will fetch from order history or calculate
                "trigger_volume": self.pending_entry["trigger_volume"],
                "prev_candle_dir": self.pending_entry["prev_candle_dir"],
                "trade_trend": self.pending_entry["trade_trend"],
                "trade_type": self.pending_entry["trade_type"]
            }

            # Retrieve commission paid for this order from broker
            commission = 0.0
            if self.manager:
                portfolio = self.manager.broker.get_portfolio()
                broker_order = self.manager.broker._order_history.get(order_id, {})
                commission = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
                if commission == 0.0:
                    commission = self.manager.broker._calculate_transaction_charges(side, price * qty)
            self.active_trade["entry_fees"] = commission

            dhan_logger.info(
                f"[Strategy] Position Opened: {side} {qty} at ₹{price:.2f}. "
                f"SL: ₹{stop_loss:.2f}, TP: ₹{take_profit:.2f}, Risk: ₹{initial_risk:.2f}, Fee: ₹{commission:.2f}"
            )
            self.pending_entry = None

        # 2. Exit Order Fill
        elif self.active_trade is not None:
            exit_time = self.timestamps[-1]
            entry_price = self.active_trade["entry_price"]
            qty = self.active_trade["qty"]
            side_entry = self.active_trade["side"]

            # Retrieve commission paid for exit order
            exit_fees = 0.0
            if self.manager:
                broker_order = self.manager.broker._order_history.get(order_id, {})
                exit_fees = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
                if exit_fees == 0.0:
                    exit_fees = self.manager.broker._calculate_transaction_charges(side, price * qty)

            total_fees = self.active_trade["entry_fees"] + exit_fees

            # Compute PnL
            if side_entry == "BUY":
                gross_pnl = (price - entry_price) * qty
            else:
                gross_pnl = (entry_price - price) * qty

            net_pnl = gross_pnl - total_fees
            hold_time_mins = int((exit_time - self.active_trade["entry_time"]).total_seconds() / 60.0)

            trade_record = {
                "Trade_ID": len(self.trade_history) + 1,
                "Symbol": self.symbol,
                "Direction": "LONG" if side_entry == "BUY" else "SHORT",
                "Setup": self.active_trade["setup"],
                "Entry_Time": self.active_trade["entry_time"].isoformat(),
                "Entry_Price": entry_price,
                "Qty": qty,
                "Exit_Time": exit_time.isoformat(),
                "Exit_Price": price,
                "Gross_PnL": gross_pnl,
                "Fees": total_fees,
                "Net_PnL": net_pnl,
                "Exit_Reason": self.active_trade["exit_reason"],
                "Hold_Time_Mins": hold_time_mins,
                "Entry_Candle_Volume": self.active_trade["trigger_volume"],
                "Prev_Candle_Direction": self.active_trade["prev_candle_dir"],
                "Trade_Trend": self.active_trade["trade_trend"],
                "Trade_Type": self.active_trade["trade_type"]
            }

            self.trade_history.append(trade_record)
            dhan_logger.info(
                f"[Strategy] Position Closed: {trade_record['Exit_Reason']} fill at ₹{price:.2f}. "
                f"Gross PnL: ₹{gross_pnl:.2f}, Fees: ₹{total_fees:.2f}, Net PnL: ₹{net_pnl:.2f}, Hold Time: {hold_time_mins} mins"
            )

            # Clear position state
            self.active_trade = None
            self.pending_entry = None
