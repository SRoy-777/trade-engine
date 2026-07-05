import math
from typing import List, Dict, Any
from datetime import datetime
from core.strategy.orb.models import TradeRecord

class TradeAnalytics:
    """Compiles and reports advanced metrics for simulated ORB signals."""

    def __init__(self):
        self.records: List[TradeRecord] = []

    def add_record(self, record: TradeRecord) -> None:
        """Stores a completed trade analysis record."""
        self.records.append(record)

    def get_all_records(self) -> List[TradeRecord]:
        return self.records

    def compile_summary(self) -> Dict[str, Any]:
        """Calculates performance aggregates over all records including Sharpe, Sortino, Calmar, and Drawdowns."""
        total = len(self.records)
        if total == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "net_pnl_points": 0.0,
                "avg_hold_time_secs": 0.0,
                "max_mfe": 0.0,
                "max_mae": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "max_drawdown": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "hourly_distribution": {},
                "daily_distribution": {}
            }

        wins = sum(1 for r in self.records if r.pnl > 0)
        losses = total - wins
        win_rate = (wins / total) * 100.0
        net_pnl = sum(r.pnl for r in self.records)
        avg_hold = sum(r.holding_time_secs for r in self.records) / total
        max_mfe = max(r.mfe for r in self.records) if self.records else 0.0
        max_mae = max(r.mae for r in self.records) if self.records else 0.0

        # Expectancy and Profit Factor
        gross_profits = sum(r.pnl for r in self.records if r.pnl > 0)
        gross_losses = sum(abs(r.pnl) for r in self.records if r.pnl < 0)
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (float("inf") if gross_profits > 0 else 0.0)
        expectancy = net_pnl / total

        # Drawdown calculation
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in self.records:
            cum_pnl += r.pnl
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd

        # Sharpe, Sortino, Calmar (Annualized ratios assuming ~252 trading days/year)
        pnl_list = [r.pnl for r in self.records]
        mean_pnl = sum(pnl_list) / total
        variance = sum((x - mean_pnl) ** 2 for x in pnl_list) / max(1, total - 1)
        std_dev = math.sqrt(variance)
        
        sharpe = (mean_pnl / std_dev) * math.sqrt(252) if std_dev > 0.0 else 0.0
        
        downside_pnls = [min(0.0, x) for x in pnl_list]
        downside_variance = sum(x**2 for x in downside_pnls) / max(1, len(downside_pnls) - 1)
        downside_deviation = math.sqrt(downside_variance)
        sortino = (mean_pnl / downside_deviation) * math.sqrt(252) if downside_deviation > 0.0 else 0.0
        
        calmar = (net_pnl / max_dd) if max_dd > 0.0 else 0.0

        # Distribution Analysis
        hourly_dist = {}
        daily_dist = {}
        for r in self.records:
            hr = r.entry_time.hour
            dy = r.entry_time.strftime("%A")
            hourly_dist[hr] = hourly_dist.get(hr, 0) + 1
            daily_dist[dy] = daily_dist.get(dy, 0) + 1

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "net_pnl_points": round(net_pnl, 2),
            "avg_hold_time_secs": round(avg_hold, 1),
            "max_mfe": round(max_mfe, 2),
            "max_mae": round(max_mae, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "max_drawdown": round(max_dd, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "expectancy": round(expectancy, 2),
            "hourly_distribution": hourly_dist,
            "daily_distribution": daily_dist
        }
