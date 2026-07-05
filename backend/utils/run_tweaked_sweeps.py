import os
import sys
import csv
import uuid
import math
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

# Suppress debug logs for clean report printing
import logging
logging.getLogger().setLevel(logging.WARNING)

from models.market import MarketEvent
from core.broker.paper import PaperBroker, SimulationConfig
from core.historical_provider import CSVHistoricalProvider
from core.strategy.orb import OpeningRangeBreakoutStrategy, TradingSignal
from providers.market.dhan.models import MarketPacket
from utils.run_independent_backtests import calculate_advanced_metrics

async def run_tweaked_simulation(vol_mult: float) -> dict:
    # Set the volume multiplier in strategy configuration environment
    os.environ["MIN_VOLUME_MULTIPLIER"] = str(vol_mult)
    os.environ["MAX_TRADES_PER_DAY"] = "1"
    
    symbol = "SBIN"
    csv_file_path = backend_dir.parent / "market_data" / "history" / "SBIN_180d.csv"

    # Initialize CSV provider
    provider = CSVHistoricalProvider(str(csv_file_path))
    start_date = datetime(2026, 1, 6)
    end_date = datetime(2026, 7, 5)
    await provider.load_data(symbol, start_date, end_date)

    # Load ticks
    ticks = []
    while True:
        tick = await provider.get_next_tick()
        if not tick:
            break
        ticks.append(tick)
    await provider.close()

    # Re-initialize config & strategy
    cfg = SimulationConfig(
        LATENCY_MS=50,
        SPREAD_MODEL="NONE",
        SPREAD_VALUE=0.0,
        SLIPPAGE_MODEL="NONE",
        SLIPPAGE_VALUE=0.0,
        LIQUIDITY_MODEL="INFINITE"
    )
    broker = PaperBroker(initial_cash_inr=60000.0, product_type="INTRADAY", sim_cfg=cfg)
    leverage = cfg.MARGIN_MULTIPLIER
    
    from core.strategy.orb.config import OrbConfig
    orb_cfg = OrbConfig(MIN_VOLUME_MULTIPLIER=vol_mult, RISK_REWARD=1.5, MAX_TRADES_PER_DAY=1)
    strategy = OpeningRangeBreakoutStrategy(config=orb_cfg)
    await strategy.register_to_event_bus()

    exiting = False
    
    for tick in ticks:
        ts = tick["timestamp"]
        price = tick["ltp"]
        vol = tick["volume"]
        day_open = tick["open"]

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
            source_provider="CSVHistory"
        )

        await strategy.on_market_event(event)

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
        await broker.on_tick(mock_packet)

        # Check entry trigger
        if strategy.active_position and not strategy.active_position.get("submitted"):
            strategy.active_position["submitted"] = True
            entry_p = strategy.active_position["entry_price"]
            qty = int((broker._cash * leverage) / entry_p)
            if qty > 0:
                await broker.submit_order({
                    "strategy_id": "orb_tweak",
                    "symbol": symbol,
                    "side": "BUY",
                    "qty": qty,
                    "price": entry_p,
                    "order_type": "MARKET"
                })

        # Check exit trigger
        if not strategy.active_position and len(broker._positions) > 0 and not exiting:
            pos_qty = broker._positions[symbol]["qty"]
            if pos_qty > 0:
                exiting = True
                await broker.submit_order({
                    "strategy_id": "orb_tweak",
                    "symbol": symbol,
                    "side": "SELL",
                    "qty": pos_qty,
                    "price": price,
                    "order_type": "MARKET"
                })

        if symbol in broker._positions and broker._positions[symbol]["qty"] == 0:
            exiting = False
        if strategy.active_position:
            exiting = False

    # Compile order history into trades list
    trades = []
    filled_buys = {}
    filled_sells = {}

    for oid, o in broker._order_history.items():
        if o["status"] == "FILLED" and o["partial_fills"]:
            fill_price = o["fill_price"]
            fees = o["transaction_charges_inr"]
            ts_str = o["filled_at"]
            
            entry = {
                "order_id": oid,
                "qty": o["qty"],
                "price": fill_price,
                "fees": fees,
                "timestamp": ts_str
            }
            if o["side"] == "BUY":
                filled_buys[oid] = entry
            else:
                filled_sells[oid] = entry

    buy_keys = sorted(filled_buys.keys(), key=lambda k: filled_buys[k]["timestamp"])
    sell_keys = sorted(filled_sells.keys(), key=lambda k: filled_sells[k]["timestamp"])

    for bid in buy_keys:
        b = filled_buys[bid]
        s = next((filled_sells[sid] for sid in sell_keys if filled_sells[sid]["timestamp"] > b["timestamp"]), None)
        if s:
            qty = min(b["qty"], s["qty"])
            gross = (s["price"] - b["price"]) * qty
            total_fees = b["fees"] + s["fees"]
            net = gross - total_fees
            
            trades.append({
                "entry_time": b["timestamp"],
                "exit_time": s["timestamp"],
                "entry_price": b["price"],
                "exit_price": s["price"],
                "qty": qty,
                "gross_pnl": gross,
                "fees": total_fees,
                "net_pnl": net
            })
            sell_keys.remove(next(sid for sid in sell_keys if filled_sells[sid]["order_id"] == s["order_id"]))

    metrics = calculate_advanced_metrics(trades)
    return metrics

def print_result_block(title: str, m: dict):
    print(f"\n=========================================================")
    print(f"TWEAKED STRATEGY REPORT: {title}")
    print(f"=========================================================")
    print(f"  Total Trades         : {m['total_trades']}")
    print(f"  Winning Trades       : {m['wins']}")
    print(f"  Losing Trades        : {m['losses']}")
    print(f"  Win Rate             : {m['win_rate']:.2f}%")
    print(f"  Net P&L              : Rs.{m['net_pnl']:,.2f}")
    print(f"  Gross P&L            : Rs.{m['gross_pnl']:,.2f}")
    print(f"  Taxes & Fees         : Rs.{m['taxes']:,.2f}")
    print(f"  Maximum Drawdown     : Rs.{m['max_drawdown']:,.2f}")
    print(f"  Profit Factor        : {m['profit_factor']}")
    print(f"  Expectancy           : Rs.{m['expectancy']:,.2f}")
    print(f"  Sharpe Ratio         : {m['sharpe']:.2f}")
    print(f"  Sortino Ratio        : {m['sortino']:.2f}")
    print(f"  Calmar Ratio         : {m['calmar']:.2f}")
    print(f"  Average Holding Time : {m['avg_hold_time_m']:.1f} minutes")
    print(f"  Average Winner       : Rs.{m['avg_winner']:,.2f}")
    print(f"  Average Loser        : Rs.{m['avg_loser']:,.2f}")
    print(f"=========================================================")

async def main():
    print("=== RUNNING TWEAKED ORB STRATEGY ON SBIN (180 DAYS) ===")
    
    # Run #1: No Volume Filter (0.0x)
    m_no_vol = await run_tweaked_simulation(0.0)
    print_result_block("SBIN - NO VOLUME FILTER (0.0x)", m_no_vol)

    # Run #2: 1x Volume Filter (1.0x)
    m_1x_vol = await run_tweaked_simulation(1.0)
    print_result_block("SBIN - 1.0x VOLUME FILTER", m_1x_vol)

    # Run #3: 1.5x Volume Filter (1.5x)
    m_1_5x_vol = await run_tweaked_simulation(1.5)
    print_result_block("SBIN - 1.5x VOLUME FILTER", m_1_5x_vol)

if __name__ == "__main__":
    asyncio.run(main())
