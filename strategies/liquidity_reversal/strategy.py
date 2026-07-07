import os
import sys
import yaml
import logging
from datetime import datetime, date, time
from typing import List, Dict, Any, Optional
from pathlib import Path

# Add backend directory to path to resolve framework imports
current_dir = Path(__file__).resolve().parent
backend_dir = current_dir.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.append(str(backend_dir))

from core.strategy.base import BaseStrategy
from providers.market.dhan.models import MarketPacket

dhan_logger = logging.getLogger("dhan_provider")

class LiquidityReversalStrategy(BaseStrategy):
    """
    Liquidity Reversal Strategy (Long Only).
    - Long Entry: Current candle low < lowest low of previous N candles (entry_lookback).
    - Long Exit: Current candle high > highest high of previous M candles (exit_lookback), or Stop Loss, or Square Off time.
    """

    def __init__(self, config_path: str):
        # Resolve configuration path
        if not os.path.isabs(config_path):
            config_path = os.path.abspath(config_path)

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.strategy_id = "liquidity_reversal"
        name = "Liquidity Reversal Strategy"
        
        # Supporting symbols configuration
        symbols_cfg = self.config.get("symbols", "TMPV")
        if isinstance(symbols_cfg, list):
            self.symbol = symbols_cfg[0] if len(symbols_cfg) > 0 else "TMPV"
            self.symbols = symbols_cfg
        else:
            self.symbol = str(symbols_cfg)
            self.symbols = [self.symbol]

        capital = float(self.config.get("capital", 60000.0))
        super().__init__(strategy_id=self.strategy_id, name=name, symbols=self.symbols, capital_limit=capital)

        # Load parameters
        self.timeframe = self.config.get("timeframe", "5m")
        self.leverage = float(self.config.get("leverage", 5.0))
        self.entry_lookback = int(self.config.get("entry_lookback", 5))
        self.exit_lookback = int(self.config.get("exit_lookback", 5))
        
        # Stop Loss
        self.enable_stop_loss = bool(self.config.get("enable_stop_loss", True))
        self.stop_loss_type = self.config.get("stop_loss_type", "pct").lower()
        self.stop_loss_pct = float(self.config.get("stop_loss_pct", 1.0))
        
        # ATR Stop Loss
        self.atr_length = int(self.config.get("atr_length", 14))
        self.atr_multiplier = float(self.config.get("atr_multiplier", 1.5))
        
        # Swing Low Stop Loss
        self.previous_low_lookback = int(self.config.get("previous_low_lookback", 10))
        
        # Fixed Points Stop Loss
        self.fixed_stop_points = float(self.config.get("fixed_stop_points", 2.0))
        
        # Trailing Stop Loss
        self.enable_trailing_stop = bool(self.config.get("enable_trailing_stop", False))
        self.trailing_trigger_rr = float(self.config.get("trailing_trigger_rr", 1.0))
        self.trailing_step_rr = float(self.config.get("trailing_step_rr", 0.5))

        # Trading Hours
        self.entry_start_str = self.config.get("entry_start", "09:15")
        self.entry_end_str = self.config.get("entry_end", "15:00")
        self.square_off_str = self.config.get("square_off", "15:15")

        # Parse times
        self.entry_start_time = datetime.strptime(self.entry_start_str, "%H:%M").time()
        self.entry_end_time = datetime.strptime(self.entry_end_str, "%H:%M").time()
        self.square_off_time = datetime.strptime(self.square_off_str, "%H:%M").time()

        # Indicators and trade state
        self.opens: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.closes: List[float] = []
        self.volumes: List[int] = []
        self.timestamps: List[datetime] = []

        self.active_trade: Optional[Dict[str, Any]] = None
        self.pending_entry: Optional[Dict[str, Any]] = None
        self.trade_history: List[Dict[str, Any]] = []

    def _calculate_atr(self) -> float:
        if len(self.closes) < 2:
            return 0.0
        tr_list = []
        # Calculate True Range (TR)
        tr_list.append(self.highs[0] - self.lows[0])
        for i in range(1, len(self.closes)):
            h = self.highs[i]
            l = self.lows[i]
            pc = self.closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)

        length = self.atr_length
        if len(tr_list) < length:
            return sum(tr_list) / len(tr_list)
        return sum(tr_list[-length:]) / length

    async def on_tick(self, packet: MarketPacket) -> None:
        # 1. Update Historical Data Lists
        self.opens.append(packet.open)
        self.highs.append(packet.high)
        self.lows.append(packet.low)
        self.closes.append(packet.close)
        self.volumes.append(packet.volume)
        self.timestamps.append(packet.timestamp)

        # Ensure lists do not grow infinitely
        if len(self.closes) > 500:
            self.opens.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)
            self.closes.pop(0)
            self.volumes.pop(0)
            self.timestamps.pop(0)

        current_time = packet.timestamp.time()

        # 2. Active Trade Management
        if self.active_trade is not None:
            trade = self.active_trade
            if trade.get("exit_order_pending"):
                return

            # Check for square off time
            if current_time >= self.square_off_time:
                dhan_logger.info(f"[Liquidity Reversal] Square-off time reached. Closing position...")
                await self._close_position(packet, "Square Off")
                return

            # Check Stop Loss (if enabled)
            if self.enable_stop_loss and trade["stop_loss"] is not None:
                sl = trade["stop_loss"]
                if packet.low <= sl:
                    exit_price = min(packet.open, sl)
                    await self._close_position(packet, "Stop Loss", exit_price)
                    return

            # Check Exit Trigger
            # We need exit_lookback completed candles before the current candle
            if len(self.highs) >= self.exit_lookback + 1:
                prev_highs = self.highs[-self.exit_lookback - 1 : -1]
                highest_high = max(prev_highs)
                if packet.high > highest_high:
                    exit_price = max(packet.open, highest_high)
                    await self._close_position(packet, "Target Exit", exit_price)
                    return

            # Update Trailing Stop Loss
            if self.enable_stop_loss and self.enable_trailing_stop and trade["stop_loss"] is not None:
                if packet.high > trade["max_price"]:
                    trade["max_price"] = packet.high

                entry_price = trade["entry_price"]
                initial_risk = trade["initial_risk"]
                if initial_risk > 0:
                    peak_r = (trade["max_price"] - entry_price) / initial_risk
                    if peak_r >= self.trailing_trigger_rr:
                        steps = int((peak_r - self.trailing_trigger_rr) / self.trailing_step_rr)
                        new_sl = entry_price + steps * self.trailing_step_rr * initial_risk
                        new_sl = round(new_sl * 20) / 20
                        if new_sl > trade["stop_loss"]:
                            trade["stop_loss"] = new_sl
                            dhan_logger.info(
                                f"[Liquidity Reversal] Trailing SL updated: Rs. {new_sl:.2f} (Peak R: {peak_r:.2f})"
                            )
            return

        # 3. Entry Logic
        if self.pending_entry is not None:
            return

        # Only allow entry within hours
        if not (self.entry_start_time <= current_time <= self.entry_end_time):
            return

        # We need entry_lookback completed candles before the current candle
        if len(self.lows) >= self.entry_lookback + 1:
            prev_lows = self.lows[-self.entry_lookback - 1 : -1]
            lowest_low = min(prev_lows)
            
            if packet.low < lowest_low:
                # Trigger Entry
                await self._enter_position(packet, "BUY", "Liquidity Reversal")

    async def _enter_position(self, packet: MarketPacket, side: str, setup_name: str) -> None:
        close_price = packet.close
        if close_price is None or close_price <= 0:
            return

        # Calculate position sizing based on available cash and leverage
        portfolio = {}
        if self.manager:
            portfolio = self.manager.broker.get_portfolio()
        current_cash = portfolio.get("cash_inr", self.capital_limit)
        buying_power = current_cash * self.leverage
        qty = int(buying_power / close_price)

        if qty <= 0:
            return

        self.pending_entry = {
            "side": side,
            "setup": setup_name,
            "trigger_time": packet.timestamp,
            "trigger_volume": packet.volume,
            "close_at_trigger": close_price
        }

        dhan_logger.info(
            f"[Liquidity Reversal] Entry Triggered: {side} {qty} {self.symbol} at close Rs. {close_price:.2f}"
        )
        await self.submit_order(self.symbol, side, qty, price=close_price, order_type="MARKET")

    async def _close_position(self, packet: MarketPacket, reason: str, override_price: Optional[float] = None) -> None:
        if self.active_trade is None or self.active_trade.get("exit_order_pending"):
            return

        exit_price = override_price or packet.close
        if exit_price is None or exit_price <= 0:
            return

        side = "SELL"
        qty = self.active_trade["qty"]

        self.active_trade["exit_order_pending"] = True
        self.active_trade["exit_reason"] = reason
        self.active_trade["exit_trigger_time"] = packet.timestamp
        self.active_trade["temp_exit_price"] = exit_price

        dhan_logger.info(
            f"[Liquidity Reversal] Exit Triggered: {reason} order submitted for {qty} shares at Rs. {exit_price:.2f}"
        )
        await self.submit_order(self.symbol, side, qty, price=exit_price, order_type="MARKET")

    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        if symbol != self.symbol:
            return

        # 1. Entry Order Fill
        if self.active_trade is None and self.pending_entry is not None:
            # Determine Stop Loss based on Stop Loss Type
            stop_loss = None
            if self.enable_stop_loss:
                if self.stop_loss_type == "percent":
                    stop_loss = price * (1.0 - self.stop_loss_pct / 100.0)
                elif self.stop_loss_type == "atr":
                    atr_val = self._calculate_atr()
                    stop_loss = price - self.atr_multiplier * atr_val
                elif self.stop_loss_type == "previous_low":
                    if len(self.lows) >= self.previous_low_lookback + 1:
                        # Exclude current candle index (-1 represents current tick candle)
                        stop_loss = min(self.lows[-self.previous_low_lookback - 1 : -1])
                    else:
                        stop_loss = min(self.lows[:-1]) if len(self.lows) > 1 else price
                elif self.stop_loss_type == "fixed_points":
                    stop_loss = price - self.fixed_stop_points
                
                if stop_loss is not None:
                    stop_loss = round(stop_loss * 20) / 20

            initial_risk = abs(price - stop_loss) if stop_loss is not None else 0.0

            # Exit high lookback boundary calculation is dynamic, so no static TP is configured
            take_profit = 99999999.0 # Arbitrary high target to ensure lookback triggers the exit

            self.active_trade = {
                "order_id": order_id,
                "side": side,
                "qty": qty,
                "entry_price": price,
                "entry_time": self.timestamps[-1],
                "setup": self.pending_entry["setup"],
                "initial_risk": initial_risk,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "max_price": price,
                "entry_fees": 0.0,
                "trigger_volume": self.pending_entry["trigger_volume"]
            }

            # Retrieve commission
            commission = 0.0
            if self.manager:
                broker_order = self.manager.broker._order_history.get(order_id, {})
                commission = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
                if commission == 0.0:
                    commission = self.manager.broker._calculate_transaction_charges(side, price * qty)
            self.active_trade["entry_fees"] = commission

            dhan_logger.info(
                f"[Liquidity Reversal] Position Opened: {side} {qty} at Rs. {price:.2f}. "
                f"SL: {f'Rs. {stop_loss:.2f}' if stop_loss else 'None'}, Risk: Rs. {initial_risk:.2f}, Fee: Rs. {commission:.2f}"
            )
            self.pending_entry = None

        # 2. Exit Order Fill
        elif self.active_trade is not None:
            trade = self.active_trade
            exit_time = self.timestamps[-1]
            entry_price = trade["entry_price"]
            qty = trade["qty"]

            broker_order = self.manager.broker._order_history.get(order_id, {}) if self.manager else {}
            exit_fees = sum(f.get("commission", 0.0) for f in broker_order.get("partial_fills", []))
            if exit_fees == 0.0 and self.manager:
                exit_fees = self.manager.broker._calculate_transaction_charges(side, price * qty)

            total_fees = trade["entry_fees"] + exit_fees
            gross_pnl = (price - entry_price) * qty
            net_pnl = gross_pnl - total_fees
            hold_time_mins = int((exit_time - trade["entry_time"]).total_seconds() / 60.0)
            return_pct = (net_pnl / (entry_price * qty)) * 100.0

            trade_record = {
                "Symbol": self.symbol,
                "Entry Date": trade["entry_time"].date().isoformat(),
                "Entry Time": trade["entry_time"].time().isoformat(),
                "Exit Date": exit_time.date().isoformat(),
                "Exit Time": exit_time.time().isoformat(),
                "Entry Price": entry_price,
                "Exit Price": price,
                "Quantity": qty,
                "Gross P&L": gross_pnl,
                "Charges": total_fees,
                "Net P&L": net_pnl,
                "Return %": return_pct,
                "Holding Time": hold_time_mins,
                "Exit Reason": trade["exit_reason"]
            }

            self.trade_history.append(trade_record)
            dhan_logger.info(
                f"[Liquidity Reversal] Position Closed: {trade_record['Exit Reason']} fill at Rs. {price:.2f}. "
                f"Gross PnL: Rs. {gross_pnl:.2f}, Fees: Rs. {total_fees:.2f}, Net PnL: Rs. {net_pnl:.2f}, Hold Time: {hold_time_mins} mins"
            )

            self.active_trade = None
            self.pending_entry = None
