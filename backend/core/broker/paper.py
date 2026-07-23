import asyncio
import uuid
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Callable, Awaitable, List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from core.broker.base import BaseBroker
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class SimulationConfig(BaseSettings):
    """Configuration options for realistic execution matching, slippage, spreads, and liquidity."""
    LATENCY_MS: int = Field(default=50)  # 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1000ms
    SPREAD_MODEL: str = Field(default="NONE")  # "NONE", "FIXED", "PERCENTAGE", "DYNAMIC", "BID_ASK"
    SPREAD_VALUE: float = Field(default=0.0)  # Absolute points for FIXED, or decimal fraction for PERCENTAGE (e.g. 0.0002)
    SLIPPAGE_MODEL: str = Field(default="NONE")  # "NONE", "FIXED_TICKS", "PERCENTAGE", "ATR", "VOLUME", "ORDER_SIZE", "RANDOM"
    SLIPPAGE_VALUE: float = Field(default=0.0)  # Multiplier value for the selected model
    LIQUIDITY_MODEL: str = Field(default="INFINITE")  # "INFINITE", "FINITE"
    LIQUIDITY_FACTOR: float = Field(default=0.1)  # Fraction of tick volume that can be matched (e.g., 0.1 for 10%)
    PARTIAL_FILLS_ALLOWED: bool = Field(default=True)
    MARKET_IMPACT_ALLOWED: bool = Field(default=False)
    MARKET_IMPACT_FACTOR: float = Field(default=0.05)
    
    # Session trading rules
    ALLOWED_SESSIONS: List[str] = Field(default_factory=lambda: ["REGULAR"])  # "PRE_OPEN", "REGULAR", "CLOSING"
    
    # Lot size and margin settings
    LOT_SIZE: int = Field(default=1)
    MIN_QTY: int = Field(default=1)
    MARGIN_MULTIPLIER: float = Field(default=5.0)  # 5x leverage

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Global settings block
sim_config = SimulationConfig()


