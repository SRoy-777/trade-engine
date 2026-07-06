import os
import sys
import yaml
import csv
import asyncio
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from core.broker.paper import PaperBroker, SimulationConfig
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from core.strategy.orb import ORBStrategy
from providers.market.dhan.models import MarketPacket
from utils.logger_setup import logger

async def run_simulation():
    config_path = backend_dir.parent / "configs" / "orb.yaml"

    print(f"Loading ORB config from: {config_path}")
    if not config_path.exists():
        print(f"Error: ORB config not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    capital = float(config.get("capital", 60000.0))
    leverage = float(config.get("leverage", 5.0))
    symbol = config.get("symbol", "SBIN")
    timeframe = config.get("timeframe", "5m")

    # Set historical CSV path
    if timeframe == "5m":
        csv_name = "SBIN_3y_5min.csv"
    else:
        csv_name = "SBIN_3y_1m.csv"

    csv_path = backend_dir.parent / "market_data" / "history" / csv_name
    print(f"Loading historical data from: {csv_path}")
    if not csv_path.exists():
        print(f"Error: Historical data file not found at {csv_path}.")
        return

    # Broker & Risk Setup
    sim_cfg = SimulationConfig()
    sim_cfg.LATENCY_MS = 50
    sim_cfg.SPREAD_MODEL = "FIXED"
    sim_cfg.SPREAD_VALUE = 0.05
    sim_cfg.SLIPPAGE_MODEL = "FIXED_TICKS"
    sim_cfg.SLIPPAGE_VALUE = 1.0
    sim_cfg.MARGIN_MULTIPLIER = leverage
    sim_cfg.LOT_SIZE = 1
    sim_cfg.MIN_QTY = 1

    broker = PaperBroker(initial_cash_inr=capital, product_type="INTRADAY", sim_cfg=sim_cfg)
    risk = RiskController(
        max_capital_per_trade_inr=capital * leverage * 1.5,
        max_daily_loss_inr=capital * 2.0,
        margin_leverage_multiplier=leverage
    )
    manager = StrategyManager(broker, risk)

    # Initialize ORB strategy
    strategy = ORBStrategy(str(config_path))
    manager.register_strategy(strategy)

    print("Reading CSV records and starting ingestion simulation...")
    
    # Store daily equity states
    daily_equity = []
    last_logged_date = None

    # Load and filter rows by start/end dates
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

        ts = r["datetime_parsed"]
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

        # Feed tick
        await manager.on_tick(packet)
        # Yield to event loop if orders are pending
        if len(broker._pending_orders) > 0 or strategy.pending_entry is not None or (strategy.active_trade is not None and strategy.active_trade.get("exit_order_pending")):
            await asyncio.sleep(0.0001)

        # Log daily equity
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

    # Force square off at end
    if strategy.active_trade is not None:
        last_row = rows[-1]
        ts = last_row["datetime_parsed"]
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
        await broker.on_tick(packet)

    portfolio = broker.get_portfolio()
    final_date = ts.date()
    if not daily_equity or daily_equity[-1]["Date"] != final_date:
        daily_equity.append({
            "Date": final_date,
            "Timestamp": ts.isoformat(),
            "Net_Asset_Value": portfolio["net_asset_value_inr"],
            "Cash": portfolio["cash_inr"]
        })

    # Save results to distinct folder
    output_dir = backend_dir.parent / config.get("output_dir", "market_data/orb")
    await compile_and_save_reports(strategy, daily_equity, capital, output_dir)

async def compile_and_save_reports(strategy: ORBStrategy, daily_equity: list, initial_capital: float, output_dir: Path):
    print(f"\n=== Compiling ORB Backtest Reports inside: {output_dir} ===")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    def safe_to_csv(df, path):
        import time
        curr_path = path
        for i in range(10):
            try:
                df.to_csv(curr_path, index=False)
                if curr_path != path:
                    print(f"  Warning: Original path locked. Saved report to: {curr_path}")
                return
            except PermissionError:
                curr_path = path.parent / f"{path.stem}_{int(time.time()) + i}{path.suffix}"
        print(f"  Error: Failed to save to {path} (Permission Denied)")

    def safe_open_write(path, mode="w", newline="", encoding="utf-8"):
        import time
        curr_path = path
        for i in range(10):
            try:
                f = open(curr_path, mode, newline=newline, encoding=encoding)
                if curr_path != path:
                    print(f"  Warning: Original path locked. Saved report to: {curr_path}")
                return f
            except PermissionError:
                curr_path = path.parent / f"{path.stem}_{int(time.time()) + i}{path.suffix}"
        raise PermissionError(f"Failed to open/write {path} after retries")

    trades = strategy.trade_history
    
    # 1. Save Trade Log CSV
    trade_log_path = output_dir / "trade_log.csv"
    trade_headers = [
        "Trade_ID", "Symbol", "Direction", "Setup", "Entry_Time", "Entry_Price", 
        "Qty", "Exit_Time", "Exit_Price", "Gross_PnL", "Fees", "Net_PnL", 
        "Exit_Reason", "Hold_Time_Mins", "Entry_Candle_Volume", 
        "Prev_Candle_Direction", "Trade_Trend", "Trade_Type"
    ]
    with safe_open_write(trade_log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_headers)
        writer.writeheader()
        writer.writerows(trades)

    # 2. Equity Curve CSV
    equity_df = pd.DataFrame(daily_equity)
    safe_to_csv(equity_df, output_dir / "equity_curve.csv")

    # 3. Compile Performance Metrics
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t["Net_PnL"] > 0])
    losing_trades = len([t for t in trades if t["Net_PnL"] <= 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    gross_profit = sum(t["Net_PnL"] for t in trades if t["Net_PnL"] > 0)
    gross_loss = sum(t["Net_PnL"] for t in trades if t["Net_PnL"] <= 0)
    net_profit = sum(t["Net_PnL"] for t in trades)
    
    fees_paid = sum(t["Fees"] for t in trades)
    profit_factor = (abs(gross_profit / gross_loss) if gross_loss != 0 else np.nan) if total_trades > 0 else 0.0
    expectancy = (net_profit / total_trades) if total_trades > 0 else 0.0

    # Drawdown Log
    equity_df["Peak"] = equity_df["Net_Asset_Value"].cummax()
    equity_df["Drawdown_INR"] = equity_df["Peak"] - equity_df["Net_Asset_Value"]
    equity_df["Drawdown_Pct"] = (equity_df["Drawdown_INR"] / equity_df["Peak"]) * 100
    max_dd_pct = equity_df["Drawdown_Pct"].max()
    
    safe_to_csv(equity_df[["Date", "Net_Asset_Value", "Peak", "Drawdown_INR", "Drawdown_Pct"]], output_dir / "drawdown.csv")

    # Monthly Returns calculation
    equity_df["Year_Month"] = pd.to_datetime(equity_df["Timestamp"]).dt.to_period("M")
    monthly_ret = []
    for grp, df_grp in equity_df.groupby("Year_Month"):
        start_val = df_grp["Net_Asset_Value"].iloc[0]
        end_val = df_grp["Net_Asset_Value"].iloc[-1]
        m_ret = ((end_val - start_val) / start_val) * 100
        monthly_ret.append({"Month": str(grp), "Return_Pct": m_ret, "Net_Profit_INR": end_val - start_val})
    safe_to_csv(pd.DataFrame(monthly_ret), output_dir / "monthly_returns.csv")

    # Quantitative Sharpe/Sortino Ratios
    # Daily returns based on Net_Asset_Value
    equity_df["Daily_Return"] = equity_df["Net_Asset_Value"].pct_change().fillna(0)
    mean_ret = equity_df["Daily_Return"].mean()
    std_ret = equity_df["Daily_Return"].std()
    
    # Sharpe Ratio (annualized, 252 trading days)
    sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0
    
    # Sortino Ratio
    downside_std = equity_df[equity_df["Daily_Return"] < 0]["Daily_Return"].std()
    sortino = (mean_ret / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    
    # Calmar Ratio
    calmar = (net_profit / initial_capital) / (max_dd_pct / 100) if max_dd_pct > 0 else 0.0

    avg_win = np.mean([t["Net_PnL"] for t in trades if t["Net_PnL"] > 0]) if winning_trades > 0 else 0.0
    avg_lose = np.mean([t["Net_PnL"] for t in trades if t["Net_PnL"] <= 0]) if losing_trades > 0 else 0.0
    avg_hold = np.mean([t["Hold_Time_Mins"] for t in trades]) if total_trades > 0 else 0.0

    perf_summary = {
        "Total Trades": total_trades,
        "Winning Trades": winning_trades,
        "Losing Trades": losing_trades,
        "Win Rate (%)": win_rate,
        "Gross Profit (INR)": gross_profit,
        "Gross Loss (INR)": gross_loss,
        "Taxes & Brokerage (INR)": fees_paid,
        "Net Profit (INR)": net_profit,
        "Profit Factor": profit_factor,
        "Expectancy (INR)": expectancy,
        "Sharpe Ratio": sharpe,
        "Sortino Ratio": sortino,
        "Calmar Ratio": calmar,
        "Maximum Drawdown (%)": max_dd_pct,
        "Average Winner (INR)": avg_win,
        "Average Loser (INR)": avg_lose,
        "Average Holding Time (Mins)": avg_hold
    }

    # Save summary
    pd.DataFrame(list(perf_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "performance_summary.csv", index=False)

    print("==================================================")
    print("      ORB BACKTEST PERFORMANCE REPORT (3-YEARS)")
    print("==================================================")
    for k, v in perf_summary.items():
        if isinstance(v, float):
            print(f"  {k:<30} : {v:.2f}")
        else:
            print(f"  {k:<30} : {v}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(run_simulation())
