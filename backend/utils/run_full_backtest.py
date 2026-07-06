import os
import sys
import csv
import yaml
import asyncio
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, date

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from core.broker.paper import PaperBroker, SimulationConfig
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.ema_pullback import EMAPullbackStrategy
from providers.market.dhan.models import MarketPacket
from utils.logger_setup import logger

async def run_simulation():
    # Setup paths
    config_path = backend_dir.parent / "configs" / "ema_pullback.yaml"

    print(f"Loading config from: {config_path}")
    if not config_path.exists():
        print(f"Error: Config not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    timeframe = config.get("timeframe", "1m")
    if timeframe == "5m":
        csv_name = "SBIN_3y_5min.csv"
    else:
        csv_name = "SBIN_3y_1m.csv"

    csv_path = backend_dir.parent / "market_data" / "history" / csv_name

    print(f"Loading historical data from: {csv_path}")
    if not csv_path.exists():
        print(f"Error: Historical data file not found at {csv_path}.")
        return

    capital = float(config.get("capital", 60000.0))
    leverage = float(config.get("leverage", 5.0))
    symbol = config.get("symbol", "SBIN")

    # Set up simulated paper broker
    sim_cfg = SimulationConfig()
    sim_cfg.LATENCY_MS = 50
    sim_cfg.SPREAD_MODEL = "FIXED"
    sim_cfg.SPREAD_VALUE = 0.05       # 1 tick bid-ask spread
    sim_cfg.SLIPPAGE_MODEL = "FIXED_TICKS"
    sim_cfg.SLIPPAGE_VALUE = 1.0      # 1 tick adverse slippage
    sim_cfg.MARGIN_MULTIPLIER = leverage
    sim_cfg.LOT_SIZE = 1
    sim_cfg.MIN_QTY = 1

    broker = PaperBroker(initial_cash_inr=capital, product_type="INTRADAY", sim_cfg=sim_cfg)
    risk = RiskController(
        max_capital_per_trade_inr=capital * leverage * 1.5,
        max_daily_loss_inr=capital * 2.0, # Disable loss limit blocks during full backtest
        margin_leverage_multiplier=leverage
    )
    manager = StrategyManager(broker, risk)

    # Initialize strategy
    strategy = EMAPullbackStrategy(str(config_path))
    manager.register_strategy(strategy)

    # Open historical CSV
    print("Reading CSV records and starting ingestion simulation...")
    
    # Store daily equity states for Sharpe/Sortino calculations
    daily_equity = []
    last_logged_date = None

    # Load rows first to show progress (filtered by date)
    start_date_str = config.get("start_date", "2023-07-07")
    end_date_str = config.get("end_date", "2026-07-06")
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    rows = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ts_str = r["timestamp"]
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", ""))
            except ValueError:
                try:
                    ts = datetime.strptime(ts_str.split("+")[0], "%Y-%m-%dT%H:%M:%S.%f")
                except ValueError:
                    ts = datetime.strptime(ts_str.split("+")[0], "%Y-%m-%dT%H:%M:%S")
            ts_date = ts.date()
            if start_dt <= ts_date <= end_dt:
                r["datetime_parsed"] = ts
                rows.append(r)

    print(f"Total bars to process: {len(rows)}")
    ts = datetime.combine(start_dt, datetime.min.time())
    
    # Process ticks
    step_size = max(1, len(rows) // 10)
    for idx, r in enumerate(rows):
        if idx % step_size == 0 or idx == len(rows) - 1:
            print(f"  Processed {idx}/{len(rows)} bars ({int((idx/len(rows))*100)}%)...")

        # Get pre-parsed timestamp
        ts = r["datetime_parsed"]

        # Construct market packet
        packet = MarketPacket(
            packet_type="Quote",
            exchange_segment="NSE_EQ",
            security_id=symbol,
            ltp=float(r["close"]),
            volume=int(r["volume"]),
            timestamp=ts,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"])
        )

        # Feed tick to Strategy Manager
        await manager.on_tick(packet)
        if len(broker._pending_orders) > 0 or strategy.pending_entry is not None or (strategy.active_trade is not None and strategy.active_trade.get("exit_order_pending")):
            await asyncio.sleep(0.0001)

        # Track daily equity state (at the start of each new day)
        curr_date = ts.date()
        if last_logged_date is None or curr_date > last_logged_date:
            portfolio = broker.get_portfolio()
            daily_equity.append({
                "Date": curr_date,
                "Timestamp": ts.isoformat(),
                "Net_Asset_Value": portfolio["net_asset_value_inr"],
                "Cash": portfolio["cash_inr"]
            })
            last_logged_date = curr_date

    # Force square off at the very end of backtest if still open
    if strategy.active_trade is not None:
        last_row = rows[-1]
        ts_str = last_row["timestamp"]
        ts = datetime.fromisoformat(ts_str.replace("Z", ""))
        packet = MarketPacket(
            packet_type="Quote",
            exchange_segment="NSE_EQ",
            security_id=symbol,
            ltp=float(last_row["close"]),
            volume=int(last_row["volume"]),
            timestamp=ts,
            open=float(last_row["open"]),
            high=float(last_row["high"]),
            low=float(last_row["low"]),
            close=float(last_row["close"])
        )
        print("Forcing final square-off of open backtest position...")
        await strategy._close_position(packet, "Final Backtest Close")
        # Trigger broker tick to process order matching
        await broker.on_tick(packet)

    # Append final equity state
    portfolio = broker.get_portfolio()
    final_date = ts.date()
    # Check if final state date is already in daily_equity to avoid duplicates
    if not daily_equity or daily_equity[-1]["Date"] != final_date:
        daily_equity.append({
            "Date": final_date,
            "Timestamp": ts.isoformat(),
            "Net_Asset_Value": portfolio["net_asset_value_inr"],
            "Cash": portfolio["cash_inr"]
        })

    # Save results
    await compile_and_save_reports(strategy, daily_equity, capital)

async def compile_and_save_reports(strategy: EMAPullbackStrategy, daily_equity: list, initial_capital: float):
    print("\n=== Compiling Backtest Reports ===")
    
    trades = strategy.trade_history
    results_dir = backend_dir.parent / "market_data"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Save Trade Log CSV
    trade_log_path = results_dir / "trade_log.csv"
    trade_headers = ["Trade_ID", "Symbol", "Direction", "Setup", "Entry_Time", "Entry_Price", "Qty", "Exit_Time", "Exit_Price", "Gross_PnL", "Fees", "Net_PnL", "Exit_Reason", "Hold_Time_Mins", "Entry_Candle_Volume", "Prev_Candle_Direction", "Trade_Trend", "Trade_Type"]
    with open(trade_log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_headers)
        writer.writeheader()
        writer.writerows(trades)
    print(f"  Saved Trade Log to: {trade_log_path}")

    # Also save to c:\Projects\trade-engine\market_data\full_backtest_report.csv for Monte Carlo compatibility
    # Monte Carlo expects 'Net P&L (INR)' header
    mc_report_path = results_dir / "full_backtest_report.csv"
    with open(mc_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Trade_ID", "Net P&L (INR)"])
        for t in trades:
            writer.writerow([t["Trade_ID"], t["Net_PnL"]])
    print(f"  Saved Monte Carlo backtest file to: {mc_report_path}")

    # 2. Save Equity Curve & Drawdown CSVs
    equity_curve_path = results_dir / "equity_curve.csv"
    drawdown_path = results_dir / "drawdown.csv"
    
    peak_nav = initial_capital
    equity_rows = []
    drawdown_rows = []
    
    for record in daily_equity:
        nav = record["Net_Asset_Value"]
        cash = record["Cash"]
        timestamp = record["Timestamp"]
        
        equity_rows.append({
            "Timestamp": timestamp,
            "Net_Asset_Value": nav,
            "Cash": cash
        })
        
        peak_nav = max(peak_nav, nav)
        dd_inr = peak_nav - nav
        dd_pct = (dd_inr / peak_nav) * 100.0
        
        drawdown_rows.append({
            "Timestamp": timestamp,
            "NAV": nav,
            "Peak_NAV": peak_nav,
            "Drawdown_INR": dd_inr,
            "Drawdown_Percent": dd_pct
        })
        
    with open(equity_curve_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Timestamp", "Net_Asset_Value", "Cash"])
        writer.writeheader()
        writer.writerows(equity_rows)
    print(f"  Saved Equity Curve to: {equity_curve_path}")

    with open(drawdown_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Timestamp", "NAV", "Peak_NAV", "Drawdown_INR", "Drawdown_Percent"])
        writer.writeheader()
        writer.writerows(drawdown_rows)
    print(f"  Saved Drawdown Log to: {drawdown_path}")

    # 3. Calculate Monthly Returns CSV
    monthly_returns_path = results_dir / "monthly_returns.csv"
    monthly_data = {}
    for record in daily_equity:
        dt = datetime.fromisoformat(record["Timestamp"])
        year_month = (dt.year, dt.month)
        if year_month not in monthly_data:
            monthly_data[year_month] = []
        monthly_data[year_month].append(record["Net_Asset_Value"])
        
    monthly_rows = []
    prev_month_close = initial_capital
    sorted_months = sorted(monthly_data.keys())
    
    for (year, month) in sorted_months:
        month_vals = monthly_data[(year, month)]
        month_close = month_vals[-1]
        
        monthly_return_inr = month_close - prev_month_close
        monthly_return_pct = (monthly_return_inr / prev_month_close) * 100.0
        
        monthly_rows.append({
            "Year": year,
            "Month": month,
            "Monthly_Return_INR": monthly_return_inr,
            "Monthly_Return_Percent": monthly_return_pct
        })
        prev_month_close = month_close
        
    with open(monthly_returns_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Year", "Month", "Monthly_Return_INR", "Monthly_Return_Percent"])
        writer.writeheader()
        writer.writerows(monthly_rows)
    print(f"  Saved Monthly Returns to: {monthly_returns_path}")

    # 4. Compute Performance Metrics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["Net_PnL"] > 0]
    losing_trades = [t for t in trades if t["Net_PnL"] <= 0]
    
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_profit = sum(t["Net_PnL"] for t in winning_trades)
    gross_loss = abs(sum(t["Net_PnL"] for t in losing_trades))
    
    total_fees = sum(t["Fees"] for t in trades)
    net_profit = gross_profit - gross_loss # Note: net pnl already subtracts fees, this matches sum of Net_PnL
    
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    
    avg_winner = (gross_profit / len(winning_trades)) if winning_trades else 0.0
    avg_loser = -(gross_loss / len(losing_trades)) if losing_trades else 0.0
    
    expectancy = (win_rate / 100.0 * avg_winner) + ((1 - win_rate / 100.0) * avg_loser)
    
    largest_winner = max([t["Net_PnL"] for t in winning_trades]) if winning_trades else 0.0
    largest_loser = min([t["Net_PnL"] for t in losing_trades]) if losing_trades else 0.0
    
    avg_holding_time = sum(t["Hold_Time_Mins"] for t in trades) / total_trades if total_trades > 0 else 0.0

    # Calculate Sharpe, Sortino, Calmar
    nav_series = [r["Net_Asset_Value"] for r in daily_equity]
    daily_returns = []
    for i in range(1, len(nav_series)):
        prev = nav_series[i-1]
        if prev > 0:
            daily_returns.append((nav_series[i] - prev) / prev)
        else:
            daily_returns.append(0.0)
            
    mean_daily_return = np.mean(daily_returns) if daily_returns else 0.0
    std_daily_return = np.std(daily_returns) if daily_returns else 0.0
    
    sharpe = (np.sqrt(252) * (mean_daily_return / std_daily_return)) if std_daily_return > 0 else 0.0
    
    downside_returns = [r for r in daily_returns if r < 0]
    downside_std = np.std(downside_returns) if downside_returns else 0.0
    sortino = (np.sqrt(252) * (mean_daily_return / downside_std)) if downside_std > 0 else 0.0
    
    max_dd_pct = max([r["Drawdown_Percent"] for r in drawdown_rows]) if drawdown_rows else 0.0
    total_days = (daily_equity[-1]["Date"] - daily_equity[0]["Date"]).days if len(daily_equity) > 1 else 1
    annualized_return = ((nav_series[-1] / initial_capital) ** (365 / total_days) - 1) if total_days > 0 else 0.0
    
    calmar = (annualized_return / (max_dd_pct / 100.0)) if max_dd_pct > 0 else 0.0

    # Setup specific metrics
    setup_a_trades = [t for t in trades if t["Setup"] == "Setup A"]
    setup_b_trades = [t for t in trades if t["Setup"] == "Setup B"]
    
    def get_setup_stats(setup_trades):
        t_count = len(setup_trades)
        wins = [t for t in setup_trades if t["Net_PnL"] > 0]
        losses = [t for t in setup_trades if t["Net_PnL"] <= 0]
        w_rate = (len(wins) / t_count * 100.0) if t_count > 0 else 0.0
        
        g_profit = sum(t["Net_PnL"] for t in wins)
        g_loss = abs(sum(t["Net_PnL"] for t in losses))
        p_factor = (g_profit / g_loss) if g_loss > 0 else (g_profit if g_profit > 0 else 0.0)
        
        a_winner = (g_profit / len(wins)) if wins else 0.0
        a_loser = -(g_loss / len(losses)) if losses else 0.0
        exp = (w_rate / 100.0 * a_winner) + ((1 - w_rate / 100.0) * a_loser)
        
        return t_count, w_rate, p_factor, exp

    t_a, wr_a, pf_a, exp_a = get_setup_stats(setup_a_trades)
    t_b, wr_b, pf_b, exp_b = get_setup_stats(setup_b_trades)

    # 5. Save Performance Summary CSV
    performance_summary_path = results_dir / "performance_summary.csv"
    summary_data = {
        "Total Trades": total_trades,
        "Winning Trades": len(winning_trades),
        "Losing Trades": len(losing_trades),
        "Win Rate (%)": win_rate,
        "Gross Profit (INR)": gross_profit,
        "Gross Loss (INR)": gross_loss,
        "Taxes & Brokerage (INR)": total_fees,
        "Net Profit (INR)": net_profit,
        "Profit Factor": profit_factor,
        "Expectancy (INR)": expectancy,
        "Sharpe Ratio": sharpe,
        "Sortino Ratio": sortino,
        "Calmar Ratio": calmar,
        "Maximum Drawdown (%)": max_dd_pct,
        "Average Winner (INR)": avg_winner,
        "Average Loser (INR)": avg_loser,
        "Average Holding Time (Mins)": avg_holding_time,
        "Largest Winner (INR)": largest_winner,
        "Largest Loser (INR)": largest_loser,
        "Setup A (EMA100) Trades": t_a,
        "Setup A Win Rate (%)": wr_a,
        "Setup A Profit Factor": pf_a,
        "Setup A Expectancy": exp_a,
        "Setup B (EMA200) Trades": t_b,
        "Setup B Win Rate (%)": wr_b,
        "Setup B Profit Factor": pf_b,
        "Setup B Expectancy": exp_b
    }
    
    with open(performance_summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Metric", "Value"])
        for k, v in summary_data.items():
            writer.writerow([k, v])
    print(f"  Saved Performance Summary to: {performance_summary_path}")

    # Output detailed report on console
    print("\n" + "="*50)
    print("      BACKTEST PERFORMANCE REPORT (3-YEARS)")
    print("="*50)
    for k, v in summary_data.items():
        if isinstance(v, float):
            print(f"  {k:<30}: {v:,.2f}")
        else:
            print(f"  {k:<30}: {v}")
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(run_simulation())