class PaperBroker(BaseBroker):
    """Simulated execution engine matching orders with configurable latency, slippage, spread, and liquidity models."""

    def __init__(self, initial_cash_inr: float = 10000000.0, latency_ms: Optional[float] = None, product_type: str = "INTRADAY", sim_cfg: Optional[SimulationConfig] = None):
        self._cash = initial_cash_inr
        self.product_type = product_type.upper()  # INTRADAY or DELIVERY
        self.current_time: Optional[datetime] = None  # Simulated time clock driven by ticks
        
        # Load simulation config
        self.sim_config = sim_cfg if sim_cfg is not None else sim_config
        # Override latency from constructor if provided
        if latency_ms is not None:
            self.sim_config.LATENCY_MS = int(latency_ms)
            
        self.latency_ms = float(self.sim_config.LATENCY_MS)

        # Positions: symbol -> {"qty": float, "avg_price": float}
        self._positions: Dict[str, Dict[str, float]] = {}
        
        # Pending Orders: order_id -> dict
        self._pending_orders: Dict[str, Dict[str, Any]] = {}
        
        # All Orders Log: order_id -> dict
        self._order_history: Dict[str, Dict[str, Any]] = {}
        
        # Real-time prices: symbol -> ltp
        self._last_prices: Dict[str, float] = {}
        
        # Total transaction taxes and execution slippage details paid
        self.total_fees_paid_inr: float = 0.0
        self.total_slippage_cost_inr: float = 0.0
        self.total_spread_cost_inr: float = 0.0
        self.total_market_impact_inr: float = 0.0
        self.total_orders_filled: int = 0
        self.total_partial_fills: int = 0
        
        # Callback for fill updates
        self._fill_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._lock = asyncio.Lock()

    def register_fill_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def get_portfolio(self) -> Dict[str, Any]:
        """Calculates and returns current assets valuation in INR."""
        holdings_value = 0.0
        margin_locked = 0.0
        margin_multiplier = self.sim_config.MARGIN_MULTIPLIER if self.product_type == "INTRADAY" else 1.0

        for symbol, pos in self._positions.items():
            current_price = self._last_prices.get(symbol, pos["avg_price"])
            holdings_value += pos["qty"] * current_price
            margin_locked += abs(pos["qty"] * current_price) / margin_multiplier

        total_value = self._cash + holdings_value
        available_cash = max(0.0, total_value - margin_locked)

        return {
            "cash_inr": available_cash,
            "holdings_market_value_inr": holdings_value,
            "net_asset_value_inr": total_value,
            "positions": self._positions,
            "last_prices": self._last_prices,
            "total_fees_paid_inr": self.total_fees_paid_inr,
            "total_slippage_cost_inr": self.total_slippage_cost_inr,
            "total_spread_cost_inr": self.total_spread_cost_inr,
            "total_market_impact_inr": self.total_market_impact_inr,
            "total_orders_filled": self.total_orders_filled,
            "total_partial_fills": self.total_partial_fills
        }

    def _is_within_market_hours(self, ts: datetime) -> bool:
        """Validates session trading hour restrictions."""
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        # If the timestamp is naive, assume it is already in IST (consistent with ticks/backtest)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ist_tz)
        ts_ist = ts.astimezone(ist_tz)
        time_str = ts_ist.strftime("%H:%M")
        
        if "PRE_OPEN" in self.sim_config.ALLOWED_SESSIONS and "09:00" <= time_str <= "09:08":
            return True
        if "REGULAR" in self.sim_config.ALLOWED_SESSIONS and "09:15" <= time_str <= "15:30":
            return True
        if "CLOSING" in self.sim_config.ALLOWED_SESSIONS and "15:30" <= time_str <= "16:00":
            return True
        return False

    async def submit_order(self, order_request: Dict[str, Any]) -> str:
        """Processes order request, enforcing session, lot size, margin limits, and queueing latency."""
        order_id = f"paper_ord_{uuid.uuid4().hex[:8]}"
        current_ts = self.current_time
        if current_ts is None:
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            current_ts = datetime.now(timezone.utc).astimezone(ist_tz).replace(tzinfo=None)
        
        symbol = order_request["symbol"]
        qty = int(order_request["qty"])
        side = order_request["side"].upper()
        order_type = order_request["order_type"].upper()  # MARKET, LIMIT, STOP_MARKET, STOP_LIMIT, IOC
        price = float(order_request["price"])

        order = {
            "order_id": order_id,
            "strategy_id": order_request["strategy_id"],
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "remaining_qty": qty,
            "price": price,
            "order_type": order_type,
            "status": "SUBMITTED",
            "submitted_at": current_ts.isoformat(),
            "broker_received_at": (current_ts + timedelta(milliseconds=self.latency_ms / 2.0)).isoformat(),
            "partial_fills": []
        }

        async with self._lock:
            self._order_history[order_id] = order

            # 1. Enforce trading session hours
            if not self._is_within_market_hours(current_ts):
                order["status"] = "REJECTED"
                order["reason"] = "Outside configured market hours"
                dhan_logger.warning(f"[Paper Broker] Order {order_id} rejected: Outside session hours ({current_ts.strftime('%H:%M:%S')})")
                return order_id

            # 2. Enforce minimum quantity and lot size constraints
            if qty < self.sim_config.MIN_QTY:
                order["status"] = "REJECTED"
                order["reason"] = f"Quantity {qty} less than minimum required {self.sim_config.MIN_QTY}"
                dhan_logger.warning(f"[Paper Broker] Order {order_id} rejected: {order['reason']}")
                return order_id

            if qty % self.sim_config.LOT_SIZE != 0:
                adjusted_qty = (qty // self.sim_config.LOT_SIZE) * self.sim_config.LOT_SIZE
                if adjusted_qty < self.sim_config.MIN_QTY:
                    order["status"] = "REJECTED"
                    order["reason"] = f"Quantity {qty} failed lot size alignment of {self.sim_config.LOT_SIZE}"
                    dhan_logger.warning(f"[Paper Broker] Order {order_id} rejected: {order['reason']}")
                    return order_id
                dhan_logger.info(f"[Paper Broker] Aligning quantity {qty} to lot size {self.sim_config.LOT_SIZE}. New qty: {adjusted_qty}")
                qty = adjusted_qty
                order["qty"] = qty
                order["remaining_qty"] = qty

            # 3. Enforce margin checks
            margin_multiplier = self.sim_config.MARGIN_MULTIPLIER if self.product_type == "INTRADAY" else 1.0
            required_margin = (qty * price) / margin_multiplier
            # On sell execution we don't buy, but we check if we hold the position for DELIVERY sells
            if side == "BUY" and required_margin > self._cash:
                order["status"] = "REJECTED"
                order["reason"] = f"Insufficient margin. Required: Rs.{required_margin:.2f}, Available: Rs.{self._cash:.2f}"
                dhan_logger.warning(f"[Paper Broker] Order {order_id} rejected: {order['reason']}")
                return order_id

            # 4. Route orders based on execution type
            if order_type == "MARKET":
                if self.latency_ms <= 0:
                    # Fill instantly
                    fill_price = self._last_prices.get(symbol, price)
                    pkt = MarketPacket(
                        packet_type="Ticker",
                        exchange_segment="NSE_EQ",
                        security_id=symbol,
                        ltp=fill_price,
                        volume=100000,
                        timestamp=current_ts
                    )
                    await self._match_and_fill_order(order_id, pkt)
                else:
                    # Delay order fill behind latency
                    fill_time = current_ts + timedelta(milliseconds=self.latency_ms)
                    order["fill_after"] = fill_time
                    order["status"] = "QUEUED_LATENCY"
                    self._pending_orders[order_id] = order
                    dhan_logger.info(f"[Paper Broker] Market order {order_id} queued with {self.latency_ms}ms latency delay")
            elif order_type in ["LIMIT", "STOP_MARKET", "STOP_LIMIT"]:
                # Limit / Stop orders are kept in the pending book
                order["status"] = "PENDING"
                self._pending_orders[order_id] = order
                dhan_logger.info(f"[Paper Broker] {order_type} order queued: {order_id} ({symbol} triggers at Rs.{price:.2f})")
            elif order_type == "IOC":
                # Immediate Or Cancel: processes instantly, cancels any remainder
                fill_price = self._last_prices.get(symbol, price)
                pkt = MarketPacket(
                    packet_type="Ticker",
                    exchange_segment="NSE_EQ",
                    security_id=symbol,
                    ltp=fill_price,
                    volume=10000,
                    timestamp=current_ts
                )
                await self._match_and_fill_order(order_id, pkt)
                if order["remaining_qty"] > 0:
                    order["status"] = "CANCELLED"
                    order["reason"] = "IOC unfilled portion cancelled"
                    dhan_logger.info(f"[Paper Broker] IOC Order {order_id} partially filled. Unfilled remainder {order['remaining_qty']} cancelled.")
            else:
                order["status"] = "REJECTED"
                order["reason"] = f"Unsupported order type: {order_type}"
                dhan_logger.warning(f"[Paper Broker] Order {order_id} rejected: {order['reason']}")

        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order."""
        current_ts = self.current_time
        if current_ts is None:
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            current_ts = datetime.now(timezone.utc).astimezone(ist_tz).replace(tzinfo=None)
        async with self._lock:
            if order_id in self._pending_orders:
                order = self._pending_orders.pop(order_id)
                order["status"] = "CANCELLED"
                order["cancelled_at"] = current_ts.isoformat()
                dhan_logger.info(f"[Paper Broker] Order cancelled: {order_id}")
                return True
        return False

    async def on_tick(self, packet: MarketPacket) -> None:
        """Updates prices, simulates Bid/Ask, and processes pending latency/limit orders."""
        symbol = packet.security_id
        price = packet.ltp
        self.current_time = packet.timestamp
        
        async with self._lock:
            self._last_prices[symbol] = price
            
            # 1. Determine Bid/Ask values on current tick
            bid, ask = self._calculate_bid_ask(packet)
            
            to_fill = []
            for order_id, order in list(self._pending_orders.items()):
                if order["symbol"] != symbol:
                    continue
                
                # Check latency target for MARKET orders
                if order["order_type"] == "MARKET":
                    fill_after = order.get("fill_after")
                    if fill_after and self.current_time >= fill_after:
                        to_fill.append(order_id)
                    continue

                # Check LIMIT / STOP order crossing triggers
                limit_price = order["price"]
                side = order["side"]
                
                if order["order_type"] == "LIMIT":
                    if side == "BUY" and ask <= limit_price:
                        to_fill.append(order_id)
                    elif side == "SELL" and bid >= limit_price:
                        to_fill.append(order_id)
                elif order["order_type"] == "STOP_MARKET":
                    # Stop triggers when price crosses trigger level
                    if side == "BUY" and price >= limit_price:
                        order["order_type"] = "MARKET"
                        order["fill_after"] = self.current_time + timedelta(milliseconds=self.latency_ms)
                        dhan_logger.info(f"[Paper Broker] Stop Market triggered for {order_id}. Converted to MARKET.")
                    elif side == "SELL" and price <= limit_price:
                        order["order_type"] = "MARKET"
                        order["fill_after"] = self.current_time + timedelta(milliseconds=self.latency_ms)
                        dhan_logger.info(f"[Paper Broker] Stop Market triggered for {order_id}. Converted to MARKET.")

            for order_id in to_fill:
                await self._match_and_fill_order(order_id, packet)

    def _calculate_bid_ask(self, packet: MarketPacket) -> tuple:
        """Determines Bid/Ask spread prices using the configured spread model."""
        price = packet.ltp
        spread_model = self.sim_config.SPREAD_MODEL.upper()
        
        # Check if packet contains real bid/ask quotes
        packet_bid = getattr(packet, "bid", None) or packet.raw_fields.get("bid")
        packet_ask = getattr(packet, "ask", None) or packet.raw_fields.get("ask")
        if spread_model == "BID_ASK" and packet_bid is not None and packet_ask is not None:
            return float(packet_bid), float(packet_ask)

        if spread_model == "FIXED":
            spread = self.sim_config.SPREAD_VALUE
        elif spread_model == "PERCENTAGE":
            spread = price * self.sim_config.SPREAD_VALUE
        elif spread_model == "DYNAMIC":
            # Dynamic spread scales wider if volume is low (modeling illiquidity)
            vol = max(1.0, float(packet.volume or 1000.0))
            spread = self.sim_config.SPREAD_VALUE * (1.0 + 5000.0 / vol)
        else:
            spread = 0.0

        bid = price - (spread / 2.0)
        ask = price + (spread / 2.0)
        return round(bid * 20) / 20, round(ask * 20) / 20

    def _calculate_slippage(self, side: str, price: float, qty: int, packet: MarketPacket) -> float:
        """Calculates slippage according to the selected model."""
        model = self.sim_config.SLIPPAGE_MODEL.upper()
        value = self.sim_config.SLIPPAGE_VALUE
        tick_size = 0.05 # standard NSE tick size

        if model == "FIXED_TICKS":
            slippage = value * tick_size
        elif model == "PERCENTAGE":
            slippage = price * value
        elif model == "VOLUME":
            vol = max(1.0, float(packet.volume or 10000.0))
            slippage = price * (qty / vol) * value
        elif model == "ORDER_SIZE":
            slippage = price * (qty / 1000.0) * value
        elif model == "RANDOM":
            # Adverse normal distribution
            slippage = abs(random.normalvariate(0, value))
        else:
            slippage = 0.0

        return slippage

    async def _match_and_fill_order(self, order_id: str, packet: MarketPacket) -> None:
        """Enforces liquidity limits, partial fills, slippage, and updates portfolio cash/positions."""
        order = self._order_history[order_id]
        symbol = order["symbol"]
        side = order["side"]
        qty = order["remaining_qty"]
        
        # 1. Determine execution reference price based on Bid/Ask spread
        bid, ask = self._calculate_bid_ask(packet)
        ref_price = ask if side == "BUY" else bid
        
        # 2. Simulate Liquidity Constraint
        if self.sim_config.LIQUIDITY_MODEL.upper() == "FINITE" and packet.volume is not None:
            max_fillable = max(1, int(packet.volume * self.sim_config.LIQUIDITY_FACTOR))
            if qty > max_fillable:
                if not self.sim_config.PARTIAL_FILLS_ALLOWED:
                    # Delay matching until next ticks
                    return
                # Partial Fill
                fill_qty = max_fillable
                self.total_partial_fills += 1
            else:
                fill_qty = qty
        else:
            fill_qty = qty

        # 3. Simulate Slippage & Market Impact
        slippage = self._calculate_slippage(side, ref_price, fill_qty, packet)
        
        market_impact = 0.0
        if self.sim_config.MARKET_IMPACT_ALLOWED and packet.volume:
            if fill_qty / packet.volume > 0.01:
                market_impact = ref_price * ((fill_qty / packet.volume) ** 2) * self.sim_config.MARKET_IMPACT_FACTOR
                
        slippage_sign = 1 if side == "BUY" else -1
        fill_price = ref_price + (slippage_sign * (slippage + market_impact))
        fill_price = round(fill_price * 20) / 20 # align to tick size 0.05
        
        # 4. Enforce Lot size adjustment on fill quantity
        if fill_qty % self.sim_config.LOT_SIZE != 0:
            fill_qty = (fill_qty // self.sim_config.LOT_SIZE) * self.sim_config.LOT_SIZE
            if fill_qty <= 0:
                return # skip until larger volume

        # 5. Process cash and position updates
        txn_value = fill_price * fill_qty
        charges = self._calculate_transaction_charges(side, txn_value)
        self.total_fees_paid_inr += charges
        self.total_slippage_cost_inr += slippage * fill_qty
        self.total_spread_cost_inr += abs(ref_price - packet.ltp) * fill_qty
        self.total_market_impact_inr += market_impact * fill_qty
        
        if symbol not in self._positions:
            self._positions[symbol] = {"qty": 0.0, "avg_price": 0.0}
            
        pos = self._positions[symbol]
        curr_qty = pos["qty"]
        curr_avg = pos["avg_price"]
        
        trade_qty = fill_qty if side == "BUY" else -fill_qty
        new_qty = curr_qty + trade_qty
        
        # Calculate realized P&L on reduction/close
        realized_pnl = 0.0
        if curr_qty > 0 and trade_qty < 0: # reducing a long position
            closed_qty = min(curr_qty, abs(trade_qty))
            realized_pnl = (fill_price - curr_avg) * closed_qty
        elif curr_qty < 0 and trade_qty > 0: # reducing a short position
            closed_qty = min(abs(curr_qty), trade_qty)
            realized_pnl = (curr_avg - fill_price) * closed_qty
            
        # Core cash balance only changes by realized P&L and transaction fees
        self._cash += realized_pnl - charges
        
        if curr_qty == 0:
            pos["avg_price"] = fill_price
        elif new_qty == 0:
            pos["avg_price"] = 0.0
        elif (curr_qty > 0 and trade_qty > 0) or (curr_qty < 0 and trade_qty < 0):
            pos["avg_price"] = ((curr_qty * curr_avg) + (trade_qty * fill_price)) / new_qty
            
        pos["qty"] = new_qty
        if new_qty == 0:
            self._positions.pop(symbol, None)

        # 6. Update order state logs
        order["remaining_qty"] -= fill_qty
        fill_details = {
            "filled_qty": fill_qty,
            "fill_price": fill_price,
            "filled_at": (self.current_time or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(),
            "slippage": slippage,
            "spread_cost": abs(ref_price - packet.ltp),
            "market_impact": market_impact,
            "commission": charges
        }
        order["partial_fills"].append(fill_details)
        
        if order["remaining_qty"] <= 0:
            # Complete execution
            order["status"] = "FILLED"
            order["filled_at"] = fill_details["filled_at"]
            order["fill_price"] = sum(f["fill_price"] * f["filled_qty"] for f in order["partial_fills"]) / order["qty"]
            order["transaction_charges_inr"] = sum(f["commission"] for f in order["partial_fills"])
            
            # Remove from pending queue
            self._pending_orders.pop(order_id, None)
            self.total_orders_filled += 1
            
            dhan_logger.info(
                f"[Paper Broker] Order {order_id} FILLED fully: {side} {order['qty']} {symbol} at Rs.{order['fill_price']:.2f}. "
                f"Slippage: Rs.{slippage:.4f}, Charges: Rs.{order['transaction_charges_inr']:.2f}"
            )
        else:
            order["status"] = "PARTIALLY_FILLED"
            dhan_logger.info(
                f"[Paper Broker] Order {order_id} PARTIALLY FILLED: {side} {fill_qty} of {order['qty']} {symbol} at Rs.{fill_price:.2f}. "
                f"Remaining: {order['remaining_qty']}"
            )

        # 7. Notify execution updates
        if self._fill_callback:
            fill_event = {
                "order_id": order_id,
                "strategy_id": order["strategy_id"],
                "symbol": symbol,
                "side": side,
                "qty": fill_qty,
                "price": fill_price,
                "status": order["status"]
            }
            async def run_callback():
                await self._fill_callback(fill_event)
            asyncio.create_task(run_callback())

    def _calculate_transaction_charges(self, side: str, value: float) -> float:
        """Calculates Dhan's standard Indian transaction fees in INR based on product type."""
        if self.product_type == "DELIVERY":
            brokerage = 0.0
            stt = value * 0.001
            stamp_duty = value * 0.00015 if side == "BUY" else 0.0
        else:
            brokerage = min(20.0, value * 0.0003)
            stt = value * 0.00025 if side == "SELL" else 0.0
            stamp_duty = value * 0.00003 if side == "BUY" else 0.0

        exch_txn_charge = value * 0.0000322
        sebi_fee = value * 0.000001
        gst = (brokerage + exch_txn_charge + sebi_fee) * 0.18
        
        return brokerage + exch_txn_charge + sebi_fee + gst + stt + stamp_duty
