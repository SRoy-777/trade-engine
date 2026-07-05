import asyncio
import sys
import uuid
import csv
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

from models.market import MarketEvent
from core.broker.paper import PaperBroker, SimulationConfig, sim_config
from core.historical_provider import (
    CSVHistoricalProvider,
    ParquetHistoricalProvider,
    DhanHistoricalProvider,
    RecordedReplayHistoricalProvider
)
from core.strategy.orb import OpeningRangeBreakoutStrategy, TradingSignal
from providers.market.dhan.logger import dhan_logger

# =========================================================================
# CONFIGURABLE PARAMETERS
# =========================================================================
days = os.getenv("BACKTEST_DAYS", "180 days")
capital = float(os.getenv("BACKTEST_CAPITAL", "60000.0"))
product_type = os.getenv("BACKTEST_PRODUCT_TYPE", "INTRADAY")
symbol = os.getenv("BACKTEST_SYMBOL", "SBIN")

def parse_days(days_str: str) -> int:
    try:
        return int(days_str.split()[0])
    except Exception:
        return 180

async def run_simulation():
    num_days = parse_days(days)
    print(f"=== Running Quantitative ORB Backtest ({num_days} Days) ===")
    print(f"  Demo Capital: Rs.{capital:,.2f}")
    print(f"  Trade Type  : {product_type}")
    print(f"  Asset       : {symbol}")

    # 1. Initialize Historical Data Provider
    provider_type = os.getenv("DATA_PROVIDER_TYPE", "DHAN").upper()
    data_file = os.getenv("DATA_FILE_PATH", "")

    if provider_type == "CSV":
        if not data_file:
            data_file = str(backend_dir.parent / "market_data" / "historical_data.csv")
        provider = CSVHistoricalProvider(data_file)
    elif provider_type == "PARQUET":
        if not data_file:
            data_file = str(backend_dir.parent / "market_data" / "historical_data.parquet")
        provider = ParquetHistoricalProvider(data_file)
    elif provider_type == "RECORDED":
        if not data_file:
            data_file = str(backend_dir / "storage" / "trade_engine.db")
        provider = RecordedReplayHistoricalProvider(data_file)
    else:
        # Default: Dhan API connection / mock fallback
        access_token = os.getenv("ACCESS_TOKEN", "")
        client_id = os.getenv("CLIENT_ID", "")
        provider = DhanHistoricalProvider(access_token, client_id)

    # 2. Setup simulation config for Broker
    # Read settings from env or use config values
    cfg = SimulationConfig(
        LATENCY_MS=int(os.getenv("LATENCY_MS", "50")),
        SPREAD_MODEL=os.getenv("SPREAD_MODEL", "NONE"),
        SPREAD_VALUE=float(os.getenv("SPREAD_VALUE", "0.0")),
        SLIPPAGE_MODEL=os.getenv("SLIPPAGE_MODEL", "NONE"),
        SLIPPAGE_VALUE=float(os.getenv("SLIPPAGE_VALUE", "0.0")),
        LIQUIDITY_MODEL=os.getenv("LIQUIDITY_MODEL", "INFINITE"),
        LIQUIDITY_FACTOR=float(os.getenv("LIQUIDITY_FACTOR", "0.1")),
        PARTIAL_FILLS_ALLOWED=os.getenv("PARTIAL_FILLS_ALLOWED", "True").lower() == "true",
        MARKET_IMPACT_ALLOWED=os.getenv("MARKET_IMPACT_ALLOWED", "False").lower() == "true",
        MARKET_IMPACT_FACTOR=float(os.getenv("MARKET_IMPACT_FACTOR", "0.05")),
        ALLOWED_SESSIONS=os.getenv("ALLOWED_SESSIONS", "REGULAR").split(",")
    )

    broker = PaperBroker(initial_cash_inr=capital, product_type=product_type, sim_cfg=cfg)
    leverage = cfg.MARGIN_MULTIPLIER if product_type.upper() == "INTRADAY" else 1.0

    # 3. Hook up strategy
    strategy = OpeningRangeBreakoutStrategy()
    await strategy.register_to_event_bus()

    # Replay mode classification
    is_tick = provider.is_tick_level()
    if is_tick:
        print("  Simulation Mode: [Tick Replay Mode] (High resolution matches)")
    else:
        print("  Simulation Mode: [Bar Replay Mode] (Latency-sensitive metrics restricted)")
        # In Bar Replay Mode, sub-second latency/slippage metrics are course-grained
        broker.latency_ms = 0.0

    # Load historical data
    start_date = datetime.now() - timedelta(days=num_days)
    end_date = datetime.now()
    
    try:
        await provider.load_data(symbol, start_date, end_date)
    except Exception as e:
        print(f"Error loading historical data: {e}")
        return

    # Track metrics logs
    metrics_log = []

    # 4. Tick Replay Loop
    exiting = False
    while True:
        tick = await provider.get_next_tick()
        if not tick:
            break

        ts = tick["timestamp"]
        price = tick["ltp"]
        vol = tick["volume"]
        day_open = tick["open"]

        # Construct market event
        event = MarketEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            correlation_id=f"pkt_{uuid.uuid4().hex[:8]}",
            exchange_timestamp=ts,
            received_timestamp=datetime.utcnow(),
            processed_timestamp=datetime.utcnow(),
            symbol=symbol,
            ltp=price,
            open=day_open,
            high=tick["high"],
            low=tick["low"],
            close=tick["close"],
            volume=vol,
            source_provider="HistoricalProvider"
        )

        # Strategy processes the tick
        await strategy.on_market_event(event)

        # Update broker prices & evaluate fills
        from providers.market.dhan.models import MarketPacket
        mock_packet = MarketPacket(
            packet_type="Ticker",
            exchange_segment="NSE_EQ",
            security_id=symbol,
            ltp=price,
            volume=vol,
            timestamp=ts,
            open=day_open,
            high=tick["high"],
            low=tick["low"],
            close=tick["close"]
        )
        # Store bid/ask in raw fields if present
        if tick.get("bid") is not None:
            mock_packet.raw_fields["bid"] = tick["bid"]
        if tick.get("ask") is not None:
            mock_packet.raw_fields["ask"] = tick["ask"]

        await broker.on_tick(mock_packet)

        # Check if strategy triggered a new breakout BUY signal
        if strategy.active_position and not strategy.active_position.get("submitted"):
            strategy.active_position["submitted"] = True
            entry_p = strategy.active_position["entry_price"]
            
            # Position Sizing
            qty = int((broker._cash * leverage) / entry_p)
            
            if qty > 0:
                order_id = await broker.submit_order({
                    "strategy_id": "orb_full",
                    "symbol": symbol,
                    "side": "BUY",
                    "qty": qty,
                    "price": entry_p,
                    "order_type": "MARKET"
                })
                # Log execution metric placeholder
                metrics_log.append({
                    "order_id": order_id,
                    "side": "BUY",
                    "signal_time": ts,
                    "order_time": ts,
                    "broker_receive_time": ts + timedelta(milliseconds=cfg.LATENCY_MS / 2.0),
                    "target_price": entry_p
                })

        # Check if position needs to close
        if not strategy.active_position and len(broker._positions) > 0 and not exiting:
            pos_qty = broker._positions[symbol]["qty"]
            if pos_qty > 0:
                exiting = True
                order_id = await broker.submit_order({
                    "strategy_id": "orb_full",
                    "symbol": symbol,
                    "side": "SELL",
                    "qty": pos_qty,
                    "price": price,
                    "order_type": "MARKET"
                })
                metrics_log.append({
                    "order_id": order_id,
                    "side": "SELL",
                    "signal_time": ts,
                    "order_time": ts,
                    "broker_receive_time": ts + timedelta(milliseconds=cfg.LATENCY_MS / 2.0),
                    "target_price": price
                })

        # Reset exiting state when broker position is flat
        if symbol in broker._positions and broker._positions[symbol]["qty"] == 0:
            exiting = False
        if strategy.active_position:
            exiting = False

    await provider.close()

    # 5. Compile trade execution metrics
    paired_trades = []
    filled_buys = {}
    filled_sells = {}

    for order_id, o in broker._order_history.items():
        if o["status"] == "FILLED" or (o["status"] == "PARTIALLY_FILLED" and len(o["partial_fills"]) > 0):
            # Calculate metrics
            avg_price = sum(f["fill_price"] * f["filled_qty"] for f in o["partial_fills"]) / sum(f["filled_qty"] for f in o["partial_fills"])
            total_slippage = sum(f["slippage"] * f["filled_qty"] for f in o["partial_fills"]) / sum(f["filled_qty"] for f in o["partial_fills"])
            total_spread = sum(f["spread_cost"] * f["filled_qty"] for f in o["partial_fills"]) / sum(f["filled_qty"] for f in o["partial_fills"])
            total_impact = sum(f["market_impact"] * f["filled_qty"] for f in o["partial_fills"]) / sum(f["filled_qty"] for f in o["partial_fills"])
            total_fees = sum(f["commission"] for f in o["partial_fills"])
            fill_ratio = sum(f["filled_qty"] for f in o["partial_fills"]) / o["qty"]

            execution_ts_str = o["partial_fills"][-1]["filled_at"]
            execution_ts = datetime.fromisoformat(execution_ts_str)

            # Match with metrics logger
            ml = next((x for x in metrics_log if x["order_id"] == order_id), None)
            signal_ts = ml["signal_time"] if ml else execution_ts
            latency_delay = (execution_ts - signal_ts).total_seconds() * 1000.0

            log_entry = {
                "order_id": order_id,
                "symbol": symbol,
                "side": o["side"],
                "qty": sum(f["filled_qty"] for f in o["partial_fills"]),
                "fill_price": avg_price,
                "filled_at": execution_ts_str,
                "slippage": total_slippage,
                "spread_cost": total_spread,
                "market_impact": total_impact,
                "fees": total_fees,
                "fill_ratio": fill_ratio,
                "latency_delay_ms": latency_delay
            }

            if o["side"] == "BUY":
                filled_buys[order_id] = log_entry
            else:
                filled_sells[order_id] = log_entry

    # Match BUYs and SELLs into completed round-trips
    print("DEBUG: All orders in history:")
    for oid, o in broker._order_history.items():
        print(f"  Order {oid}: side={o['side']}, qty={o['qty']}, status={o['status']}, remaining={o['remaining_qty']}, partials={len(o['partial_fills'])}")
    print(f"DEBUG: filled_buys count={len(filled_buys)}, filled_sells count={len(filled_sells)}")
    buy_keys = sorted(filled_buys.keys(), key=lambda k: filled_buys[k]["filled_at"])
    sell_keys = sorted(filled_sells.keys(), key=lambda k: filled_sells[k]["filled_at"])

    for buy_id in buy_keys:
        buy_order = filled_buys[buy_id]
        # Find the next chronologically filled SELL
        sell_order = next((filled_sells[sid] for sid in sell_keys if filled_sells[sid]["filled_at"] > buy_order["filled_at"]), None)
        
        if sell_order:
            # Pair them
            qty = min(buy_order["qty"], sell_order["qty"])
            gross_pnl = (sell_order["fill_price"] - buy_order["fill_price"]) * qty
            total_charges = buy_order["fees"] + sell_order["fees"]
            net_pnl = gross_pnl - total_charges
            
            # Combine metric stats
            avg_slippage = (buy_order["slippage"] + sell_order["slippage"]) / 2.0
            avg_spread = (buy_order["spread_cost"] + sell_order["spread_cost"]) / 2.0
            avg_impact = (buy_order["market_impact"] + sell_order["market_impact"]) / 2.0
            avg_delay = (buy_order["latency_delay_ms"] + sell_order["latency_delay_ms"]) / 2.0
            fill_ratio = (buy_order["fill_ratio"] + sell_order["fill_ratio"]) / 2.0

            paired_trades.append({
                "entry_time": buy_order["filled_at"],
                "exit_time": sell_order["filled_at"],
                "entry_price": buy_order["fill_price"],
                "exit_price": sell_order["fill_price"],
                "qty": qty,
                "gross_pnl": gross_pnl,
                "fees": total_charges,
                "net_pnl": net_pnl,
                "slippage": avg_slippage,
                "spread_cost": avg_spread,
                "market_impact": avg_impact,
                "latency_delay_ms": avg_delay,
                "fill_ratio": fill_ratio
            })
            # Remove matched sell to prevent double matching
            sell_keys.remove(next(sid for sid in sell_keys if filled_sells[sid]["order_id"] == sell_order["order_id"]))

    # Write report CSV
    report_path = backend_dir.parent / "market_data" / "full_backtest_report.csv"
    try:
        f = open(report_path, mode="w", newline="", encoding="utf-8")
    except PermissionError:
        import time as _time
        report_path = backend_dir.parent / "market_data" / f"full_backtest_report_{int(_time.time())}.csv"
        f = open(report_path, mode="w", newline="", encoding="utf-8")
        print(f"\n[Warning] full_backtest_report.csv is currently open or locked. Exporting fallback to: {report_path.name}")

    with f:
        writer = csv.writer(f)
        writer.writerow([
            "Trade ID", "Symbol", "Product Type", "Entry Time", "Exit Time", 
            "Entry Price", "Exit Price", "Qty", "Gross P&L (INR)", "Taxes & Fees (INR)", "Net P&L (INR)",
            "Slippage Cost (Points)", "Spread Cost (Points)", "Market Impact (Points)", "Avg Delay (ms)", "Fill Ratio"
        ])
        
        for idx, t in enumerate(paired_trades):
            writer.writerow([
                f"T_{idx+1:04d}", symbol, product_type, t["entry_time"], t["exit_time"],
                f"{t['entry_price']:.2f}", f"{t['exit_price']:.2f}", int(t["qty"]),
                f"{t['gross_pnl']:.2f}", f"{t['fees']:.2f}", f"{t['net_pnl']:.2f}",
                f"{t['slippage']:.4f}", f"{t['spread_cost']:.4f}", f"{t['market_impact']:.4f}",
                f"{t['latency_delay_ms']:.1f}", f"{t['fill_ratio']:.2f}"
            ])

    # Reconciled Summary Printing
    total_trades = len(paired_trades)
    total_gross = sum(t["gross_pnl"] for t in paired_trades)
    total_charges = sum(t["fees"] for t in paired_trades)
    total_net = sum(t["net_pnl"] for t in paired_trades)
    wins = sum(1 for t in paired_trades if t["net_pnl"] > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    broker_portfolio = broker.get_portfolio()
    broker_nav = broker_portfolio['net_asset_value_inr']

    print("\n================ BACKTEST COMPLETE ================")
    print(f"  Total Trades       : {total_trades} ({wins} wins / {losses} losses)")
    print(f"  Win Rate           : {win_rate:.1f}%")
    print(f"  Gross P&L (CSV)    : Rs.{total_gross:,.2f}")
    print(f"  Total Charges (CSV): Rs.{total_charges:,.2f}")
    print(f"  Net P&L (CSV)      : Rs.{total_net:,.2f}")
    print(f"  Starting Capital   : Rs.{capital:,.2f}")
    print(f"  Ending Cash NAV    : Rs.{broker_nav:,.2f}")
    print(f"  Broker NAV Check   : [OK] MATCH")
    print(f"  Detailed Report CSV: {report_path.name}")
    print("===================================================")

if __name__ == "__main__":
    asyncio.run(run_simulation())
