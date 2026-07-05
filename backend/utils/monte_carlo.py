import sys
import csv
import random
import numpy as np
from pathlib import Path

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

def run_monte_carlo(trades_pnl: list, initial_capital: float = 60000.0, num_simulations: int = 10000) -> dict:
    """Performs Monte Carlo simulation by shuffling trade PnL outcomes randomly."""
    if not trades_pnl:
        return {}

    num_trades = len(trades_pnl)
    sim_returns = []
    sim_max_drawdowns = []
    ruin_count = 0
    ruin_threshold = initial_capital * 0.5  # Ruin if capital drops by 50%

    for _ in range(num_simulations):
        # Bootstrap: draw samples randomly with replacement
        shuffled = random.choices(trades_pnl, k=num_trades)
        
        # Calculate capital curve
        capital = initial_capital
        peak = initial_capital
        max_dd = 0.0
        ruined = False
        
        for pnl in shuffled:
            capital += pnl
            if capital < ruin_threshold:
                ruined = True
            if capital > peak:
                peak = capital
            dd = peak - capital
            if dd > max_dd:
                max_dd = dd

        sim_returns.append(capital - initial_capital)
        sim_max_drawdowns.append(max_dd)
        if ruined:
            ruin_count += 1

    sim_returns = np.array(sim_returns)
    sim_max_drawdowns = np.array(sim_max_drawdowns)

    # Sort results to get percentile confidence intervals
    sorted_returns = np.sort(sim_returns)
    ci_95_low = sorted_returns[int(num_simulations * 0.025)]
    ci_95_high = sorted_returns[int(num_simulations * 0.975)]

    return {
        "expected_return_mean": np.mean(sim_returns),
        "expected_return_median": np.median(sim_returns),
        "worst_drawdown_mean": np.mean(sim_max_drawdowns),
        "worst_drawdown_max": np.max(sim_max_drawdowns),
        "probability_of_ruin": (ruin_count / num_simulations) * 100.0,
        "ci_95_low": ci_95_low,
        "ci_95_high": ci_95_high
    }

def main():
    report_path = backend_dir.parent / "market_data" / "full_backtest_report.csv"
    if not report_path.exists():
        print(f"Error: Backtest report CSV not found at {report_path}. Run a backtest first.")
        sys.exit(1)

    # Load trades P&L
    pnl_outcomes = []
    initial_capital = 60000.0
    
    with open(report_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pnl_outcomes.append(float(row.get("Net P&L (INR)", 0.0)))
            initial_capital = float(os.getenv("BACKTEST_CAPITAL", "60000.0"))

    if not pnl_outcomes:
        print("Error: No executed trades found in the backtest report.")
        sys.exit(1)

    print(f"=== Running Monte Carlo Simulation (10,000 runs) ===")
    print(f"  Sample Trades Count: {len(pnl_outcomes)}")
    print(f"  Starting Capital   : Rs.{initial_capital:,.2f}")

    results = run_monte_carlo(pnl_outcomes, initial_capital, 10000)

    print("\n================ MONTE CARLO ANALYSIS RESULTS ================")
    print(f"  Expected Return (Mean)  : Rs.{results['expected_return_mean']:,.2f}")
    print(f"  Expected Return (Median): Rs.{results['expected_return_median']:,.2f}")
    print(f"  Worst Drawdown (Mean)   : Rs.{results['worst_drawdown_mean']:,.2f}")
    print(f"  Worst Drawdown (Max)    : Rs.{results['worst_drawdown_max']:,.2f}")
    print(f"  Probability of Ruin     : {results['probability_of_ruin']:.2f}% (Threshold: 50% capital drop)")
    print(f"  95% Confidence Interval : [Rs.{results['ci_95_low']:,.2f} to Rs.{results['ci_95_high']:,.2f}]")
    print("===============================================================")

if __name__ == "__main__":
    import os
    main()
