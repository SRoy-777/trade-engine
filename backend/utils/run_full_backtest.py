import asyncio
import sys
import uuid
import csv
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

from models.market import MarketEvent
from core.broker.paper import PaperBroker
from core.strategy.orb import OpeningRangeBreakoutStrategy, TradingSignal
from providers.market.dhan.logger import dhan_logger

# =========================================================================
# CONFIGURABLE PARAMETERS
# Change these values to adjust the backtest scope and leverage settings
# =========================================================================
days = "180 days"        # Backtest duration: e.g. "180 days", "365 days", "10 days"
capital = 60000.0        # Demo fund in INR
product_type = "INTRADAY" # Trade Mode: "INTRADAY" (5x leverage) or "DELIVERY" (1x leverage)
symbol = "SBIN"
base_price = 1041.00     # Starting stock price for SBIN

def parse_days(days_str: str) -> int:
    """Helper to parse days from strings like '180 days'."""
    try:
        return int(days_str.split()[0])
    except Exception:
        return 180

async def run_simulation():
    num_days = parse_days(days)
    print(f"=== Running Full ORB Backtest ({num_days} Days) ===")
    print(f"  Demo Capital: Rs.{capital:,.2f}")
    print(f"  Trade Type  : {product_type}")
    print(f"  Asset       : {symbol} @ base Rs.{base_price:.2f}")
    
    # 1. Initialize broker with latency and product-specific settings
    broker = PaperBroker(initial_cash_inr=capital, latency_ms=50.0, product_type=product_type)
    
    # 2. Hook up strategy
    signals = []
    trades_log = []
    
    # Calculate leverage factor
    leverage = 5 if product_type.upper() == "INTRADAY" else 1
    
    # Define ORB strategy
    strategy = OpeningRangeBreakoutStrategy()
    await strategy.register_to_event_bus()

    # Callback to handle BUY signals and submit market orders to PaperBroker
    async def on_fill_update(fill: dict):
        # ORB Strategy is signal-driven and exits on market events (ticks)
        # Broker executes trades in parallel for tracking charges and slippages
        pass

    broker.register_fill_callback(on_fill_update)

    # 3. Generate multi-day simulated ticks for 180 days
    # This guarantees consistent and deterministic performance testing over the entire period
    print(f"\nGenerating simulated historical data for {num_days} days...")
    start_date = datetime.now() - timedelta(days=num_days)
    
    ticks = []
    for day_offset in range(num_days):
        current_date = start_date + timedelta(days=day_offset)
        # Skip weekends
        if current_date.weekday() >= 5:
            continue
            
        # Day Open: 09:15 Range Formation
        ticks.append((current_date.replace(hour=9, minute=15, second=0, microsecond=0), base_price - 1.0, 1000))
        ticks.append((current_date.replace(hour=9, minute=20, second=0, microsecond=0), base_price + 4.0, 1000))
        ticks.append((current_date.replace(hour=9, minute=30, second=0, microsecond=0), base_price + 2.0, 1000))
        
        # Day Breakout Check: 09:35 (Triggers BUY breakout)
        ticks.append((current_date.replace(hour=9, minute=35, second=0, microsecond=0), base_price + 5.0, 4000))
        
        # Exits simulation based on Day cycle index
        if day_offset % 3 == 0:
            # Day type A: Hit Target Profit (+2x range)
            ticks.append((current_date.replace(hour=10, minute=0, second=0, microsecond=0), base_price + 15.0, 1000))
        elif day_offset % 3 == 1:
            # Day type B: Hit Stop Loss (drops below range low)
            ticks.append((current_date.replace(hour=10, minute=15, second=0, microsecond=0), base_price - 3.0, 1000))
        else:
            # Day type C: Forced Intraday Square Off at 15:10
            ticks.append((current_date.replace(hour=12, minute=0, second=0, microsecond=0), base_price + 6.0, 1000))
            ticks.append((current_date.replace(hour=15, minute=10, second=0, microsecond=0), base_price + 7.0, 1000))

    # 4. Run ticks through the simulator
    print(f"Replaying {len(ticks)} ticks through strategy manager and broker...")
    
    for ts, price, vol in ticks:
        # Construct event packet
        event = MarketEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            correlation_id=f"pkt_{uuid.uuid4().hex[:8]}",
            exchange_timestamp=ts,
            received_timestamp=datetime.utcnow(),
            processed_timestamp=datetime.utcnow(),
            symbol=symbol,
            ltp=price,
            open=base_price,
            high=price,
            low=price,
            close=price,
            volume=vol,
            source_provider="Replay"
        )
        
        # Strategy analyzes the tick
        await strategy.on_market_event(event)
        
        # Update broker prices and fill checks
        # Create a mock Dhan MarketPacket for PaperBroker's matching engine
        from providers.market.dhan.models import MarketPacket
        mock_packet = MarketPacket(
            packet_type="Ticker",
            exchange_segment="NSE_EQ",
            security_id=symbol,
            ltp=price,
            volume=vol,
            timestamp=ts
        )
        await broker.on_tick(mock_packet)
        
        # If strategy triggered a new signal, submit it to PaperBroker using our demo fund position calculation
        if strategy.active_position and not strategy.active_position.get("submitted"):
            strategy.active_position["submitted"] = True
            entry_p = strategy.active_position["entry_price"]
            
            # Position Calculation: available capital with leverage / entry price
            qty = int((capital * leverage) / entry_p)
            
            if qty > 0:
                # Place order with broker
                await broker.submit_order({
                    "strategy_id": "orb_full",
                    "symbol": symbol,
                    "side": "BUY",
                    "qty": qty,
                    "price": entry_p,
                    "order_type": "MARKET"
                })
                
                signals.append({
                    "timestamp": ts.isoformat(),
                    "symbol": symbol,
                    "action": "BUY",
                    "price": entry_p,
                    "qty": qty,
                    "type": product_type
                })
        
        # Handle sell executions when strategy position exits
        if not strategy.active_position and len(broker._positions) > 0:
            pos_qty = broker._positions[symbol]["qty"]
            if pos_qty > 0:
                await broker.submit_order({
                    "strategy_id": "orb_full",
                    "symbol": symbol,
                    "side": "SELL",
                    "qty": pos_qty,
                    "price": price,
                    "order_type": "MARKET"
                })
                
                signals.append({
                    "timestamp": ts.isoformat(),
                    "symbol": symbol,
                    "action": "SELL",
                    "price": price,
                    "qty": pos_qty,
                    "type": product_type
                })

        await asyncio.sleep(0.0001)

    # 5. Export trades and fees to CSV
    report_path = backend_dir.parent / "market_data" / "full_backtest_report.csv"
    
    # Process analytics history
    records = strategy.analytics.get_all_records()
    
    # Track order fill metrics to match transaction charges
    broker_portfolio = broker.get_portfolio()
    
    with open(report_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Trade ID", "Symbol", "Product Type", "Entry Time", "Exit Time", 
            "Entry Price", "Exit Price", "Qty", "Gross P&L (INR)", "Taxes & Fees (INR)", "Net P&L (INR)"
        ])
        
        for idx, r in enumerate(records):
            # Dynamic position size matching
            qty = int((capital * leverage) / r.entry_price)
            gross_pnl = (r.exit_price - r.entry_price) * qty
            
            # Retrieve charges from broker history if available, else estimate standard charges
            fees = broker._calculate_transaction_charges("BUY", r.entry_price * qty) + \
                   broker._calculate_transaction_charges("SELL", r.exit_price * qty)
                   
            net_pnl = gross_pnl - fees
            
            writer.writerow([
                f"T_{idx+1:04d}", r.symbol, product_type, r.entry_time.isoformat(), r.exit_time.isoformat(),
                f"{r.entry_price:.2f}", f"{r.exit_price:.2f}", qty, f"{gross_pnl:.2f}", f"{fees:.2f}", f"{net_pnl:.2f}"
            ])

    print("\n================ BACKTEST COMPLETE ================")
    print(f"  Ending Cash NAV    : Rs.{broker_portfolio['net_asset_value_inr']:,.2f}")
    print(f"  Total Fees/Taxes   : Rs.{broker_portfolio['total_fees_paid_inr']:,.2f}")
    print(f"  Detailed Report CSV: {report_path.name}")
    print("===================================================")

if __name__ == "__main__":
    asyncio.run(run_simulation())
