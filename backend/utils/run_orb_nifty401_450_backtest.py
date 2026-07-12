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

def safe_to_csv(df, path):
    import time
    curr_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(10):
        try:
            df.to_csv(curr_path, index=False)
            return
        except PermissionError:
            curr_path = path.parent / f"{path.stem}_{int(time.time()) + i}{path.suffix}"
    print(f"  Error: Failed to save to {path} (Permission Denied)")

def safe_open_write(path, mode="w", newline="", encoding="utf-8"):
    import time
    curr_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(10):
        try:
            f = open(curr_path, mode, newline=newline, encoding=encoding)
            return f
        except PermissionError:
            curr_path = path.parent / f"{path.stem}_{int(time.time()) + i}{path.suffix}"
    raise PermissionError(f"Failed to open/write {path} after retries")

async def run_single_stock_backtest(symbol: str, config_path: Path, config: dict, start_dt: date, end_dt: date) -> tuple:
    capital = float(config.get("capital", 60000.0))
    leverage = float(config.get("leverage", 5.0))
    
    # Locate data file in nifty_401-450 historical directory
    history_dir = backend_dir.parent / "market_data" / "history" / "nifty_401-450"
    csv_paths = [
        history_dir / f"{symbol}_3y_5m.csv",
        history_dir / f"{symbol}_3y_5min.csv"
    ]
    
    csv_path = None
    for p in csv_paths:
        if p.exists():
            csv_path = p
            break
            
    if not csv_path:
        print(f"  [SKIPPED] Historical data not found for {symbol} in nifty_401-450 folder")
        return [], []

    print(f"  [RUNNING] Backtest for {symbol} using {csv_path.name}...")

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

    # Initialize strategy and bind symbol overrides
    strategy = ORBStrategy(str(config_path))
    strategy.symbol = symbol
    strategy.symbols = [symbol]
    
    manager.register_strategy(strategy)

    # Load and filter rows by start/end dates
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

    if not rows:
        print(f"    Warning: No data rows found within backtest period for {symbol}.")
        return [], []

    daily_equity = []
    last_logged_date = None
    ts = rows[0]["datetime_parsed"]

    # Ingestion simulation
    for idx, r in enumerate(rows):
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

        await manager.on_tick(packet)
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

    # Individual stock output goes to nifty_401-450 subfolder
    individual_output_dir = backend_dir.parent / "market_data" / "orb" / "nifty_401-450" / symbol
    await compile_and_save_individual_reports(strategy, daily_equity, capital, individual_output_dir)

    print(f"  [COMPLETED] {symbol} backtest. Trades: {len(strategy.trade_history)}, Final NAV: Rs. {portfolio['net_asset_value_inr']:.2f}")
    return strategy.trade_history, daily_equity

