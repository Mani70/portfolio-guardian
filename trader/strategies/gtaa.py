"""Cross-asset trend (Faber's tactical asset allocation, on India-listed ETFs).

Each month-end, every asset in `assets` gets an equal slice of the strategy's capital while it closes
above its `sma_days` average; below it, that slice sits in cash. Orders go into the next session's
opening auction. Research (2016-2026, research/swing_research.py): 13.5-15.7% a year with a worst fall
of 13-16%, against 11.4% / -36% for the Nifty ETF, stable across 150/200/250-day averages.
"""
from __future__ import annotations

from typing import List

from ..models import BUY, CNC, SELL, Signal
from .base import SwingStrategy
from .momentum import last_session_of_month, rebalance_period

DEFAULT_ASSETS = ["NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES"]


class TrendAllocation(SwingStrategy):
    description = "Cross-asset ETF trend: Nifty 50, Nifty Next 50, Nasdaq 100, gold; each held while above its 200-day average"

    def assets(self) -> List[str]:
        return [a.upper() for a in self.params.get("assets", DEFAULT_ASSETS)]

    def slots(self) -> int:
        return len(self.assets())

    def symbols(self, engine_universe) -> List[str]:
        return self.assets()

    def signals(self, frames, bench, d, held, now) -> List[Signal]:
        period = self.due_period(d)
        if period is None:
            return []
        n = int(self.params.get("sma_days", 200))
        out = []
        for a in self.assets():
            df = frames.get(a)
            if df is None or len(df) < n or df.index[-1].date() != d:
                continue
            close = float(df["close"].iloc[-1])
            avg = float(df["close"].iloc[-n:].mean())
            if close > avg and a not in held:
                out.append(Signal(self.name, a, BUY, "entry", close / avg - 1, close, now, CNC,
                                  reason=f"above its {n}-day average by {close / avg - 1:+.1%}"))
            elif close <= avg and a in held:
                out.append(Signal(self.name, a, SELL, "exit", 1.0, close, now, CNC,
                                  reason=f"fell below its {n}-day average ({close / avg - 1:+.1%})"))
        self._pending_period = period          # saved by commit() once the orders went out
        return out
