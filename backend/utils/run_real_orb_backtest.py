import os
import sys
import yaml
import csv
import asyncio
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timezone

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

async def run_simulation():
    config_path = backend_dir.parent / "configs" / "real_orb.yaml"

    print(f"Loading Real ORB config from: {config_path}")
    if not config_path.exists():
        print(f"Error: Real ORB config not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Determine symbols to backtest
    config_symbols = config.get("symbols", "ALL")
    
    symbols = []
    if isinstance(config_symbols, str) and config_symbols.endswith(".xlsx"):
        xlsx_path = backend_dir.parent / config_symbols
        if not xlsx_path.exists():
            xlsx_path = Path(config_symbols)
        try:
            df = pd.read_excel(xlsx_path, header=None)
            symbols = df[0].dropna().astype(str).str.strip().tolist()
            symbols = [sym.upper() for sym in symbols if sym]
        except Exception as e:
            print(f"Error loading symbols from excel {xlsx_path}: {e}")
            return
    elif isinstance(config_symbols, list):
        symbols = config_symbols
    else:
        print("Error: symbols must point to the Excel file.")
        return
        
    capital = float(config.get("capital", 60000.0))
    leverage = float(config.get("leverage", 5.0))
    start_date_str = config.get("start_date", "2023-07-07")
    end_date_str = config.get("end_date", "2026-07-06")
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    print(f"Resolving historical data recursively for {len(symbols)} symbols...")
    history_dir = backend_dir.parent / "market_data" / "history"
    
    all_rows = []
    symbols_with_data = []
    
    for symbol in symbols:
        # Search recursively for csv file
        csv_path = None
        for suffix in [f"{symbol}_3y_5m.csv", f"{symbol}_3y_5min.csv", f"{symbol}_180d.csv", f"{symbol}_180d_5min.csv"]:
            found = list(history_dir.glob(f"**/{suffix}"))
            if found:
                csv_path = found[0]
                break
        
        if not csv_path:
            continue
            
        symbols_with_data.append(symbol)
        
        # Load and parse rows
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
                    r["symbol"] = symbol
                    all_rows.append(r)

    # Load Nifty 50 historical data for the trend filter if available
    nifty_csv_path = None
    for suffix in ["NIFTY_50_3y_5m.csv", "NIFTY_50_3y_5min.csv", "NIFTY_3y_5m.csv", "NIFTY_50_180d.csv"]:
        found = list(history_dir.glob(f"**/{suffix}"))
        if found:
            nifty_csv_path = found[0]
            break

    if nifty_csv_path:
        print(f"Loading Nifty 50 index historical data from: {nifty_csv_path.name}")
        nifty_count = 0
        with open(nifty_csv_path, mode="r", encoding="utf-8") as f:
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
                    r["symbol"] = "NIFTY_50"
                    all_rows.append(r)
                    nifty_count += 1
        print(f"Loaded {nifty_count} Nifty 50 candles for trend filtering.")
    else:
        print("Warning: Nifty 50 historical data not found recursively under market_data/history/. Nifty trend filter will be inactive.")

    if not all_rows:
        print("Error: No historical data found for any of the symbols within the backtest period.")
        return

    print(f"Sorting {len(all_rows)} total candles chronologically across {len(symbols_with_data)} symbols...")
    all_rows.sort(key=lambda x: (x["datetime_parsed"], x["symbol"]))

    # Setup single consolidated broker and manager
    sim_cfg = SimulationConfig()
    sim_cfg.LATENCY_MS = 0
    sim_cfg.SPREAD_MODEL = "FIXED"
    sim_cfg.SPREAD_VALUE = 0.05
    sim_cfg.SLIPPAGE_MODEL = "FIXED_TICKS"
    sim_cfg.SLIPPAGE_VALUE = 1.0
    sim_cfg.MARGIN_MULTIPLIER = leverage
    sim_cfg.LOT_SIZE = 1
    sim_cfg.MIN_QTY = 1

    broker = PaperBroker(initial_cash_inr=capital, product_type="INTRADAY", latency_ms=0.0, sim_cfg=sim_cfg)
    risk = RiskController(
        max_capital_per_trade_inr=capital * leverage * 1.5,
        max_daily_loss_inr=capital * 2.0,
        margin_leverage_multiplier=leverage
    )
    manager = StrategyManager(broker, risk)
    
    # Configure allocation parameters in the Strategy Manager to enforce SINGLE_STOCK
    manager.update_allocation_config({
        "allocation_strategy": "SINGLE_STOCK",
        "priority_ranking": symbols_with_data,
        "allocation_weights": [1.0],
        "total_capital": capital
    })
    
    # Instantiate and register strategies
    for symbol in symbols_with_data:
        strat = ORBStrategy(str(config_path))
        strat.strategy_id = f"orb_{symbol.lower()}"
        strat.name = f"ORB Strategy ({symbol})"
        strat.symbol = symbol
        strat.symbols = [symbol]
        strat.capital_limit = capital
        strat.leverage = leverage
        
        manager.register_strategy(strat)

    print(f"Running simulation timeline...")
    
    daily_equity = []
    last_logged_date = None
    ts = all_rows[-1]["datetime_parsed"]

    for idx, r in enumerate(all_rows):
        symbol = r["symbol"]
        ts = r["datetime_parsed"]
        
        if symbol == "NIFTY_50":
            if hasattr(manager, "indices"):
                manager.indices["NIFTY_50"] = {
                    "ltp": float(r["close"]),
                    "open": float(r["open"])
                }
            continue

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
        await asyncio.sleep(0)

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
    for strat in manager.strategies.values():
        if strat.active_trade is not None:
            symbol_rows = [r for r in all_rows if r["symbol"] == strat.symbol]
            if symbol_rows:
                last_row = symbol_rows[-1]
                ts = last_row["datetime_parsed"]
                packet = MarketPacket(
                    packet_type="Quote",
                    exchange_segment="NSE_EQ",
                    security_id=strat.symbol,
                    ltp=float(last_row["close"]),
                    volume=int(last_row["volume"]),
                    timestamp=ts,
                    open=float(last_row["open"]),
                    high=float(last_row["high"]),
                    low=float(last_row["low"]),
                    close=float(last_row["close"])
                )
                await strat._close_position(packet, "Final Backtest Close")
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

    # Master output directory
    output_dir = backend_dir.parent / config.get("output_dir", "market_data/real_orb")
    output_dir.mkdir(parents=True, exist_ok=True)

    master_equity_df = pd.DataFrame(daily_equity)
    safe_to_csv(master_equity_df, output_dir / "master_equity_curve.csv")

    # Gather master trades
    master_trades = []
    for strat in manager.strategies.values():
        master_trades.extend(strat.trade_history)

    # Sort trades chronologically
    master_trades.sort(key=lambda x: x["Entry_Time"])
    
    # Assign unique sequential Trade IDs starting from 1
    for t_idx, trade in enumerate(master_trades):
        trade["Trade_ID"] = t_idx + 1

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
    
    calmar = (net_profit / capital) / (max_dd_pct / 100) if max_dd_pct > 0 else 0.0

    avg_win = np.mean([t["Net_PnL"] for t in master_trades if t["Net_PnL"] > 0]) if winning_trades > 0 else 0.0
    avg_lose = np.mean([t["Net_PnL"] for t in master_trades if t["Net_PnL"] <= 0]) if losing_trades > 0 else 0.0
    avg_hold = np.mean([t["Hold_Time_Mins"] for t in master_trades]) if total_trades > 0 else 0.0

    sl_count = len([t for t in master_trades if t.get("Exit_Reason") == "Stop Loss"])
    tp_count = len([t for t in master_trades if t.get("Exit_Reason") == "Take Profit"])
    so_count = len([t for t in master_trades if t.get("Exit_Reason") == "Square Off"])

    sl_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Stop Loss")
    tp_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Take Profit")
    so_pnl = sum(t["Net_PnL"] for t in master_trades if t.get("Exit_Reason") == "Square Off")

    # Aggregate filter block counters from all strategies
    total_blocked_nifty = 0
    total_blocked_range = 0
    total_blocked_wick = 0
    for strat in manager.strategies.values():
        total_blocked_nifty += getattr(strat, "blocked_by_nifty_count", 0)
        total_blocked_range += getattr(strat, "blocked_by_range_count", 0)
        total_blocked_wick += getattr(strat, "blocked_by_wick_count", 0)

    perf_summary = {
        "Total Portfolio Capital (INR)": capital,
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
        "Net P&L from Square Off (INR)": so_pnl,
        "Blocked by Nifty Filter": total_blocked_nifty,
        "Blocked by Opening Range Filter": total_blocked_range,
        "Blocked by Wick Rejection Filter": total_blocked_wick
    }

    # Save summary
    pd.DataFrame(list(perf_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "master_performance_summary.csv", index=False)

    print("==================================================")
    print("   MASTER CONSOLIDATED PERFORMANCE REPORT (REAL ORB)")
    print("==================================================")
    for k, v in perf_summary.items():
        if isinstance(v, float):
            print(f"  {k:<35} : {v:.2f}")
        else:
            print(f"  {k:<35} : {v}")
    print("==================================================")
    print(f"Master files saved to: {output_dir}")
    print(f"Stocks backtested = {len(symbols_with_data)}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
