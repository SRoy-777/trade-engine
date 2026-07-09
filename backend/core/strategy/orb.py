import yaml
import logging
from datetime import datetime, date
from typing import Any, List, Optional
from core.strategy.base import BaseStrategy
from providers.market.dhan.models import MarketPacket

dhan_logger = logging.getLogger("dhan_provider")

class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout (ORB) Strategy.
    - Defines opening range (e.g., 9:15 to 9:30) high/low.
    - Enters trade on breakout of opening range (Close > High or Close < Low).
    - Requires volume confirmation (volume > multiplier * average volume).
    - Uses range boundary as Stop Loss.
    - Exits via static TP (based on Risk Reward ratio) or SL, or at Square-off time.
    """
    def __init__(self, config_path: str):
        self.config = {}
        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f) or {}
        except Exception:
            pass

        self.strategy_id = "orb"
        name = "Opening Range Breakout Strategy"
        self.symbol = self.config.get("symbol", "SBIN")
        capital = float(self.config.get("capital", 60000.0))

        super().__init__(strategy_id=self.strategy_id, name=name, symbols=[self.symbol], capital_limit=capital)

        # Load parameters
        self.timeframe = self.config.get("timeframe", "5m")
        self.leverage = float(self.config.get("leverage", 5.0))
        self.opening_range_mins = int(self.config.get("opening_range_mins", 15))
        self.volume_filter_multiplier = float(self.config.get("volume_filter_multiplier", 1.5))
        self.volume_sma_len = int(self.config.get("volume_sma_len", 10))
        self.take_profit_pct = float(self.config.get("take_profit_pct", 1.5))
        self.stop_loss_pct = float(self.config.get("stop_loss_pct", 1.0))
        self.enable_trailing_sl = bool(self.config.get("enable_trailing_sl", False))
        self.trailing_trigger_rr = float(self.config.get("trailing_trigger_rr", 1.0))
        self.trailing_step_rr = float(self.config.get("trailing_step_rr", 0.5))
        self.square_off_str = self.config.get("square_off", "15:15")
        self.entry_start_str = self.config.get("entry_start", "09:30")
        self.entry_end_str = self.config.get("entry_end", "11:00")

        # Parse times
        self.square_off_time = datetime.strptime(self.square_off_str, "%H:%M").time()
        self.entry_start_time = datetime.strptime(self.entry_start_str, "%H:%M").time()
        self.entry_end_time = datetime.strptime(self.entry_end_str, "%H:%M").time()

        # Historical data list
        self.opens: List[float] = []
        self.closes: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.timestamps: List[datetime] = []
        self.volumes: List[int] = []

        # Strategy daily state variables
        self.current_day: Optional[date] = None
        self.daily_opening_range_candles: List[MarketPacket] = []
        self.curr_day_high: Optional[float] = None
        self.curr_day_low: Optional[float] = None
        self.opening_range_set: bool = False
        self.trade_taken_today: bool = False
        
        # State tracking
        self.active_trade: Optional[dict[str, Any]] = None
        self.pending_entry: Optional[dict[str, Any]] = None
        self.trade_history: List[dict[str, Any]] = []

    async def on_tick(self, packet: MarketPacket) -> None:
        """Processes each 5-minute OHLC tick."""
        if packet.security_id != self.symbol:
            return

        if (packet.open is None or packet.close is None or 
            packet.high is None or packet.low is None or 
            packet.volume is None or packet.timestamp is None):
            return

        current_dt = packet.timestamp
        current_date = current_dt.date()
        current_time = current_dt.time()

        # 1. Reset state on a new trading day
        if self.current_day is None or current_date != self.current_day:
            self.current_day = current_date
            self.daily_opening_range_candles = []
            self.curr_day_high = None
            self.curr_day_low = None
            self.opening_range_set = False
            self.trade_taken_today = False
            self.pending_entry = None
            
        # Append data to lists
        self.opens.append(packet.open)
        self.closes.append(packet.close)
        self.highs.append(packet.high)
        self.lows.append(packet.low)
        self.timestamps.append(packet.timestamp)
        self.volumes.append(packet.volume)

        # 2. Build the opening range candles
        if not self.opening_range_set:
            if current_time < self.entry_start_time:
                self.daily_opening_range_candles.append(packet)
            else:
                # We have reached/passed the entry start time. Calculate the opening range
                if len(self.daily_opening_range_candles) > 0:
                    highs_list = [p.high for p in self.daily_opening_range_candles if p.high is not None]
                    lows_list = [p.low for p in self.daily_opening_range_candles if p.low is not None]
                    if len(highs_list) > 0 and len(lows_list) > 0:
                        self.curr_day_high = max(highs_list)
                        self.curr_day_low = min(lows_list)
                        self.opening_range_set = True
                        dhan_logger.info(
                            f"[ORB] {current_date} Opening Range established: "
                            f"High=₹{self.curr_day_high:.2f}, Low=₹{self.curr_day_low:.2f} "
                            f"(from {len(self.daily_opening_range_candles)} candles)"
                        )
                else:
                    # In case data starts late
                    self.opening_range_set = False

        # --- Active Trade Management ---
        if self.active_trade is not None:
            trade = self.active_trade
            # If an exit order is already pending, don't submit another exit
            if trade.get("exit_order_pending"):
                return

            # Check for square off time
            if current_time >= self.square_off_time:
                dhan_logger.info(f"[Strategy] Square-off time reached. Closing position...")
                await self._close_position(packet, "Square Off")
                return

            # Check SL, TP
            is_long = trade["side"] == "BUY"
            sl = trade["stop_loss"]
            tp = trade["take_profit"]

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

                # Trailing stop update
                if self.enable_trailing_sl:
                    if packet.high > trade["max_price"]:
                        trade["max_price"] = packet.high
                    
                    entry_price = trade["entry_price"]
                    initial_risk = trade["initial_risk"]
                    peak_r = (trade["max_price"] - entry_price) / initial_risk
                    
                    if peak_r >= self.trailing_trigger_rr:
                        steps = int((peak_r - self.trailing_trigger_rr) / self.trailing_step_rr)
                        new_sl = entry_price + steps * self.trailing_step_rr * initial_risk
                        new_sl = round(new_sl * 20) / 20
                        if new_sl > trade["stop_loss"]:
                            trade["stop_loss"] = new_sl
                            dhan_logger.info(f"[ORB] Trailing Stop Loss updated to: ₹{new_sl:.2f} (Peak R: {peak_r:.2f})")
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

                # Trailing stop update
                if self.enable_trailing_sl:
                    if packet.low < trade["min_price"]:
                        trade["min_price"] = packet.low
                    
                    entry_price = trade["entry_price"]
                    initial_risk = trade["initial_risk"]
                    peak_r = (entry_price - trade["min_price"]) / initial_risk
                    
                    if peak_r >= self.trailing_trigger_rr:
                        steps = int((peak_r - self.trailing_trigger_rr) / self.trailing_step_rr)
                        new_sl = entry_price - steps * self.trailing_step_rr * initial_risk
                        new_sl = round(new_sl * 20) / 20
                        if new_sl < trade["stop_loss"]:
                            trade["stop_loss"] = new_sl
                            dhan_logger.info(f"[ORB] Trailing Stop Loss updated to: ₹{new_sl:.2f} (Peak R: {peak_r:.2f})")
            return

        # --- Entry Logic ---
        if self.pending_entry is not None:
            return

        # Check trading hours & ensure range is set & only 1 trade per day
        if not self.opening_range_set or self.trade_taken_today:
            return

        if not (self.entry_start_time <= current_time <= self.entry_end_time):
            return

        # Calculate previous average volume (excluding the current breakout candle)
        prev_vols = self.volumes[:-1]
        if len(prev_vols) > 0:
            vol_sma_len = min(self.volume_sma_len, len(prev_vols))
            avg_volume = sum(prev_vols[-vol_sma_len:]) / vol_sma_len
        else:
            avg_volume = 0.0

        # Breakout Checks
        if self.curr_day_high is None or self.curr_day_low is None:
            return

        long_breakout = packet.close > self.curr_day_high
        short_breakout = packet.close < self.curr_day_low

        volume_confirmed = packet.volume >= (self.volume_filter_multiplier * avg_volume)

        if (long_breakout or short_breakout) and volume_confirmed:
            side = "BUY" if long_breakout else "SELL"
            self.trade_taken_today = True
            await self._enter_position(packet, side, "ORB Breakout", avg_volume)

    async def _enter_position(self, packet: MarketPacket, side: str, setup_name: str, avg_volume: float) -> None:
        close_price = packet.close
        if close_price is None or close_price <= 0:
            return

        portfolio = self.manager.broker.get_portfolio() if self.manager else {}
        
        # Query manager for allocated capital based on ranking/allocation strategy
        if self.manager and hasattr(self.manager, "get_allocated_capital"):
            allocated_capital = self.manager.get_allocated_capital(self.symbol)
        else:
            allocated_capital = self.capital_limit

        if allocated_capital <= 0:
            dhan_logger.warning(f"[ORB] Entry Blocked: Zero capital allocated to {self.symbol} (strategy or rank restriction)")
            return

        buying_power = allocated_capital * self.leverage
        
        # Enforce actual available cash constraints from the main funds pool
        available_cash = portfolio.get("cash_inr", 0.0)
        max_broker_power = available_cash * self.leverage
        buying_power = min(buying_power, max_broker_power)
        
        # Cap buying power by risk controller limits if present
        if self.manager and hasattr(self.manager, "risk_controller"):
            max_allowed_val = self.manager.risk_controller.max_capital_per_trade_inr
            buying_power = min(buying_power, max_allowed_val * 0.99)
        
        qty = int(buying_power / close_price)
        if qty <= 0:
            dhan_logger.warning(f"[ORB] Entry Blocked: Calculated quantity is 0 for {self.symbol} (Price: {close_price}, Buying Power: {buying_power})")
            return

        # Calculate previous candle direction (t-1 Relative to entry trigger)
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

        trade_trend = "BULLISH" if side == "BUY" else "BEARISH"
        trade_type = "BULLISH" if side == "BUY" else "BEARISH"

        self.pending_entry = {
            "side": side,
            "setup": setup_name,
            "trigger_time": packet.timestamp,
            "trigger_volume": packet.volume,
            "prev_candle_dir": prev_dir,
            "trade_trend": trade_trend,
            "trade_type": trade_type,
            "orb_high": self.curr_day_high,
            "orb_low": self.curr_day_low
        }

        dhan_logger.info(f"[ORB] Entry Triggered: {side} {qty} {self.symbol} on {setup_name} at close ₹{close_price:.2f} (Avg Vol: {avg_volume:.0f})")
        await self.submit_order(self.symbol, side, qty, price=close_price, order_type="MARKET")

    async def _close_position(self, packet: MarketPacket, reason: str, override_price: Optional[float] = None) -> None:
        if self.active_trade is None or self.active_trade.get("exit_order_pending"):
            return

        exit_price = override_price or packet.close
        if exit_price is None or exit_price <= 0:
            return

        trade = self.active_trade
        side = "SELL" if trade["side"] == "BUY" else "BUY"
        qty = trade["qty"]

        trade["exit_order_pending"] = True
        trade["exit_reason"] = reason
        trade["exit_trigger_time"] = packet.timestamp
        trade["temp_exit_price"] = exit_price

        dhan_logger.info(f"[ORB] Exit Triggered: {reason} order submitted for {qty} shares at ₹{exit_price:.2f}")
        await self.submit_order(self.symbol, side, qty, price=exit_price, order_type="MARKET")

    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        if symbol != self.symbol:
            return

        # 1. Entry Order Fill
        if self.active_trade is None and self.pending_entry is not None:
            setup_name = self.pending_entry["setup"]
            orb_high = self.pending_entry["orb_high"]
            orb_low = self.pending_entry["orb_low"]

            # Calculate Stop Loss and Take Profit based on configured percentages
            if side == "BUY":
                stop_loss = price * (1.0 - self.stop_loss_pct / 100.0)
                take_profit = price * (1.0 + self.take_profit_pct / 100.0)
            else:
                stop_loss = price * (1.0 + self.stop_loss_pct / 100.0)
                take_profit = price * (1.0 - self.take_profit_pct / 100.0)

            initial_risk = abs(price - stop_loss)

            # Round to tick size 0.05
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
                "entry_fees": 0.0,
                "trigger_volume": self.pending_entry["trigger_volume"],
                "prev_candle_dir": self.pending_entry["prev_candle_dir"],
                "trade_trend": self.pending_entry["trade_trend"],
                "trade_type": self.pending_entry["trade_type"]
            }

            # Retrieve commission
            commission = 0.0
            if self.manager:
                portfolio = self.manager.broker.get_portfolio()
                broker_order = self.manager.broker._order_history.get(order_id, {})
                commission = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
                if commission == 0.0:
                    commission = self.manager.broker._calculate_transaction_charges(side, price * qty)
            self.active_trade["entry_fees"] = commission

            dhan_logger.info(
                f"[ORB] Position Opened: {side} {qty} at ₹{price:.2f}. "
                f"SL: ₹{stop_loss:.2f}, TP: ₹{take_profit:.2f}, Risk: ₹{initial_risk:.2f}, Fee: ₹{commission:.2f}"
            )
            self.pending_entry = None

        # 2. Exit Order Fill
        elif self.active_trade is not None:
            trade = self.active_trade
            exit_time = self.timestamps[-1]
            entry_price = trade["entry_price"]
            qty = trade["qty"]
            side_entry = trade["side"]

            broker_order = self.manager.broker._order_history.get(order_id, {}) if self.manager else {}
            exit_fees = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
            if exit_fees == 0.0 and self.manager:
                exit_fees = self.manager.broker._calculate_transaction_charges(side, price * qty)

            total_fees = trade["entry_fees"] + exit_fees

            if side_entry == "BUY":
                gross_pnl = (price - entry_price) * qty
            else:
                gross_pnl = (entry_price - price) * qty

            net_pnl = gross_pnl - total_fees
            hold_time_mins = int((exit_time - trade["entry_time"]).total_seconds() / 60.0)

            trade_record = {
                "Trade_ID": len(self.trade_history) + 1,
                "Symbol": self.symbol,
                "Direction": "LONG" if side_entry == "BUY" else "SHORT",
                "Setup": trade["setup"],
                "Entry_Time": trade["entry_time"].isoformat(),
                "Entry_Price": entry_price,
                "Qty": qty,
                "Exit_Time": exit_time.isoformat(),
                "Exit_Price": price,
                "Gross_PnL": gross_pnl,
                "Fees": total_fees,
                "Net_PnL": net_pnl,
                "Exit_Reason": trade["exit_reason"],
                "Hold_Time_Mins": hold_time_mins,
                "Entry_Candle_Volume": trade["trigger_volume"],
                "Prev_Candle_Direction": trade["prev_candle_dir"],
                "Trade_Trend": trade["trade_trend"],
                "Trade_Type": trade["trade_type"]
            }

            self.trade_history.append(trade_record)
            dhan_logger.info(
                f"[ORB] Position Closed: {trade_record['Exit_Reason']} fill at ₹{price:.2f}. "
                f"Gross PnL: ₹{gross_pnl:.2f}, Fees: ₹{total_fees:.2f}, Net PnL: ₹{net_pnl:.2f}, Hold Time: {hold_time_mins} mins"
            )

            self.active_trade = None
            self.pending_entry = None
