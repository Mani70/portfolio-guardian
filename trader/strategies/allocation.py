"""Long-term core: a fixed mix of ETFs, rebalanced by bands (research/PREREGISTRATION.md Addendum 13, rule L1).

On the last session of each month the strategy compares what it holds with its target weights. When any ETF is more
than `band` (default 5 points) away from its target - and on the year's last session in any case - every ETF is
brought back to target: what is over target is sold (part of the position), what is under is bought (topped up).
The engine (Engine._allocate) turns the targets into orders; buys that need the money from the same evening's sells
wait for it, and the month's review stays open until the mix is back inside the band.

Research (research/FINDINGS.md Addendum 13, 2006-2026 with dividends): 14.9% a year / worst fall -48% / Sharpe 0.85
in 2006-2015 and 15.2% / -27% / 1.26 in 2016-2026, against 12.1% / -60% / 0.59 and 11.4% / -38% / 0.77 for the
Nifty 50 held.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import SwingStrategy

DEFAULT_WEIGHTS = {"NIFTYBEES": 0.45, "JUNIORBEES": 0.15, "MON100": 0.20, "GOLDBEES": 0.10, "LIQUIDCASE": 0.10}


class CoreAllocation(SwingStrategy):
    description = "Long-term core: fixed ETF mix (80% equity), back to target when an ETF drifts 5 points or at year-end"

    def weights(self) -> Dict[str, float]:
        w = {str(k).upper(): float(v) for k, v in (self.params.get("weights") or DEFAULT_WEIGHTS).items()}
        tot = sum(v for v in w.values() if v > 0)
        if tot <= 0:
            raise ValueError(f"{self.name}: weights must add up to more than 0")
        return {k: v / tot for k, v in w.items() if v > 0}

    def band(self) -> float:
        return float(self.params.get("band", 0.05))

    def slots(self) -> int:
        return len(self.weights())

    def symbols(self, engine_universe) -> List[str]:
        return list(self.weights())

    def signals(self, frames, bench, d, held, now) -> list:
        return []                                       # this strategy trades through targets(), not signals

    def due_period(self, d):
        """As SwingStrategy, plus: a review left open (buys waiting for sale money or funds) is resumed on the
        following sessions of the first week, also for a strategy whose first review has not completed yet."""
        period = super().due_period(d)
        if period is None:
            from .momentum import rebalance_period
            still = self.state_get("open_period")
            if still and rebalance_period(d, self.sessions, self.state_get("last_period"), self.holidays) == still:
                return still
        return period

    def commit(self, ok: bool = True) -> None:
        period = getattr(self, "_pending_period", None)
        if period:
            self.state_put("open_period", None if ok else period)
        super().commit(ok)

    def targets(self, frames, d) -> Dict[str, float]:
        """Target weights for session d over the ETFs with a price today (a missing one's weight is spread pro
        rata over the others, as in the research)."""
        w = {k: v for k, v in self.weights().items()
             if frames.get(k) is not None and len(frames[k]) and frames[k].index[-1].date() == d}
        tot = sum(w.values())
        return {k: v / tot for k, v in w.items()} if tot > 0 else {}

    def needs_rebalance(self, current: Dict[str, float], target: Dict[str, float], d, year_end: bool) -> Optional[str]:
        """Why the mix goes back to target today, or None. `current` are weights of the strategy's capital."""
        if not any(v > 0 for v in current.values()):
            return "first purchase"
        off = max((abs(current.get(k, 0.0) - target.get(k, 0.0)), k) for k in set(current) | set(target))
        if off[0] > self.band():
            return f"{off[1]} {current.get(off[1], 0.0):.0%} vs target {target.get(off[1], 0.0):.0%}"
        if year_end and self.params.get("year_end", True):
            return "year-end rebalance"
        return None
