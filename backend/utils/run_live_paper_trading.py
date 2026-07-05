import sys
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

# Set logging levels
logging.getLogger("trade_engine.dhan").setLevel(logging.INFO)
logging.getLogger("websockets").setLevel(logging.WARNING)

from providers.market.dhan.market_provider import DhanMarketProvider
from core.broker.paper import PaperBroker
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.templates.hft_scalper import HftMicroScalper
from providers.market.dhan.models import MarketPacket

# Configurable constants for your budget
INITIAL_CASH_INR = 60000.0          # ₹60,000 capital
TARGET_PROFIT_INR = 500.0           # ₹500 Target Profit
TICKS_TARGET = 100                  # Target ticks (100 ticks * ₹0.05 = ₹5.00 price change)
# At ₹5.00 price change, required position size = 500 / 5.00 = 100 shares.
# Exposure for 100 shares of Tata Motors (at ₹980) = ₹98,000.
# At 5x intraday margin leverage, ₹98,000 only requires ₹19,600 in cash, fitting your ₹60,000 budget!

async def main():
    print("==================================================")
    print("      LAUNCHING LIVE PAPER TRADING ENGINE         ")
    print("==================================================")
    print(f"Initial virtual cash: Rs.{INITIAL_CASH_INR:,.2f}")
    
    # 1. Initialize broker & pre-trade risk controller
    broker = PaperBroker(initial_cash_inr=INITIAL_CASH_INR, latency_ms=50.0)
    risk = RiskController(
        max_capital_per_trade_inr=200000.0,  # Max ₹2 Lakh per trade (suits your ₹60k capital)
        max_daily_loss_inr=5000.0,           # Stop trading if loss exceeds ₹5,000 today
        margin_leverage_multiplier=5.0       # 5x intraday margin leverage
    )
    
    # 2. Setup strategy coordinator
    manager = StrategyManager(broker, risk)
    
    # 3. Register your HFT Micro Scalper strategy on Tata Motors
    # NSE token for Tata Motors EQ is typically "3456" (or check your Dhan dashboard).
    # You can configure the token and symbol here
    strategy = HftMicroScalper(
        strategy_id="live_scalp_01",
        name="Tata Motors Micro Scalper",
        symbols=["TATAMOTORS"],
        target_profit_inr=TARGET_PROFIT_INR,
        ticks_target=TICKS_TARGET,
        capital_limit=60000.0
    )
    manager.register_strategy(strategy)

    # 4. Initialize the live Dhan WebSocket feed provider
    live_feed = DhanMarketProvider()
    
    # Connect live feed ticks to our strategy manager callback
    live_feed.set_packet_callback(manager.on_tick)

    # 5. Start live session
    print("\nConnecting to Dhan live WebSocket server...")
    try:
        await live_feed.start()
        
        # Keep running to process live feed ticks
        print("Live Paper Trading Active! Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1.0)
            
            # Print a periodic performance report every 60 seconds
            status = strategy.get_status()
            portfolio = broker.get_portfolio()
            print(
                f"\n[REPORT] Net P&L: Rs.{status['total_pnl_inr']:.2f} | "
                f"Taxes Paid: Rs.{portfolio['total_fees_paid_inr']:.2f} | "
                f"Current Cash: Rs.{portfolio['cash_inr']:.2f}"
            )
            
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopping Live Paper Trading Engine...")
    finally:
        await live_feed.stop()
        print("Engine stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