async def compile_and_save_individual_reports(strategy: ORBStrategy, daily_equity: list, initial_capital: float, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
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

    # 2. Save Performance Summary
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t["Net_PnL"] > 0])
    losing_trades = len([t for t in trades if t["Net_PnL"] <= 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    net_profit = sum(t["Net_PnL"] for t in trades)
    fees_paid = sum(t["Fees"] for t in trades)
    
    perf_summary = {
        "Total Trades": total_trades,
        "Winning Trades": winning_trades,
        "Losing Trades": losing_trades,
        "Win Rate (%)": win_rate,
        "Taxes & Brokerage (INR)": fees_paid,
        "Net Profit (INR)": net_profit
    }
    pd.DataFrame(list(perf_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "performance_summary.csv", index=False)

    # 3. Monthly Returns
    equity_df = pd.DataFrame(daily_equity)
    equity_df["Year_Month"] = pd.to_datetime(equity_df["Timestamp"]).dt.to_period("M")
    monthly_ret = []
    for grp, df_grp in equity_df.groupby("Year_Month"):
        start_val = df_grp["Net_Asset_Value"].iloc[0]
        end_val = df_grp["Net_Asset_Value"].iloc[-1]
        m_ret = ((end_val - start_val) / start_val) * 100
        monthly_ret.append({"Month": str(grp), "Return_Pct": m_ret, "Net_Profit_INR": end_val - start_val})
    safe_to_csv(pd.DataFrame(monthly_ret), output_dir / "monthly_returns.csv")

async def run_simulation():
    import logging
    logging.disable(logging.INFO)

    config_path = backend_dir.parent / "configs" / "orb_nifty_401_450.yaml"

    print(f"Loading ORB config from: {config_path}")
    if not config_path.exists():
        print(f"Error: ORB Nifty 401-450 config not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Determine symbols
    config_symbols = config.get("symbols", "ALL")
    
    symbols = []
    if isinstance(config_symbols, list):
        symbols = config_symbols
    elif isinstance(config_symbols, str):
        if config_symbols.upper() == "ALL":
            nifty_csv_path = backend_dir.parent / "market_data" / "nifty401_450_security_ids.csv"
            if nifty_csv_path.exists():
                with open(nifty_csv_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    symbols = [row["symbol"] for row in reader]
        else:
            symbols = [config_symbols]
            
    capital = float(config.get("capital", 60000.0))
    start_date_str = config.get("start_date", "2023-03-01")
    end_date_str = config.get("end_date", "2026-07-06")
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    print(f"Running simulation for {len(symbols)} Nifty 401-450 symbols from {start_dt} to {end_dt}...")
    
    master_trades = []
    all_daily_equities = {}
    
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] Backtesting {symbol}...")
        trades, daily_equity = await run_single_stock_backtest(symbol, config_path, config, start_dt, end_dt)
        if daily_equity:
            master_trades.extend(trades)
            all_daily_equities[symbol] = daily_equity

    if not all_daily_equities:
        print("ERROR: No backtests were completed successfully!")
        return

    # Consolidate Daily Equity Curve
    print("\nConsolidating daily equity across all Nifty 401-450 symbols...")
    unique_dates = set()
    for symbol, de_list in all_daily_equities.items():
        for item in de_list:
            unique_dates.add(item["Date"])
            
    sorted_dates = sorted(list(unique_dates))
    master_daily_equity = []
    
    last_known_equity = {symbol: {"Net_Asset_Value": capital, "Cash": capital} for symbol in all_daily_equities.keys()}
    
    for d in sorted_dates:
        for symbol, de_list in all_daily_equities.items():
            match = next((item for item in de_list if item["Date"] == d), None)
            if match:
                last_known_equity[symbol] = {
                    "Net_Asset_Value": match["Net_Asset_Value"],
                    "Cash": match["Cash"]
                }
                
        total_nav = sum(item["Net_Asset_Value"] for item in last_known_equity.values())
        total_cash = sum(item["Cash"] for item in last_known_equity.values())
        
        dt_combine = datetime.combine(d, datetime.min.time())
        master_daily_equity.append({
            "Date": d,
            "Timestamp": dt_combine.isoformat(),
            "Net_Asset_Value": total_nav,
            "Cash": total_cash
        })
        
    master_equity_df = pd.DataFrame(master_daily_equity)
    
    # Output dir for nifty_401-450 consolidated reports
    output_dir = backend_dir.parent / "market_data" / "orb" / "nifty_401-450"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    safe_to_csv(master_equity_df, output_dir / "master_equity_curve.csv")

    # Consolidate master trades and sort by Entry_Time
    master_trades.sort(key=lambda x: x["Entry_Time"])
    
    # Write master trade log CSV
    trade_headers = [
        "Trade_ID", "Symbol", "Direction", "Setup", "Entry_Time", "Entry_Price", 
        "Qty", "Exit_Time", "Exit_Price", "Gross_PnL", "Fees", "Net_PnL", 
        "Exit_Reason", "Hold_Time_Mins", "Entry_Candle_Volume", 
        "Prev_Candle_Direction", "Trade_Trend", "Trade_Type"
    ]
    master_trade_log_path = output_dir / "master_trade_log.csv"
    with safe_open_write(master_trade_log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_headers)
        writer.writeheader()
        writer.writerows(master_trades)

    # Master performance calculations
    total_capital = len(all_daily_equities) * capital
    total_trades = len(master_trades)
    winning_trades = len([t for t in master_trades if t["Net_PnL"] > 0])
    losing_trades = len([t for t in master_trades if t["Net_PnL"] <= 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    gross_profit = sum(t["Net_PnL"] for t in master_trades if t["Net_PnL"] > 0)
    gross_loss = sum(t["Net_PnL"] for t in master_trades if t["Net_PnL"] <= 0)
    net_profit = sum(t["Net_PnL"] for t in master_trades)
    fees_paid = sum(t["Fees"] for t in master_trades)
    
    profit_factor = (abs(gross_profit / gross_loss) if gross_loss != 0 else np.nan) if total_trades > 0 else 0.0
    expectancy = (net_profit / total_trades) if total_trades > 0 else 0.0

    # Drawdown calculations on master portfolio
    master_equity_df["Peak"] = master_equity_df["Net_Asset_Value"].cummax()
    master_equity_df["Drawdown_INR"] = master_equity_df["Peak"] - master_equity_df["Net_Asset_Value"]
    master_equity_df["Drawdown_Pct"] = (master_equity_df["Drawdown_INR"] / master_equity_df["Peak"]) * 100
    max_dd_pct = master_equity_df["Drawdown_Pct"].max()
    
    safe_to_csv(master_equity_df[["Date", "Net_Asset_Value", "Peak", "Drawdown_INR", "Drawdown_Pct"]], output_dir / "master_drawdown.csv")

    # Master monthly returns
    master_equity_df["Year_Month"] = pd.to_datetime(master_equity_df["Timestamp"]).dt.to_period("M")
    master_monthly_ret = []
    for grp, df_grp in master_equity_df.groupby("Year_Month"):
        start_val = df_grp["Net_Asset_Value"].iloc[0]
        end_val = df_grp["Net_Asset_Value"].iloc[-1]
        m_ret = ((end_val - start_val) / start_val) * 100
        master_monthly_ret.append({"Month": str(grp), "Return_Pct": m_ret, "Net_Profit_INR": end_val - start_val})
    safe_to_csv(pd.DataFrame(master_monthly_ret), output_dir / "master_monthly_returns.csv")

    # Master Sharpe / Sortino Ratios
    master_equity_df["Daily_Return"] = master_equity_df["Net_Asset_Value"].pct_change().fillna(0)
    mean_ret = master_equity_df["Daily_Return"].mean()
    std_ret = master_equity_df["Daily_Return"].std()
    
    sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0
    
    downside_std = master_equity_df[master_equity_df["Daily_Return"] < 0]["Daily_Return"].std()
    sortino = (mean_ret / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    
    calmar = (net_profit / total_capital) / (max_dd_pct / 100) if max_dd_pct > 0 else 0.0

    avg_win = np.mean([t["Net_PnL"] for t in master_trades if t["Net_PnL"] > 0]) if winning_trades > 0 else 0.0
    avg_lose = np.mean([t["Net_PnL"] for t in master_trades if t["Net_PnL"] <= 0]) if losing_trades > 0 else 0.0
    avg_hold = np.mean([t["Hold_Time_Mins"] for t in master_trades]) if total_trades > 0 else 0.0

    sl_count = len([t for t in master_trades if t.get("Exit_Reason") == "Stop Loss"])
    tp_count = len([t for t in master_trades if t.get("Exit_Reason") == "Take Profit"])
    so_count = len([t for t in master_trades if t.get("Exit_Reason") == "Square Off"])

    sl_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Stop Loss")
    tp_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Take Profit")
    so_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Square Off")

    perf_summary = {
        "Total Portfolio Capital (INR)": total_capital,
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
        "Average Holding Time (Mins)": avg_hold,
        "Trades Closed by Stop Loss": sl_count,
        "Trades Closed by Take Profit": tp_count,
        "Trades Closed by Square Off": so_count,
        "Net P&L from Stop Loss (INR)": sl_pnl,
        "Net P&L from Take Profit (INR)": tp_pnl,
        "Net P&L from Square Off (INR)": so_pnl
    }

    # Save summary
    pd.DataFrame(list(perf_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "master_performance_summary.csv", index=False)

    # Print in tab-separated style
    print("Metric\tValue")
    for k, v in perf_summary.items():
        print(f"{k}\t{v}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
