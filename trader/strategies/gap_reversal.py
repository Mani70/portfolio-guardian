"""Stock-specific gap-down reversal (research candidate: PAPER ONLY until its paper record passes the gates).

Evidence: on 12 years of daily bars, Nifty 50 stocks that opened >= 3% below the previous close on a day
the market itself did not gap down rose from open to close by ~0.7% net of costs on average (2016-2023).
In the last 2.5 years (5-minute data) the effect was absent, so this runs on paper to see whether it is
back before any money goes near it.

Signal: at 09:20 (first 5-minute bar complete), a stock whose opening gap is <= -min_gap while the median
gap of the universe is above -market_gap_max. Buy at the 09:20 price, stop `stop_atr` daily ATRs below,
exit at the square-off time. Strength = size of the gap.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..models import BUY, INTRADAY, Signal
from .base import IntradayStrategy


class GapReversal(IntradayStrategy):
    description = "Buy stock-specific opening gap-downs of 3%+ (market calm), exit 15:15"

    def prepare(self, day, daily, symbols) -> None:
        self.ctx: Dict[str, dict] = {}
        self.decided = False
        for s in symbols:
            d = daily.get(s)
            if d is None or len(d) < 16:
                continue
            pc = d["close"].shift(1)
            tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
            self.ctx[s] = {"atr": float(tr.iloc[-14:].mean()), "prev_close": float(d["close"].iloc[-1])}

    def on_bar(self, now, bars, held) -> List[Signal]:
        if self.decided:
            return []
        h, m = map(int, str(self.params.get("decide_by", "09:30")).split(":"))
        if (now.hour, now.minute) > (h, m):
            self.decided = True                           # started late: don't act on a stale gap
            return []
        firsts = {s: b for s, b in bars.items() if s in self.ctx and b is not None and len(b) >= 1}
        if len(firsts) < max(5, len(self.ctx) // 2):
            return []                                     # wait until most stocks have a first bar
        self.decided = True
        gaps = {s: float(b["open"].iloc[0]) / self.ctx[s]["prev_close"] - 1 for s, b in firsts.items()}
        market = float(np.median(list(gaps.values())))
        if market <= -float(self.params.get("market_gap_max", 0.01)):
            return []                                     # market-wide gap: a different animal
        out = []
        min_gap = float(self.params.get("min_gap", 0.03))
        for s, g in sorted(gaps.items(), key=lambda kv: kv[1]):
            if g > -min_gap:
                break
            entry = float(firsts[s]["close"].iloc[0])
            stop = entry - float(self.params.get("stop_atr", 1.0)) * self.ctx[s]["atr"]
            out.append(Signal(self.name, s, BUY, "entry", -g, entry, now, INTRADAY, stop=stop,
                              reason=f"opened {g:+.1%} (market {market:+.1%})"))
        return out
