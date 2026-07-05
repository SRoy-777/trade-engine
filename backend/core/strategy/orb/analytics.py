from typing import List, Dict, Any
from core.strategy.orb.models import TradeRecord

class TradeAnalytics:
    """Compiles and reports metrics for simulated ORB signals."""

    def __init__(self):
        self.records: List[TradeRecord] = []

    def add_record(self, record: TradeRecord) -> None:
        """Stores a completed trade analysis record."""
        self.records.append(record)

    def get_all_records(self) -> List[TradeRecord]:
        return self.records

    def compile_summary(self) -> Dict[str, Any]:
        """Calculates performance aggregates over all records."""
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
                "max_mae": 0.0
            }

        wins = sum(1 for r in self.records if r.pnl > 0)
        losses = total - wins
        win_rate = (wins / total) * 100.0
        net_pnl = sum(r.pnl for r in self.records)
        avg_hold = sum(r.holding_time_secs for r in self.records) / total
        max_mfe = max(r.mfe for r in self.records)
        max_mae = max(r.mae for r in self.records)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "net_pnl_points": round(net_pnl, 2),
            "avg_hold_time_secs": round(avg_hold, 1),
            "max_mfe": round(max_mfe, 2),
            "max_mae": round(max_mae, 2)
        }
