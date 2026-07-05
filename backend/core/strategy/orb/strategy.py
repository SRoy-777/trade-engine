import uuid
from datetime import datetime, time, date
from typing import List, Dict, Any, Optional, Callable
from models.market import MarketEvent
from core.strategy.orb.config import orb_config
from core.strategy.orb.models import TradingSignal, TradeRecord
from core.strategy.orb.filters import TimeFilter, VolumeFilter, BaseFilter
from core.strategy.orb.analytics import TradeAnalytics
from providers.market.dhan.logger import dhan_logger

class OpeningRangeBreakoutStrategy:
    """Consumes MarketEvents from the Event Bus and generates signal-only ORB breakouts."""

    def __init__(self, 
                 config=orb_config, 
                 filters: Optional[List[BaseFilter]] = None,
                 signal_callback: Optional[Callable[[TradingSignal], None]] = None):
        self.config = config
        self.analytics = TradeAnalytics()
        
        # Load default filters if none provided
        self.filters: List[BaseFilter] = filters if filters is not None else [
            TimeFilter(),
            VolumeFilter()
        ]
        
        # Optional callback for broadcasting signals
        self.signal_callback = signal_callback
        
        # Daily state tracking variables
        self.current_date: Optional[date] = None
        self.opening_high = 0.0
        self.opening_low = float("inf")
        self.opening_range = 0.0
        self.range_formed = False
        self.daily_trades_taken = 0
        self.volume_history: List[int] = []
        
        # Active position details (dictionary tracking the state of current signal)
        self.active_position: Optional[Dict[str, Any]] = None

    async def register_to_event_bus(self) -> None:
        """Subscribes the strategy handler to the Event Bus at Priority 5."""
        from event_bus.event_bus import event_bus
        await event_bus.subscribe(self.on_market_event, priority=5)
        dhan_logger.info("[ORB Strategy] Subscribed to Event Bus at Priority 5")

    async def on_market_event(self, event: MarketEvent) -> None:
        """Processes incoming MarketEvent and evaluates ORB logic rules."""
        event_time = event.exchange_timestamp or event.received_timestamp
        event_date = event_time.date()
        time_str = event_time.strftime("%H:%M")

        # 1. Detect day boundaries and reset variables
        if self.current_date is None or event_date != self.current_date:
            self.current_date = event_date
            self.opening_high = 0.0
            self.opening_low = float("inf")
            self.opening_range = 0.0
            self.range_formed = False
            self.daily_trades_taken = 0
            self.volume_history = []
            self.active_position = None
            dhan_logger.info(f"[ORB Strategy] New trading day detected: {event_date}. Session resets complete.")

        # 2. Accumulate High, Low, and Volume during range formation window
        if self.config.ORB_START <= time_str <= self.config.ORB_END:
            self.opening_high = max(self.opening_high, event.ltp)
            self.opening_low = min(self.opening_low, event.ltp)
            self.volume_history.append(event.volume)
            if len(self.volume_history) > self.config.VOLUME_LOOKBACK:
                self.volume_history.pop(0)

        # 3. Finalize opening range targets at window boundary close
        elif time_str > self.config.ORB_END:
            if not self.range_formed:
                self.opening_range = self.opening_high - self.opening_low
                self.range_formed = True
                dhan_logger.info(
                    f"[ORB Strategy] Opening Range Created for {event.symbol}. "
                    f"High: Rs.{self.opening_high:.2f}, Low: Rs.{self.opening_low:.2f}, Range: Rs.{self.opening_range:.2f}"
                )

            # 4. Check for Entry Breakout Triggers
            min_range = self.opening_low * 0.005
            if (
                self.daily_trades_taken < self.config.MAX_TRADES_PER_DAY
                and not self.active_position
                and self.opening_range >= min_range
            ):
                # Evaluate filters
                avg_volume = sum(self.volume_history) / len(self.volume_history) if self.volume_history else 0.0
                context = {
                    "orb_end": self.config.ORB_END,
                    "last_entry": self.config.LAST_ENTRY_TIME,
                    "avg_volume": avg_volume,
                    "min_volume_multiplier": self.config.MIN_VOLUME_MULTIPLIER
                }
                
                # Check breakout condition (LTP crosses range high) and filter validations
                is_breakout = event.ltp > self.opening_high
                filters_passed = all(f.validate(event, context) for f in self.filters)
                
                if is_breakout and filters_passed:
                    # Generate Trading Signal (BUY)
                    entry_price = event.ltp
                    stop_loss = self.opening_low
                    target = entry_price + (self.opening_range * self.config.RISK_REWARD)
                    
                    signal = TradingSignal(
                        signal_id=str(uuid.uuid4()),
                        timestamp=event_time,
                        symbol=event.symbol,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target=target,
                        reason="Opening Range Breakout",
                        risk_reward=self.config.RISK_REWARD
                    )
                    
                    dhan_logger.info(
                        f"[ORB Strategy] Breakout Detected! Trade Triggered on {event.symbol} at Rs.{entry_price:.2f}. "
                        f"SL: Rs.{stop_loss:.2f}, TP Target: Rs.{target:.2f}"
                    )
                    
                    self.active_position = {
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "target": target,
                        "entry_time": event_time,
                        "mfe": entry_price,
                        "mae": entry_price
                    }
                    self.daily_trades_taken += 1
                    
                    if self.signal_callback:
                        self.signal_callback(signal)
                        
                elif is_breakout and not filters_passed:
                    # Skip log
                    dhan_logger.debug(f"[ORB Strategy] Trade Skipped on {event.symbol}: Breakout met but filters failed.")

            # 5. Monitor and check Exit levels if position is active
            if self.active_position:
                # Update Excursions (MFE/MAE)
                self.active_position["mfe"] = max(self.active_position["mfe"], event.ltp)
                self.active_position["mae"] = min(self.active_position["mae"], event.ltp)
                
                # Break-Even SL trailing
                if event.ltp >= self.active_position["entry_price"] + self.opening_range:
                    if self.active_position["stop_loss"] < self.active_position["entry_price"]:
                        self.active_position["stop_loss"] = self.active_position["entry_price"]
                        dhan_logger.info(
                            f"[ORB Strategy] Profit reached 1.0x range. Trailing Stop Loss to break-even at Rs.{self.active_position['entry_price']:.2f}"
                        )
                
                # Exit Triggers
                is_target_hit = event.ltp >= self.active_position["target"]
                is_sl_hit = event.ltp <= self.active_position["stop_loss"]
                is_square_off = time_str >= self.config.SQUARE_OFF_TIME
                
                if is_target_hit or is_sl_hit or is_square_off:
                    reason = "Target Hit" if is_target_hit else ("Stop Loss Hit" if is_sl_hit else "Square Off")
                    exit_price = event.ltp
                    entry_p = self.active_position["entry_price"]
                    
                    # Log exit
                    if is_target_hit:
                        dhan_logger.info(f"[ORB Strategy] Target Hit on {event.symbol} at Rs.{exit_price:.2f}")
                    elif is_sl_hit:
                        dhan_logger.warning(f"[ORB Strategy] Stop Loss Hit on {event.symbol} at Rs.{exit_price:.2f}")
                    else:
                        dhan_logger.info(f"[ORB Strategy] Square Off reached on {event.symbol} at Rs.{exit_price:.2f}")

                    # Calculate analytics
                    record = TradeRecord(
                        symbol=event.symbol,
                        entry_time=self.active_position["entry_time"],
                        exit_time=event_time,
                        entry_price=entry_p,
                        exit_price=exit_price,
                        holding_time_secs=(event_time - self.active_position["entry_time"]).total_seconds(),
                        pnl=exit_price - entry_p,
                        mfe=self.active_position["mfe"] - entry_p,
                        mae=entry_p - self.active_position["mae"],
                        exit_reason=reason
                    )
                    
                    self.analytics.add_record(record)
                    self.active_position = None
