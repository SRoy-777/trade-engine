import os
import sys
import asyncio
import csv
from pathlib import Path

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

# Clear logger outputs for stress test console cleanliness
import logging
logging.getLogger().setLevel(logging.ERROR)

from utils.run_full_backtest import run_simulation

async def run_test_scenario(scenario_name: str, env_overrides: dict) -> dict:
    """Runs the simulation with environmental overrides and parses the output report."""
    # Apply overrides
    old_env = {}
    for k, v in env_overrides.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = str(v)

    # Force fallback output if report is locked
    os.environ["DATA_PROVIDER_TYPE"] = "DHAN"  # Use generator/feed mock to run quickly
    os.environ["BACKTEST_DAYS"] = "90 days"    # Use 90 days for speed in multi-run sweeps
    
    # Run backtest
    await run_simulation()

    # Restore overrides
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    # Read output report
    report_path = backend_dir.parent / "market_data" / "full_backtest_report.csv"
    if not report_path.exists():
        # Look for fallback timestamped report
        fallback_files = list((backend_dir.parent / "market_data").glob("full_backtest_report_*.csv"))
        if fallback_files:
            report_path = max(fallback_files, key=os.path.getctime)
        else:
            return {"name": scenario_name, "error": "Report not found"}

    trades = []
    with open(report_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "net_pnl": float(row.get("Net P&L (INR)", 0.0)),
                "slippage": float(row.get("Slippage Cost (Points)", 0.0)),
                "spread_cost": float(row.get("Spread Cost (Points)", 0.0)),
                "delay": float(row.get("Avg Delay (ms)", 0.0))
            })

    # Clean up fallback report if it was timestamped
    if "full_backtest_report_" in report_path.name:
        try:
            os.remove(report_path)
        except Exception:
            pass

    if not trades:
        return {
            "name": scenario_name,
            "total_trades": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "avg_slippage": 0.0,
            "avg_spread": 0.0,
            "avg_delay": 0.0
        }

    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    net_pnl = sum(t["net_pnl"] for t in trades)
    avg_slippage = sum(t["slippage"] for t in trades) / len(trades)
    avg_spread = sum(t["spread_cost"] for t in trades) / len(trades)
    avg_delay = sum(t["delay"] for t in trades) / len(trades)

    return {
        "name": scenario_name,
        "total_trades": len(trades),
        "win_rate": (wins / len(trades)) * 100.0,
        "net_pnl": net_pnl,
        "avg_slippage": avg_slippage,
        "avg_spread": avg_spread,
        "avg_delay": avg_delay
    }

async def main():
    print("==================================================================")
    print("=== STARTING SIMULATION STRESS TESTS (90-Day sweeps) ===")
    print("==================================================================")

    results = []

    # 1. Sweep Latencies
    print("\nSweeping Latency configurations...")
    for lat in [10, 50, 250, 500]:
        res = await run_test_scenario(
            scenario_name=f"Latency {lat}ms",
            env_overrides={"LATENCY_MS": lat}
        )
        results.append(res)

    # 2. Sweep Spreads
    print("\nSweeping Bid-Ask Spread models...")
    spreads = [
        ("No Spread", {"SPREAD_MODEL": "NONE", "SPREAD_VALUE": 0.0}),
        ("Fixed Spread (0.1 pt)", {"SPREAD_MODEL": "FIXED", "SPREAD_VALUE": 0.10}),
        ("Percentage (0.01%)", {"SPREAD_MODEL": "PERCENTAGE", "SPREAD_VALUE": 0.0001}),
        ("Dynamic Spread", {"SPREAD_MODEL": "DYNAMIC", "SPREAD_VALUE": 0.05})
    ]
    for name, envs in spreads:
        res = await run_test_scenario(scenario_name=name, env_overrides=envs)
        results.append(res)

    # 3. Sweep Slippages
    print("\nSweeping Slippage models...")
    slippages = [
        ("No Slippage", {"SLIPPAGE_MODEL": "NONE", "SLIPPAGE_VALUE": 0.0}),
        ("Fixed Ticks (1 tick)", {"SLIPPAGE_MODEL": "FIXED_TICKS", "SLIPPAGE_VALUE": 1.0}),
        ("Percentage (0.02%)", {"SLIPPAGE_MODEL": "PERCENTAGE", "SLIPPAGE_VALUE": 0.0002}),
        ("Random Noise (0.05)", {"SLIPPAGE_MODEL": "RANDOM", "SLIPPAGE_VALUE": 0.05})
    ]
    for name, envs in slippages:
        res = await run_test_scenario(scenario_name=name, env_overrides=envs)
        results.append(res)

    # 4. Sweep Multiple Stocks
    print("\nSweeping Multiple Stock Targets...")
    for symbol in ["SBIN", "RELIANCE", "TATASTEEL", "ICICIBANK"]:
        res = await run_test_scenario(
            scenario_name=f"Asset: {symbol}",
            env_overrides={"BACKTEST_SYMBOL": symbol}
        )
        results.append(res)

    # Compile COMPARISON REPORT
    print("\n\n" + "="*80)
    print("STRESS TEST COMPARATIVE PERFORMANCE REPORT")
    print("="*80)
    print(f"{'Scenario Name':<25} | {'Trades':<6} | {'Win%':<6} | {'Net P&L (INR)':<14} | {'Slippage':<8} | {'Spread':<8} | {'Delay':<8}")
    print("-"*80)
    
    for r in results:
        if "error" in r:
            print(f"{r['name']:<25} | Error: {r['error']}")
            continue
        print(f"{r['name']:<25} | {r['total_trades']:<6d} | {r['win_rate']:<5.1f}% | Rs.{r['net_pnl']:<10.2f} | {r['avg_slippage']:<8.4f} | {r['avg_spread']:<8.4f} | {r['avg_delay']:<5.1f}ms")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
