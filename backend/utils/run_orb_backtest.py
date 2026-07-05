import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

from event_bus.event_bus import event_bus
from models.market import MarketEvent
from core.strategy.orb import OpeningRangeBreakoutStrategy, TradingSignal
from storage_engine.csv_source import CSVReplaySource

async def run_orb_backtest():
    print("=== STARTING PHASE 3 ORB STRATEGY BACKTEST ===")
    
    # 1. Initialize Strategy and register signals callback
    signals: list[TradingSignal] = []
    
    def on_signal(signal: TradingSignal):
        signals.append(signal)
        print(
            f"  [SIGNAL EMITTED] {signal.timestamp} - {signal.symbol}: "
            f"{signal.reason} | Entry: Rs.{signal.entry_price:.2f} | "
            f"SL: Rs.{signal.stop_loss:.2f} | TP Target: Rs.{signal.target:.2f}"
        )

    strategy = OpeningRangeBreakoutStrategy(signal_callback=on_signal)
    await strategy.register_to_event_bus()

    # 2. Setup Data Source
    csv_path = backend_dir.parent / "market_data" / "sbin_multiday_replay.csv"
    if not csv_path.exists():
        print(f"Error: Replay CSV not found at {csv_path}")
        return

    csv_source = CSVReplaySource(str(csv_path))
    await csv_source.open()

    # 3. Replay ticks through the Event Bus
    packet_count = 0
    print("\n--- Feeding Replay Ticks to Event Bus ---")
    
    while True:
        row = await csv_source.read_next()
        if not row:
            break
            
        # Ignore comments/header lines in parser
        if row.get("symbol") == "symbol" or not row.get("ltp"):
            continue
            
        timestamp_str = row.get("timestamp")
        # Handle simple comment cleaning if present
        if timestamp_str.startswith("#"):
            continue

        event = MarketEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            correlation_id=f"pkt_{uuid.uuid4().hex[:8]}",
            exchange_timestamp=datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ"),
            received_timestamp=datetime.utcnow(),
            processed_timestamp=datetime.utcnow(),
            symbol=row.get("symbol", "SBIN"),
            ltp=float(row.get("ltp", 0.0)),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=int(row.get("volume", 0)),
            source_provider="ReplayReplay"
        )
        
        # Publish event to the Event Bus (triggers the strategy)
        await event_bus.publish(event)
        packet_count += 1
        
        # yield to allow loop to process callbacks
        await asyncio.sleep(0.001)

    await csv_source.close()
    print(f"\nIngested {packet_count} ticks from history.")

    # 4. Compile and verify results
    summary = strategy.analytics.compile_summary()
    print("\n================ ORB BACKTEST PERFORMANCE SUMMARY ================")
    print(f"  Total Trades        : {summary['total_trades']}")
    print(f"  Winning Trades      : {summary['wins']}")
    print(f"  Losing Trades       : {summary['losses']}")
    print(f"  Win Rate Percentage : {summary['win_rate_pct']}%")
    print(f"  Net P&L Points      : {summary['net_pnl_points']} pts")
    print(f"  Average Hold Time   : {summary['avg_hold_time_secs']} seconds")
    print(f"  Max Favourable Excur: {summary['max_mfe']} pts")
    print(f"  Max Adverse Excur   : {summary['max_mae']} pts")
    print("==================================================================")

    # Asserts to confirm deterministic results
    assert len(signals) == 3, f"Expected 3 signals, got: {len(signals)}"
    assert summary['total_trades'] == 3, f"Expected 3 records, got: {summary['total_trades']}"
    assert summary['wins'] == 2, f"Expected 2 wins (Day 1 Target, Day 3 Square Off points positive), got: {summary['wins']}"
    assert summary['losses'] == 1, f"Expected 1 loss (Day 2 SL), got: {summary['losses']}"
    
    # 5. Export results to CSV files
    import csv
    
    signals_csv_path = backend_dir.parent / "market_data" / "orb_signals_output.csv"
    with open(signals_csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["signal_id", "timestamp", "symbol", "entry_price", "stop_loss", "target", "reason", "risk_reward"])
        for s in signals:
            writer.writerow([s.signal_id, s.timestamp.isoformat(), s.symbol, s.entry_price, s.stop_loss, s.target, s.reason, s.risk_reward])
            
    trades_csv_path = backend_dir.parent / "market_data" / "orb_trades_output.csv"
    with open(trades_csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "entry_time", "exit_time", "entry_price", "exit_price", "holding_time_secs", "pnl", "mfe", "mae", "exit_reason"])
        for r in strategy.analytics.get_all_records():
            writer.writerow([r.symbol, r.entry_time.isoformat(), r.exit_time.isoformat(), r.entry_price, r.exit_price, r.holding_time_secs, r.pnl, r.mfe, r.mae, r.exit_reason])
            
    print(f"\nExported signals to: {signals_csv_path.name}")
    print(f"Exported trade analytics to: {trades_csv_path.name}")
    print("\n[OK] ORB Strategy backtest verification complete: ALL TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(run_orb_backtest())
