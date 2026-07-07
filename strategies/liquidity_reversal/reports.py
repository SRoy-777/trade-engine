import os
import csv
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

def safe_to_csv(df: pd.DataFrame, path: Path):
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

def safe_open_write(path: Path, mode="w", newline="", encoding="utf-8"):
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

def save_individual_reports(symbol: str, trades: list, daily_equity: list, initial_capital: float, output_dir: Path, years_duration: float):
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Save trades.csv
    trade_headers = [
        "Symbol", "Entry Date", "Entry Time", "Exit Date", "Exit Time", "Entry Price", 
        "Exit Price", "Quantity", "Gross P&L", "Charges", "Net P&L", 
        "Return %", "Holding Time", "Exit Reason"
    ]
    trades_path = output_dir / "trades.csv"
    with safe_open_write(trades_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_headers)
        writer.writeheader()
        writer.writerows(trades)

    # 2. Save equity_curve.csv
    equity_df = pd.DataFrame(daily_equity)
    safe_to_csv(equity_df, output_dir / "equity_curve.csv")

    # 3. Monthly Returns
    equity_df["Year_Month"] = pd.to_datetime(equity_df["Timestamp"]).dt.to_period("M")
    monthly_ret = []
    for grp, df_grp in equity_df.groupby("Year_Month"):
        start_val = df_grp["Net_Asset_Value"].iloc[0]
        end_val = df_grp["Net_Asset_Value"].iloc[-1]
        m_ret = ((end_val - start_val) / start_val) * 100
        monthly_ret.append({"Month": str(grp), "Return_Pct": m_ret, "Net_Profit_INR": end_val - start_val})
    safe_to_csv(pd.DataFrame(monthly_ret), output_dir / "monthly_returns.csv")

    # 4. Performance Summary
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t["Net P&L"] > 0])
    losing_trades = len([t for t in trades if t["Net P&L"] <= 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    gross_profit = sum(t["Net P&L"] for t in trades if t["Net P&L"] > 0)
    gross_loss = sum(t["Net P&L"] for t in trades if t["Net P&L"] <= 0)
    net_profit = sum(t["Net P&L"] for t in trades)
    
    avg_win = np.mean([t["Net P&L"] for t in trades if t["Net P&L"] > 0]) if winning_trades > 0 else 0.0
    avg_lose = np.mean([t["Net P&L"] for t in trades if t["Net P&L"] <= 0]) if losing_trades > 0 else 0.0
    profit_factor = (abs(gross_profit / gross_loss) if gross_loss != 0 else np.nan) if total_trades > 0 else 0.0
    
    # Calculate Drawdown
    equity_df["Peak"] = equity_df["Net_Asset_Value"].cummax()
    equity_df["Drawdown_Pct"] = ((equity_df["Peak"] - equity_df["Net_Asset_Value"]) / equity_df["Peak"]) * 100
    max_dd = equity_df["Drawdown_Pct"].max()
    
    cagr = ((equity_df["Net_Asset_Value"].iloc[-1] / initial_capital) ** (1.0 / years_duration) - 1.0) * 100.0 if years_duration > 0 and equity_df["Net_Asset_Value"].iloc[-1] > 0 else -100.0
    avg_hold = np.mean([t["Holding Time"] for t in trades]) if total_trades > 0 else 0.0
    
    largest_win = max([t["Net P&L"] for t in trades if t["Net P&L"] > 0]) if winning_trades > 0 else 0.0
    largest_lose = min([t["Net P&L"] for t in trades if t["Net P&L"] <= 0]) if losing_trades > 0 else 0.0

    perf_summary = {
        "Total Trades": total_trades,
        "Winning Trades": winning_trades,
        "Losing Trades": losing_trades,
        "Win Rate": win_rate,
        "Gross Profit": gross_profit,
        "Gross Loss": gross_loss,
        "Net Profit": net_profit,
        "Average Winner": avg_win,
        "Average Loser": avg_lose,
        "Profit Factor": profit_factor,
        "Maximum Drawdown": max_dd,
        "CAGR": cagr,
        "Average Holding Time": avg_hold,
        "Largest Winner": largest_win,
        "Largest Loser": largest_lose
    }
    pd.DataFrame(list(perf_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "performance_summary.csv", index=False)

def generate_consolidated_reports(all_trades: list, all_daily_equities: dict, capital: float, output_dir: Path, start_dt: datetime.date, end_dt: datetime.date):
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Duration in years
    duration_days = (end_dt - start_dt).days
    years_duration = duration_days / 365.25 if duration_days > 0 else 3.0
    
    # 1. Master Trade Log
    # Sort trades by Entry Date and Entry Time
    all_trades.sort(key=lambda x: (x["Entry Date"], x["Entry Time"]))
    trade_headers = [
        "Symbol", "Entry Date", "Entry Time", "Exit Date", "Exit Time", "Entry Price", 
        "Exit Price", "Quantity", "Gross P&L", "Charges", "Net P&L", 
        "Return %", "Holding Time", "Exit Reason"
    ]
    with safe_open_write(output_dir / "master_trade_log.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_headers)
        writer.writeheader()
        writer.writerows(all_trades)

    # 2. Master Monthly Returns
    # Compile monthly stats group by (Year, Month, Stock)
    monthly_groups = {}
    for t in all_trades:
        exit_dt = datetime.strptime(t["Exit Date"], "%Y-%m-%d")
        y_m = (exit_dt.year, exit_dt.month)
        stock = t["Symbol"]
        key = (y_m[0], y_m[1], stock)
        if key not in monthly_groups:
            monthly_groups[key] = []
        monthly_groups[key].append(t)
        
    monthly_records = []
    for key, trades_grp in monthly_groups.items():
        year, month, stock = key
        num_trades = len(trades_grp)
        wins = len([t for t in trades_grp if t["Net P&L"] > 0])
        loses = len([t for t in trades_grp if t["Net P&L"] <= 0])
        m_ret = sum(t["Net P&L"] for t in trades_grp)
        m_pct = (m_ret / capital) * 100.0
        
        gross_win = sum(t["Net P&L"] for t in trades_grp if t["Net P&L"] > 0)
        gross_lose = sum(t["Net P&L"] for t in trades_grp if t["Net P&L"] <= 0)
        pf = (abs(gross_win / gross_lose) if gross_lose != 0 else np.nan) if num_trades > 0 else 0.0
        
        monthly_records.append({
            "Year": year,
            "Month": f"{month:02d}",
            "Stock": stock,
            "Monthly Return": m_ret,
            "Monthly %": m_pct,
            "Number of Trades": num_trades,
            "Winning Trades": wins,
            "Losing Trades": loses,
            "Profit Factor": pf
        })
        
    # Sort monthly returns
    monthly_df = pd.DataFrame(monthly_records)
    if not monthly_df.empty:
        monthly_df.sort_values(by=["Year", "Month", "Stock"], inplace=True)
    safe_to_csv(monthly_df, output_dir / "master_monthly_returns.csv")

    # 3. Master Performance Summary (compiled per Stock)
    perf_records = []
    for stock, de_list in all_daily_equities.items():
        stock_trades = [t for t in all_trades if t["Symbol"] == stock]
        total_trades = len(stock_trades)
        winning_trades = len([t for t in stock_trades if t["Net P&L"] > 0])
        losing_trades = len([t for t in stock_trades if t["Net P&L"] <= 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        
        gross_profit = sum(t["Net P&L"] for t in stock_trades if t["Net P&L"] > 0)
        gross_loss = sum(t["Net P&L"] for t in stock_trades if t["Net P&L"] <= 0)
        net_profit = sum(t["Net P&L"] for t in stock_trades)
        
        avg_win = np.mean([t["Net P&L"] for t in stock_trades if t["Net P&L"] > 0]) if winning_trades > 0 else 0.0
        avg_lose = np.mean([t["Net P&L"] for t in stock_trades if t["Net P&L"] <= 0]) if losing_trades > 0 else 0.0
        profit_factor = (abs(gross_profit / gross_loss) if gross_loss != 0 else np.nan) if total_trades > 0 else 0.0
        
        # Drawdown for this stock
        df_de = pd.DataFrame(de_list)
        df_de["Peak"] = df_de["Net_Asset_Value"].cummax()
        df_de["Drawdown_Pct"] = ((df_de["Peak"] - df_de["Net_Asset_Value"]) / df_de["Peak"]) * 100
        max_dd = df_de["Drawdown_Pct"].max()
        
        cagr = ((df_de["Net_Asset_Value"].iloc[-1] / capital) ** (1.0 / years_duration) - 1.0) * 100.0 if years_duration > 0 and df_de["Net_Asset_Value"].iloc[-1] > 0 else -100.0
        avg_hold = np.mean([t["Holding Time"] for t in stock_trades]) if total_trades > 0 else 0.0
        
        largest_win = max([t["Net P&L"] for t in stock_trades if t["Net P&L"] > 0]) if winning_trades > 0 else 0.0
        largest_lose = min([t["Net P&L"] for t in stock_trades if t["Net P&L"] <= 0]) if losing_trades > 0 else 0.0
        
        perf_records.append({
            "Stock": stock,
            "Total Trades": total_trades,
            "Winning Trades": winning_trades,
            "Losing Trades": losing_trades,
            "Win Rate": win_rate,
            "Gross Profit": gross_profit,
            "Gross Loss": gross_loss,
            "Net Profit": net_profit,
            "Average Winner": avg_win,
            "Average Loser": avg_lose,
            "Profit Factor": profit_factor,
            "Maximum Drawdown": max_dd,
            "CAGR": cagr,
            "Average Holding Time": avg_hold,
            "Largest Winner": largest_win,
            "Largest Loser": largest_lose
        })
        
    safe_to_csv(pd.DataFrame(perf_records), output_dir / "master_performance_summary.csv")

    # 4. Overall Portfolio Summary
    # Consolidate equity curves
    unique_dates = set()
    for stock, de_list in all_daily_equities.items():
        for item in de_list:
            unique_dates.add(item["Date"])
            
    sorted_dates = sorted(list(unique_dates))
    portfolio_daily_equity = []
    
    last_known = {stock: {"Net_Asset_Value": capital, "Cash": capital} for stock in all_daily_equities.keys()}
    for d in sorted_dates:
        for stock, de_list in all_daily_equities.items():
            match = next((item for item in de_list if item["Date"] == d), None)
            if match:
                last_known[stock] = {
                    "Net_Asset_Value": match["Net_Asset_Value"],
                    "Cash": match["Cash"]
                }
        total_nav = sum(item["Net_Asset_Value"] for item in last_known.values())
        portfolio_daily_equity.append({
            "Date": d,
            "Net_Asset_Value": total_nav
        })
        
    portfolio_df = pd.DataFrame(portfolio_daily_equity)
    portfolio_df["Peak"] = portfolio_df["Net_Asset_Value"].cummax()
    portfolio_df["Drawdown_Pct"] = ((portfolio_df["Peak"] - portfolio_df["Net_Asset_Value"]) / portfolio_df["Peak"]) * 100
    max_portfolio_dd = portfolio_df["Drawdown_Pct"].max()

    # Streak calculations
    max_win_streak = 0
    max_lose_streak = 0
    current_win_streak = 0
    current_lose_streak = 0
    
    # Sort all trades by exit time
    sorted_all_trades = sorted(all_trades, key=lambda x: (x["Exit Date"], x["Exit Time"]))
    for t in sorted_all_trades:
        if t["Net P&L"] > 0:
            current_win_streak += 1
            current_lose_streak = 0
            if current_win_streak > max_win_streak:
                max_win_streak = current_win_streak
        else:
            current_lose_streak += 1
            current_win_streak = 0
            if current_lose_streak > max_lose_streak:
                max_lose_streak = current_lose_streak

    total_portfolio_trades = len(all_trades)
    winning_portfolio_trades = len([t for t in all_trades if t["Net P&L"] > 0])
    win_rate_portfolio = (winning_portfolio_trades / total_portfolio_trades * 100) if total_portfolio_trades > 0 else 0.0
    
    gross_win_portfolio = sum(t["Net P&L"] for t in all_trades if t["Net P&L"] > 0)
    gross_lose_portfolio = sum(t["Net P&L"] for t in all_trades if t["Net P&L"] <= 0)
    net_profit_portfolio = sum(t["Net P&L"] for t in all_trades)
    fees_portfolio = sum(t["Charges"] for t in all_trades)
    
    pf_portfolio = (abs(gross_win_portfolio / gross_lose_portfolio) if gross_lose_portfolio != 0 else np.nan) if total_portfolio_trades > 0 else 0.0
    avg_ret_per_trade = np.mean([t["Return %"] for t in all_trades]) if total_portfolio_trades > 0 else 0.0
    
    # Average monthly return calculation
    portfolio_df["Year_Month"] = pd.to_datetime(portfolio_df["Date"]).dt.to_period("M")
    monthly_pcts = []
    for grp, df_grp in portfolio_df.groupby("Year_Month"):
        start_val = df_grp["Net_Asset_Value"].iloc[0]
        end_val = df_grp["Net_Asset_Value"].iloc[-1]
        monthly_pcts.append(((end_val - start_val) / start_val) * 100.0)
    avg_monthly_pct = np.mean(monthly_pcts) if monthly_pcts else 0.0

    overall_summary = {
        "Total Trades": total_portfolio_trades,
        "Overall Win Rate": win_rate_portfolio,
        "Net Profit": net_profit_portfolio,
        "Gross Profit": gross_win_portfolio,
        "Gross Loss": gross_lose_portfolio,
        "Profit Factor": pf_portfolio,
        "Maximum Drawdown": max_portfolio_dd,
        "Monthly Returns": avg_monthly_pct,
        "Average Return per Trade": avg_ret_per_trade,
        "Largest Winning Streak": max_win_streak,
        "Largest Losing Streak": max_lose_streak
    }
    pd.DataFrame(list(overall_summary.items()), columns=["Metric", "Value"]).to_csv(output_dir / "overall_portfolio_summary.csv", index=False)
    
    # Calculate additional metrics for final terminal printout
    total_capital = len(all_daily_equities) * capital
    
    # Sharpe/Sortino/Calmar ratios of consolidated portfolio
    # Daily returns based on consolidated NAV
    portfolio_df["Daily_Return"] = portfolio_df["Net_Asset_Value"].pct_change().fillna(0)
    mean_ret = portfolio_df["Daily_Return"].mean()
    std_ret = portfolio_df["Daily_Return"].std()
    sharpe = (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0
    
    downside_std = portfolio_df[portfolio_df["Daily_Return"] < 0]["Daily_Return"].std()
    sortino = (mean_ret / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    
    calmar = (net_profit_portfolio / total_capital) / (max_portfolio_dd / 100.0) if max_portfolio_dd > 0 else 0.0
    
    avg_winner_val = np.mean([t["Net P&L"] for t in all_trades if t["Net P&L"] > 0]) if winning_portfolio_trades > 0 else 0.0
    avg_loser_val = np.mean([t["Net P&L"] for t in all_trades if t["Net P&L"] <= 0]) if (total_portfolio_trades - winning_portfolio_trades) > 0 else 0.0
    avg_hold_val = np.mean([t["Holding Time"] for t in all_trades]) if total_portfolio_trades > 0 else 0.0
    expectancy_val = (net_profit_portfolio / total_portfolio_trades) if total_portfolio_trades > 0 else 0.0

    print("==================================================")
    print("      LIQUIDITY REVERSAL PERFORMANCE REPORT")
    print("==================================================")
    print(f"  Total Portfolio Capital (INR)       : {total_capital:.2f}")
    print(f"  Total Trades                        : {total_portfolio_trades}")
    print(f"  Winning Trades                      : {winning_portfolio_trades}")
    print(f"  Losing Trades                       : {total_portfolio_trades - winning_portfolio_trades}")
    print(f"  Win Rate (%)                        : {win_rate_portfolio:.2f}")
    print(f"  Gross Profit (INR)                  : {gross_win_portfolio:.2f}")
    print(f"  Gross Loss (INR)                    : {gross_lose_portfolio:.2f}")
    print(f"  Taxes & Brokerage (INR)             : {fees_portfolio:.2f}")
    print(f"  Net Profit (INR)                    : {net_profit_portfolio:.2f}")
    print(f"  Profit Factor                       : {pf_portfolio:.2f}")
    print(f"  Expectancy (INR)                    : {expectancy_val:.2f}")
    print(f"  Sharpe Ratio                        : {sharpe:.2f}")
    print(f"  Sortino Ratio                       : {sortino:.2f}")
    print(f"  Calmar Ratio                        : {calmar:.2f}")
    print(f"  Maximum Drawdown (%)                : {max_portfolio_dd:.2f}")
    print(f"  Average Winner (INR)                : {avg_winner_val:.2f}")
    print(f"  Average Loser (INR)                 : {avg_loser_val:.2f}")
    print(f"  Average Holding Time (Mins)         : {avg_hold_val:.2f}")
    print("==================================================")
