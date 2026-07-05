import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
import sys
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

from core.broker.paper import PaperBroker
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.templates.day_trading import DayTradingTrendFollower
from core.strategy.templates.hft_scalper import HftMicroScalper
from storage_engine.csv_source import CSVReplaySource
from providers.market.dhan.models import MarketPacket

async def run_backtest():
    print("==================================================")
    # 1. Setup Data Source
    csv_path = backend_dir.parent / "market_data" / "historical_data.csv"
    if not csv_path.exists():
        print(f"Error: Historical CSV file not found at {csv_path}")
        return

    print(f"Loading historical data from: {csv_path.name}")
    csv_source = CSVReplaySource(str(csv_path))
    
    # 2. Setup Paper Broker (50 Lakh INR initial balance)
    broker = PaperBroker(initial_cash_inr=5000000.0)
    risk = RiskController(
        max_capital_per_trade_inr=30000000.0, # Increased to ₹3 Crore
        max_daily_loss_inr=20000.0,
        margin_leverage_multiplier=5.0
    )
    manager = StrategyManager(broker, risk)
    
    # 3. Choose and Register Strategy (HFT Micro Scalper)
    strategy = HftMicroScalper(
        strategy_id="backtest_scalp_01",
        name="HFT Scalper Backtest",
        symbols=["INFY"],
        target_profit_inr=1000.0,
        ticks_target=2
    )
    manager.register_strategy(strategy)

    print("\n--- Starting Historical Replay ---")
    packet_count = 0
    await csv_source.open()
    
    while True:
        row = await csv_source.read_next()
        if not row:
            break
            
        # Convert raw CSV row dictionary into MarketPacket
        # Expected CSV columns: timestamp, symbol, ltp, volume
        packet = MarketPacket(
            packet_type="Ticker",
            exchange_segment="NSE_EQ",
            security_id=row.get("symbol", "INFY"),
            ltp=float(row.get("ltp", 0.0)),
            volume=int(row.get("volume", 0)) if row.get("volume") else None,
            timestamp=datetime.strptime(row.get("timestamp"), "%Y-%m-%dT%H:%M:%S.%fZ") if "timestamp" in row and row.get("timestamp") else datetime.utcnow()
        )
        
        await manager.on_tick(packet)
        packet_count += 1
        # Brief yield to event loop to process async order fills
        await asyncio.sleep(0.001)

    await csv_source.close()

    print(f"Ingested {packet_count} ticks from history.")
    
    # 4. Print results
    print("\n================ BACKTEST REPORTS ================")
    status = strategy.get_status()
    print(f"Strategy Name : {status['name']}")
    print(f"Realized P&L  : Rs.{status['realized_pnl_inr']:,.2f}")
    print(f"Unrealized P&L: Rs.{status['unrealized_pnl_inr']:,.2f}")
    print(f"Total Net P&L : Rs.{status['total_pnl_inr']:,.2f}")
    print(f"Final Positions: {status['positions']}")
    
    portfolio = broker.get_portfolio()
    print("\nBroker Account Status:")
    print(f"  Ending Cash   : Rs.{portfolio['cash_inr']:,.2f}")
    print(f"  Net Asset Value: Rs.{portfolio['net_asset_value_inr']:,.2f}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(run_backtest())
