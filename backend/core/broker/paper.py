import asyncio
import uuid
from typing import Dict, Any, Callable, Awaitable, List, Optional
from datetime import datetime
from core.broker.base import BaseBroker
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class PaperBroker(BaseBroker):
    """Simulated execution engine matching orders against incoming tick streams."""

    def __init__(self, initial_cash_inr: float = 10000000.0): # Default: 1 Crore virtual INR
        self._cash = initial_cash_inr
        
        # Positions: symbol -> {"qty": float, "avg_price": float}
        self._positions: Dict[str, Dict[str, float]] = {}
        
        # Pending Orders: order_id -> dict
        self._pending_orders: Dict[str, Dict[str, Any]] = {}
        
        # All Orders Log: order_id -> dict
        self._order_history: Dict[str, Dict[str, Any]] = {}
        
        # Real-time prices: symbol -> ltp
        self._last_prices: Dict[str, float] = {}
        
        # Callback for fill updates
        self._fill_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._lock = asyncio.Lock()

    def register_fill_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def get_portfolio(self) -> Dict[str, Any]:
        """Calculates and returns current assets valuation in INR."""
        # Calculate total holdings market value
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
            "last_prices": self._last_prices
        }

    async def submit_order(self, order_request: Dict[str, Any]) -> str:
        """Processes order request. Fills MARKET immediately, queues LIMIT."""
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
            
            # Match MARKET order immediately using last known tick price
            if order["order_type"] == "MARKET":
                fill_price = self._last_prices.get(symbol)
                if not fill_price:
                    # If no ticks have arrived yet, assume limit order fallback price or error out
                    fill_price = order["price"] if order["price"] and order["price"] > 0 else 1.0
                    
                await self._fill_order(order_id, fill_price)
            else:
                # Queue LIMIT order for matching in next tick cycles
                order["status"] = "PENDING"
                self._pending_orders[order_id] = order
                dhan_logger.info(f"[Paper Broker] Limit order queued: {order_id} ({symbol} limit ₹{order['price']:.2f})")

        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending limit order."""
        async with self._lock:
            if order_id in self._pending_orders:
                order = self._pending_orders.pop(order_id)
                order["status"] = "CANCELLED"
                order["cancelled_at"] = datetime.utcnow().isoformat()
                dhan_logger.info(f"[Paper Broker] Limit order cancelled: {order_id}")
                return True
        return False

    async def on_tick(self, packet: MarketPacket) -> None:
        """Updates prices and checks if any queued limit orders are crossed by the tick."""
        symbol = packet.security_id
        price = packet.ltp
        
        async with self._lock:
            # 1. Update last price cache
            self._last_prices[symbol] = price
            
            # 2. Check and fill pending limit orders
            to_fill = []
            for order_id, order in list(self._pending_orders.items()):
                if order["symbol"] != symbol:
                    continue
                
                limit_price = order["price"]
                side = order["side"]
                
                # Check crossing logic
                if side == "BUY" and price <= limit_price:
                    to_fill.append((order_id, price)) # Filled at tick price or limit price? Standard: filled at tick price
                elif side == "SELL" and price >= limit_price:
                    to_fill.append((order_id, price))
                    
            # Execute fills outside the loop
            for order_id, fill_price in to_fill:
                self._pending_orders.pop(order_id, None)
                await self._fill_order(order_id, fill_price)

    async def _fill_order(self, order_id: str, fill_price: float) -> None:
        """Executes position updates and notifies callbacks of fills."""
        order = self._order_history[order_id]
        symbol = order["symbol"]
        side = order["side"]
        qty = order["qty"]
        
        # 1. Update Cash Balances in INR
        transaction_value = fill_price * qty
        if side == "BUY":
            self._cash -= transaction_value
        else:
            self._cash += transaction_value

        # 2. Update Positions
        if symbol not in self._positions:
            self._positions[symbol] = {"qty": 0.0, "avg_price": 0.0}
            
        pos = self._positions[symbol]
        current_qty = pos["qty"]
        current_avg = pos["avg_price"]
        
        trade_qty = qty if side == "BUY" else -qty
        new_qty = current_qty + trade_qty
        
        if current_qty == 0:
            # Position opened
            pos["avg_price"] = fill_price
        elif new_qty == 0:
            # Position closed
            pos["avg_price"] = 0.0
        elif (current_qty > 0 and trade_qty > 0) or (current_qty < 0 and trade_qty < 0):
            # Average entry price recalculation (adding to position)
            pos["avg_price"] = ((current_qty * current_avg) + (trade_qty * fill_price)) / new_qty
        else:
            # Reduction or reversal
            if abs(trade_qty) >= abs(current_qty):
                # Reversal
                pos["avg_price"] = fill_price
            
        pos["qty"] = new_qty
        
        # Clean up empty position entries
        if new_qty == 0:
            self._positions.pop(symbol, None)

        # 3. Mark filled
        order["status"] = "FILLED"
        order["filled_at"] = datetime.utcnow().isoformat()
        order["fill_price"] = fill_price
        
        dhan_logger.info(f"[Paper Broker] Order {order_id} filled: {side} {qty} {symbol} at ₹{fill_price:.2f}")

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
            # Run callback in task to keep lock duration short
            asyncio.create_task(self._fill_callback(fill_event))
