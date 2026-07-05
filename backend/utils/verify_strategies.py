import sys
import asyncio
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load .env variables before importing other modules
backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=backend_dir / ".env")

from core.broker.paper import PaperBroker
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.templates.hft_scalper import HftMicroScalper
from core.strategy.templates.day_trading import DayTradingTrendFollower
from providers.market.dhan.models import MarketPacket

async def run_simulation():
    print("=== STARTING STRATEGY AND PAPER TRADING VERIFICATION ===")
    
    # 1. Initialize Components
    # We give the paper broker 50 Lakh INR virtual balance
    paper_broker = PaperBroker(initial_cash_inr=5000000.0)
    risk_controller = RiskController(
        max_capital_per_trade_inr=20000000.0, # 2 Crore limit
        max_daily_loss_inr=10000.0,           # ₹10,000 limit
        margin_leverage_multiplier=5.0        # 5x paper leverage
    )
    manager = StrategyManager(paper_broker, risk_controller)

    # 2. Register Strategies
    # Strategy 1: HFT Scalper targeting ₹1,000 profit in 2 ticks
    scalper = HftMicroScalper(
        strategy_id="strat_hft_scalp_01",
        name="INR Micro Scalper",
        symbols=["1624"], # INFY
        target_profit_inr=1000.0,
        ticks_target=2
    )
    
    # Strategy 2: Standard Day Trader using 5-period SMA
    day_trader = DayTradingTrendFollower(
        strategy_id="strat_day_trend_01",
        name="Day Trend Follower",
        symbols=["1624"],
        sma_period=5
    )
    
    manager.register_strategy(scalper)
    manager.register_strategy(day_trader)

    # 3. Simulate Tick Sequences
    # Sequence of prices for INFY
    # We expect Scalper entry at 1600.00, TP hit at 1600.10.
    # Then second scalp entry at 1600.00, SL hit at 1599.70.
    ticks = [
        # --- First Trade (Profitable) ---
        1600.00, # Tick 1: Scalper enters BUY MARKET (10,000 shares at ₹1600)
        1600.05, # Tick 2: Price ticks up
        1600.10, # Tick 3: Price hits TP target (₹1600.10). Scalper exits SELL. Realized P&L: +₹1,000.
        
        # --- Second Trade (Stop Loss) ---
        1600.00, # Tick 4: Scalper enters second BUY MARKET (10,000 shares at ₹1600)
        1599.95, # Tick 5: Price ticks down
        1599.90, # Tick 6
        1599.85, # Tick 7
        1599.80, # Tick 8
        1599.75, # Tick 9
        1599.70, # Tick 10: Price hits SL target (₹1599.70). Scalper exits SELL. Realized loss: -₹3,000. Net P&L: -₹2,000.
    ]

    print("\n--- Feeding Tick Stream to Strategy Manager ---")
    for i, price in enumerate(ticks):
        packet = MarketPacket(
            packet_type="Ticker",
            exchange_segment="NSE_EQ",
            security_id="1624", # INFY
            ltp=price,
            timestamp=datetime.utcnow()
        )
        print(f"\nTick {i+1}: Price is Rs.{price:.2f}")
        await manager.on_tick(packet)
        # Give a small sleep to allow async task execution loops to complete
        await asyncio.sleep(0.01)

    print("\n--- Final Strategy Reports ---")
    for status in manager.get_all_strategy_status():
        print(f"\nStrategy: {status['name']} (ID: {status['strategy_id']})")
        print(f"  Realized P&L : Rs.{status['realized_pnl_inr']:,.2f}")
        print(f"  Unrealized P&L: Rs.{status['unrealized_pnl_inr']:,.2f}")
        print(f"  Total P&L     : Rs.{status['total_pnl_inr']:,.2f}")
        print(f"  Active Positions: {status['positions']}")

    print("\n--- Final Broker Portfolio Report ---")
    portfolio = paper_broker.get_portfolio()
    print(f"  Available Cash   : Rs.{portfolio['cash_inr']:,.2f}")
    print(f"  Net Asset Value  : Rs.{portfolio['net_asset_value_inr']:,.2f}")
    print(f"  Active Holdings  : {portfolio['positions']}")
    
    # 4. Verify P&L totals
    # Net P&L should be -Rs.2,000
    assert abs(scalper.total_realized_pnl - (-2000.0)) < 0.01, f"Expected P&L of -Rs.2,000, got: {scalper.total_realized_pnl}"
    print("\n[OK] Strategy and Paper Trading verification complete: ALL TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(run_simulation())
