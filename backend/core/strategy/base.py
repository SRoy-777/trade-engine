from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
from providers.market.dhan.models import MarketPacket

class BaseStrategy(ABC):
    """Abstract base class for all algorithmic trading strategies."""

    def __init__(self, strategy_id: str, name: str, symbols: List[str], capital_limit: float = 100000.0):
        self.strategy_id = strategy_id
        self.name = name
        self.symbols = symbols
        self.capital_limit = capital_limit # Max capital exposure in INR
        self.is_active = True
        
        # Strategy-level portfolio tracking
        # symbol -> {"qty": int, "avg_price": float, "realized_pnl": float, "unrealized_pnl": float}
        self.positions: Dict[str, Dict[str, float]] = {}
        self.total_realized_pnl: float = 0.0
        
        # Reference to strategy manager or risk controller to submit orders
        self.manager: Optional[Any] = None

    def set_manager(self, manager: Any) -> None:
        """Binds this strategy to the central manager."""
        self.manager = manager

    @abstractmethod
    async def on_tick(self, packet: MarketPacket) -> None:
        """Called whenever a live market data tick is received for bound symbols."""
        pass

    @abstractmethod
    async def on_order_fill(self, order_id: str, symbol: str, side: str, qty: int, price: float) -> None:
        """Callback received when a submitted order gets successfully filled."""
        pass

    async def submit_order(self, symbol: str, side: str, qty: int, price: Optional[float] = None, order_type: str = "MARKET") -> None:
        """Submits an order request to the central execution gateway."""
        if not self.is_active:
            raise ValueError(f"Strategy {self.name} is inactive and cannot place orders")
            
        if self.manager:
            # Order request payload routed via Risk Controller
            order_request = {
                "strategy_id": self.strategy_id,
                "symbol": symbol,
                "side": side.upper(), # BUY / SELL
                "qty": qty,
                "price": price,
                "order_type": order_type.upper() # MARKET / LIMIT
            }
            await self.manager.process_order(order_request)
        else:
            raise RuntimeError(f"Strategy {self.name} is not bound to a StrategyManager / Execution Gateway")

    def update_unrealized_pnl(self, symbol: str, current_price: float) -> float:
        """Updates and returns the unrealized P&L in INR for a given symbol position."""
        if symbol not in self.positions or self.positions[symbol]["qty"] == 0:
            return 0.0
            
        pos = self.positions[symbol]
        qty = pos["qty"]
        avg_price = pos["avg_price"]
        
        # P&L calculation
        unrealized = (current_price - avg_price) * qty
        pos["unrealized_pnl"] = unrealized
        return unrealized

    def apply_fill(self, symbol: str, side: str, qty: int, price: float) -> None:
        """Updates position tracking and realizes P&L upon order execution fill."""
        if symbol not in self.positions:
            self.positions[symbol] = {
                "qty": 0.0,
                "avg_price": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0
            }
            
        pos = self.positions[symbol]
        current_qty = pos["qty"]
        current_avg = pos["avg_price"]
        
        trade_qty = qty if side.upper() == "BUY" else -qty
        new_qty = current_qty + trade_qty
        
        if current_qty == 0:
            # 1. Opening a brand new position
            pos["avg_price"] = price
            pos["qty"] = new_qty
        elif new_qty == 0:
            # 2. Fully closing a position
            trade_pnl = (price - current_avg) * current_qty if current_qty > 0 else (current_avg - price) * abs(current_qty)
            pos["realized_pnl"] += trade_pnl
            self.total_realized_pnl += trade_pnl
            pos["qty"] = 0.0
            pos["avg_price"] = 0.0
            pos["unrealized_pnl"] = 0.0
        elif (current_qty > 0 and trade_qty > 0) or (current_qty < 0 and trade_qty < 0):
            # 3. Adding to existing position (same direction)
            pos["avg_price"] = ((current_qty * current_avg) + (trade_qty * price)) / new_qty
            pos["qty"] = new_qty
        else:
            # 4. Reducing or reversing position
            if abs(trade_qty) >= abs(current_qty):
                # Reversal (closed current_qty, opened remaining new_qty in opposite direction)
                closed_qty = abs(current_qty)
                trade_pnl = (price - current_avg) * current_qty if current_qty > 0 else (current_avg - price) * closed_qty
                pos["realized_pnl"] += trade_pnl
                self.total_realized_pnl += trade_pnl
                
                # New direction entry
                pos["avg_price"] = price
                pos["qty"] = new_qty
            else:
                # Partial reduction (average price remains identical, realize P&L on closed portion)
                closed_qty = abs(trade_qty)
                trade_pnl = (price - current_avg) * closed_qty if current_qty > 0 else (current_avg - price) * closed_qty
                pos["realized_pnl"] += trade_pnl
                self.total_realized_pnl += trade_pnl
                pos["qty"] = new_qty

    def get_status(self) -> Dict[str, Any]:
        """Returns details, positions, and total P&L in INR."""
        total_unrealized = sum(pos["unrealized_pnl"] for pos in self.positions.values())
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "symbols": self.symbols,
            "is_active": self.is_active,
            "capital_limit": self.capital_limit,
            "positions": self.positions,
            "realized_pnl_inr": self.total_realized_pnl,
            "unrealized_pnl_inr": total_unrealized,
            "total_pnl_inr": self.total_realized_pnl + total_unrealized
        }
