# Liquidity Reversal Strategy (Long Only)

This module implements a custom Liquidity Reversal algorithm that enters LONG on support breakouts (liquidity sweeps) and exits on resistance breaks.

## Strategy Logic

### 1. Long Entry
We scan for a sweep of the recent support range:
*   **Trigger**: Current Candle Low < Lowest Low of Previous N Candles (where N is `entry_lookback`).
*   **Action**: Place MARKET BUY order with sized capital (including leverage).

### 2. Long Exit
We exit the position on any of the following conditions:
*   **Target Exit**: Current Candle High > Highest High of Previous M Candles (where M is `exit_lookback`).
*   **Stop Loss**: Triggered if the low sweeps below the parsed Stop Loss (Percentage, ATR, Swing Low, or Fixed Points).
*   **Square Off**: Triggered automatically when simulated time hits the square off limit (default: 15:15).

---

## Folder Structure

```
strategies/
└── liquidity_reversal/
    ├── strategy.py      # BaseStrategy implementation & indicators
    ├── config.yaml      # Configurable strategy & risk parameters
    ├── runner.py        # Main execution driver script
    ├── reports.py       # Performance & consolidation reporting
    └── README.md        # Documentation
```

---

## Configuration

All variables are configured inside `config.yaml`:
*   `symbols`: Set to `"TMPV"` by default. Can be set to `"ALL"` to run on Nifty 50 stocks.
*   `stop_loss_type`: `"none"`, `"percent"`, `"atr"`, `"previous_low"`, or `"fixed_points"`.
*   `enable_trailing_stop`: Set to `true` or `false`.

---

## Running the Backtest

To run the simulation, navigate to the project root and run:
```powershell
python strategies/liquidity_reversal/runner.py
```
Outputs are compiled under `market_data/liquidity_reversal/`.
