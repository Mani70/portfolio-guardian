"""Opening-gap fade on the most volatile large caps (research candidate, paper only until proven).

Before the open, rank the universe by yesterday's ATR as % of price and keep the top `top_n`.
When the first 5-minute bar completes (09:20), a stock that opened more than `min_gap` away from
yesterday's close is faded: bought after a gap down, sold short after a gap up (or longs only).
Stop = entry -/+ `stop_atr` x daily ATR. Exit at the intraday square-off time.
Strength = gap size; only gaps of at least `min_gap` are signals at all.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from ..models import BUY, INTRADAY, SELL, Signal
from .base import IntradayStrategy


class GapFade(IntradayStrategy):
    description = "Fade opening gaps > 1% in the 10 most volatile stocks; stop 0.5 ATR; exit 15:15"

    def prepare(self, day, daily, symbols) -> None:
        self.ctx: Dict[str, dict] = {}
        self.done = set()
        rows = []
        for s in symbols:
            d = daily.get(s)
            if d is None or len(d) < 16:
                continue
            pc = d["close"].shift(1)
            tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
            atr = float(tr.iloc[-14:].mean())
            prev = float(d["close"].iloc[-1])
            rows.append((s, atr / prev, atr, prev))
        rows.sort(key=lambda r: -r[1])
        for s, _, atr, prev in rows[:int(self.params.get("top_n", 10))]:
            self.ctx[s] = {"atr": atr, "prev_close": prev}

    def on_bar(self, now, bars, held) -> List[Signal]:
        out = []
        min_gap = float(self.params.get("min_gap", 0.01))
        longs_only = bool(self.params.get("longs_only", False))
        for s, c in self.ctx.items():
            b = bars.get(s)
            if s in self.done or b is None or len(b) != 1:      # act on the first completed bar only
                continue
            self.done.add(s)
            gap = float(b["open"].iloc[0]) / c["prev_close"] - 1
            if abs(gap) < min_gap:
                continue
            side = BUY if gap < 0 else SELL
            if longs_only and side == SELL:
                continue
            entry = float(b["close"].iloc[0])
            k = float(self.params.get("stop_atr", 0.5)) * c["atr"]
            stop = entry - k if side == BUY else entry + k
            out.append(Signal(self.name, s, side, "entry", abs(gap), entry, now, INTRADAY, stop=stop,
                              reason=f"gap {gap:+.1%} faded"))
        return out
