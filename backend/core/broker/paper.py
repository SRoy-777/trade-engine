import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Awaitable, List, Optional
from core.broker.base import BaseBroker
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class PaperBroker(BaseBroker):
    """Simulated execution engine matching orders with latency delays and Dhan transaction fees in INR."""

    def __init__(self, initial_cash_inr: float = 10000000.0, latency_ms: float = 50.0):
        self._cash = initial_cash_inr
        self.latency_ms = latency_ms # Simulated network roundtrip execution delay in ms
        
        # Positions: symbol -> {"qty": float, "avg_price": float}
        self._positions: Dict[str, Dict[str, float]] = {}
        
        # Pending Orders (limit orders and delayed market orders): order_id -> dict
        self._pending_orders: Dict[str, Dict[str, Any]] = {}
        
        # All Orders Log: order_id -> dict
        self._order_history: Dict[str, Dict[str, Any]] = {}
        
        # Real-time prices: symbol -> ltp
        self._last_prices: Dict[str, float] = {}
        
        # Total transaction taxes paid
        self.total_fees_paid_inr: float = 0.0
        
        # Callback for fill updates
        self._fill_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._lock = asyncio.Lock()

    def register_fill_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def get_portfolio(self) -> Dict[str, Any]:
        """Calculates and returns current assets valuation in INR."""
        holdings_value = 0.0
        for symbol, pos in self._positions.items():
            current_price = self._last_prices.get(symbol, pos["avg_price"])
            holdings_value += pos["qty"] * current_price

        total_value = self._cash + holdings_value
        return {
            "cash_inr": self._cash,
            "holdings_market_value_inr": holdings_value,
            "net_asset_value_inr": total_value,
            "positions": self._positions,
            "last_prices": self._last_prices,
            "total_fees_paid_inr": self.total_fees_paid_inr
        }

    async def submit_order(self, order_request: Dict[str, Any]) -> str:
        """Processes order request, applying simulated roundtrip latency for MARKET orders."""
        order_id = f"paper_ord_{uuid.uuid4().hex[:8]}"
        
        order = {
            "order_id": order_id,
            "strategy_id": order_request["strategy_id"],
            "symbol": order_request["symbol"],
            "side": order_request["side"], # BUY / SELL
            "qty": order_request["qty"],
            "price": order_request["price"],
            "order_type": order_request["order_type"], # MARKET / LIMIT
            "status": "SUBMITTED",
            "submitted_at": datetime.utcnow().isoformat()
        }

        async with self._lock:
            self._order_history[order_id] = order
            symbol = order["symbol"]
            
            if order["order_type"] == "MARKET":
                # Market orders are queued with a delay to mimic roundtrip execution latency
                fill_time = datetime.utcnow() + timedelta(milliseconds=self.latency_ms)
                order["fill_after"] = fill_time
                order["status"] = "QUEUED_LATENCY"
                self._pending_orders[order_id] = order
                dhan_logger.info(f"[Paper Broker] Market order {order_id} queued with {self.latency_ms}ms latency delay")
            else:
                # Queue LIMIT order directly in book
                order["status"] = "PENDING"
                self._pending_orders[order_id] = order
                dhan_logger.info(f"[Paper Broker] Limit order queued: {order_id} ({symbol} limit Rs.{order['price']:.2f})")

        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending limit order."""
        async with self._lock:
            if order_id in self._pending_orders:
                order = self._pending_orders.pop(order_id)
                order["status"] = "CANCELLED"
                order["cancelled_at"] = datetime.utcnow().isoformat()
                dhan_logger.info(f"[Paper Broker] Order cancelled: {order_id}")
                return True
        return False

    async def on_tick(self, packet: MarketPacket) -> None:
        """Updates prices and checks if any queued limit or latency market orders are filled."""
        symbol = packet.security_id
        price = packet.ltp
        
        async with self._lock:
            self._last_prices[symbol] = price
            
            to_fill = []
            for order_id, order in list(self._pending_orders.items()):
                if order["symbol"] != symbol:
                    continue
                
                # Check MARKET order latency delay
                if order["order_type"] == "MARKET":
                    fill_after = order.get("fill_after")
                    if fill_after and datetime.utcnow() >= fill_after:
                        to_fill.append((order_id, price))
                    continue
                
                # Check LIMIT order crossing logic
                limit_price = order["price"]
                side = order["side"]
                if side == "BUY" and price <= limit_price:
                    to_fill.append((order_id, price))
                elif side == "SELL" and price >= limit_price:
                    to_fill.append((order_id, price))
                    
            for order_id, fill_price in to_fill:
                self._pending_orders.pop(order_id, None)
                await self._fill_order(order_id, fill_price)

    def _calculate_transaction_charges(self, side: str, value: float) -> float:
        """Calculates Dhan's standard Indian intraday transaction fees in INR."""
        # 1. Brokerage: 0.03% of trade value or Rs. 20 (whichever is lower)
        brokerage = min(20.0, value * 0.0003)
        
        # 2. Exchange Transaction Charge (NSE Equity Intraday: 0.00322%)
        exch_txn_charge = value * 0.0000322
        
        # 3. SEBI Turnover Fee (Rs 10 per Crore / 0.0001%)
        sebi_fee = value * 0.000001
        
        # 4. GST: 18% on (Brokerage + Exchange Txn Charge + SEBI Fee)
        gst = (brokerage + exch_txn_charge + sebi_fee) * 0.18
        
        # 5. Securities Transaction Tax (STT: 0.025% on Sell side only)
        stt = value * 0.00025 if side == "SELL" else 0.0
        
        # 6. Stamp Duty (Intraday Equity: 0.003% on Buy side only)
        stamp_duty = value * 0.00003 if side == "BUY" else 0.0
        
        total_charges = brokerage + exch_txn_charge + sebi_fee + gst + stt + stamp_duty
        return total_charges

    async def _fill_order(self, order_id: str, fill_price: float) -> None:
        """Executes position updates, applies Dhan transaction fees, and notifies strategy callbacks."""
        order = self._order_history[order_id]
        symbol = order["symbol"]
        side = order["side"]
        qty = order["qty"]
        
        # 1. Update Cash Balances in INR
        transaction_value = fill_price * qty
        charges = self._calculate_transaction_charges(side, transaction_value)
        self.total_fees_paid_inr += charges
        
        if side == "BUY":
            self._cash -= (transaction_value + charges)
        else:
            self._cash += (transaction_value - charges)

        # 2. Update Positions
        if symbol not in self._positions:
            self._positions[symbol] = {"qty": 0.0, "avg_price": 0.0}
            
        pos = self._positions[symbol]
        current_qty = pos["qty"]
        current_avg = pos["avg_price"]
        
        trade_qty = qty if side == "BUY" else -qty
        new_qty = current_qty + trade_qty
        
        if current_qty == 0:
            pos["avg_price"] = fill_price
        elif new_qty == 0:
            pos["avg_price"] = 0.0
        elif (current_qty > 0 and trade_qty > 0) or (current_qty < 0 and trade_qty < 0):
            pos["avg_price"] = ((current_qty * current_avg) + (trade_qty * fill_price)) / new_qty
            
        pos["qty"] = new_qty
        
        if new_qty == 0:
            self._positions.pop(symbol, None)

        # 3. Mark filled
        order["status"] = "FILLED"
        order["filled_at"] = datetime.utcnow().isoformat()
        order["fill_price"] = fill_price
        order["transaction_charges_inr"] = charges
        
        dhan_logger.info(
            f"[Paper Broker] Order {order_id} filled: {side} {qty} {symbol} at Rs.{fill_price:.2f}. "
            f"Charges: Rs.{charges:.2f}"
        )

        # 4. Trigger Strategy Event Callback
        if self._fill_callback:
            fill_event = {
                "order_id": order_id,
                "strategy_id": order["strategy_id"],
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": fill_price
            }
            asyncio.create_task(self._fill_callback(fill_event))
