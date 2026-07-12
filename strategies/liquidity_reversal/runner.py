import os
import sys
import yaml
import csv
import asyncio
import pandas as pd
from pathlib import Path
from datetime import datetime, date

# Setup paths
current_dir = Path(__file__).resolve().parent
backend_dir = current_dir.parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.append(str(backend_dir))

from core.broker.paper import PaperBroker, SimulationConfig
from core.risk.controller import RiskController
from core.strategy.manager import StrategyManager
from strategies.liquidity_reversal.strategy import LiquidityReversalStrategy
from strategies.liquidity_reversal.reports import save_individual_reports, generate_consolidated_reports
from providers.market.dhan.models import MarketPacket

async def run_single_stock_backtest(symbol: str, config_path: Path, config: dict, start_dt: date, end_dt: date, years_duration: float) -> tuple:
    capital = float(config.get("capital", 60000.0))
    leverage = float(config.get("leverage", 5.0))
    
    # Check for historical data files
    history_dir = backend_dir.parent / "market_data" / "history"
    csv_paths = [
        history_dir / f"{symbol}_3y_5m.csv",
        history_dir / f"{symbol}_3y_5min.csv",
        history_dir / f"{symbol}_180d.csv",
        history_dir / f"{symbol}_180d_5min.csv"
    ]
    
    csv_path = None
    for p in csv_paths:
        if p.exists():
            csv_path = p
            break
            
    if not csv_path:
        print(f"  [SKIPPED] Historical data not found for {symbol}")
        return [], []

    print(f"  [RUNNING] Backtest for {symbol} using {csv_path.name}...")

    # Simulation execution settings
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
    strategy = LiquidityReversalStrategy(str(config_path))
    strategy.symbol = symbol
    strategy.symbols = [symbol]
    manager.register_strategy(strategy)

    # Load rows and filter by start/end dates
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

    # Save individual reports
    output_dir = current_dir / "output" / symbol
    save_individual_reports(symbol, strategy.trade_history, daily_equity, capital, output_dir, years_duration)

    return strategy.trade_history, daily_equity

async def main():
    config_path = current_dir / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Determine symbols list
    symbols_cfg = config.get("symbols", "TMPV")
    symbols = []
    if isinstance(symbols_cfg, list):
        symbols = symbols_cfg
    elif isinstance(symbols_cfg, str):
        if symbols_cfg.upper() == "ALL":
            nifty_csv_path = backend_dir.parent / "market_data" / "nifty50_security_ids.csv"
            if nifty_csv_path.exists():
                with open(nifty_csv_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    symbols = [row["symbol"] for row in reader]
            else:
                symbols = ["TMPV"]
        else:
            symbols = [symbols_cfg]

    capital = float(config.get("capital", 60000.0))
    start_date_str = config.get("start_date", "2023-07-07")
    end_date_str = config.get("end_date", "2026-07-06")
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    
    # Calculate years duration
    duration_days = (end_dt - start_dt).days
    years_duration = duration_days / 365.25 if duration_days > 0 else 3.0

    print(f"Running Liquidity Reversal simulation for {len(symbols)} symbols...")
    
    all_trades = []
    all_daily_equities = {}

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] Backtesting {symbol}...")
        trades, daily_equity = await run_single_stock_backtest(symbol, config_path, config, start_dt, end_dt, years_duration)
        if daily_equity:
            all_trades.extend(trades)
            all_daily_equities[symbol] = daily_equity

    if not all_daily_equities:
        print("ERROR: No backtests were completed successfully!")
        return

    # Compile consolidated reports in market_data/liquidity_reversal/
    output_dir = backend_dir.parent / config.get("output_dir", "market_data/liquidity_reversal")
    generate_consolidated_reports(all_trades, all_daily_equities, capital, output_dir, start_dt, end_dt)

if __name__ == "__main__":
    asyncio.run(main())
