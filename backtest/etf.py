"""Cross-asset ETF strategies (India-listed ETFs: Nifty, Nifty Next 50, Bank Nifty, Nasdaq 100, gold).

ETFs are cheap to trade in India: no STT on buys and 0.001% on sells for equity ETFs (none for gold or
international ETFs), against 0.1% each way on shares. That makes short-horizon index signals viable
where the same signal on single stocks is eaten by costs.

dual_momentum   Antonacci's Global Equity Momentum, adapted: hold whichever of NIFTYBEES / MON100 /
                GOLDBEES has the best 12-month return, if it beats cash; else cash. Monthly.
gtaa            Faber's tactical allocation: NIFTYBEES, JUNIORBEES, MON100, GOLDBEES 25% each while above
                their 200-day average, that quarter in cash otherwise. Monthly.
etf_rsi2        Connors RSI(2) on index ETFs: buy RSI(2) < 10 above the 200-day average, sell on a close
                above the 5-day average.
turn_of_month   NIFTYBEES held from the last session of a month to the 3rd session of the next.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .engine import Costs, Result, simulate
from .rotation import simulate_rotation
from .strategies import atr, rsi, sma

ETF_COSTS = Costs(brokerage_per_order=10, stt_pct=0.0005, exchange_pct=0.00297, sebi_pct=0.0001,
                  stamp_buy_pct=0.015, gst_pct=18, dp_per_sell=16, slippage_pct=0.02)


def _ret(c: pd.Series, d: pd.Timestamp, n: int) -> float:
    s = c.loc[:d]
    if len(s) <= n:
        return float("nan")
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1)


def dual_momentum(frames: Dict[str, pd.DataFrame], dates, capital: float, costs: Costs = ETF_COSTS,
                  cash_yield_pct: float = 6.0, assets=("NIFTYBEES", "MON100", "GOLDBEES"),
                  lookback: int = 252, top: int = 1, every: str = "monthly") -> Result:
    assets = [a for a in assets if a in frames]

    def choose(d, current):
        rets = {a: _ret(frames[a]["close"], d, lookback) for a in assets}
        cash = (1 + cash_yield_pct / 100) ** (lookback / 252) - 1
        ranked = sorted([a for a, r in rets.items() if r == r and r > cash], key=lambda a: -rets[a])
        return ranked[:top]
    return simulate_rotation({a: frames[a] for a in assets}, choose, dates, capital, top, costs, cash_yield_pct, every)


def gtaa(frames: Dict[str, pd.DataFrame], dates, capital: float, costs: Costs = ETF_COSTS,
         cash_yield_pct: float = 6.0, assets=("NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES"),
         sma_days: int = 200, every: str = "monthly") -> Result:
    assets = [a for a in assets if a in frames]

    def choose(d, current):
        out = []
        for a in assets:
            c = frames[a]["close"].loc[:d]
            if len(c) >= sma_days and c.iloc[-1] > c.iloc[-sma_days:].mean():
                out.append(a)
        return out
    return simulate_rotation({a: frames[a] for a in assets}, choose, dates, capital, len(assets), costs,
                             cash_yield_pct, every)


def etf_rsi2_signals(df: pd.DataFrame, buy_below: float = 10, trend: int = 200, exit_sma: int = 5) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    r = rsi(out["close"], 2)
    out["entry"] = (out["close"] > sma(out["close"], trend)) & (r < buy_below)
    out["exit"] = out["close"] > sma(out["close"], exit_sma)
    out["score"] = (buy_below - r).fillna(0)
    return out


def etf_rsi2(frames: Dict[str, pd.DataFrame], dates, capital: float, costs: Costs = ETF_COSTS,
             cash_yield_pct: float = 6.0, assets=("NIFTYBEES", "BANKBEES", "JUNIORBEES"),
             buy_below: float = 10, trend: int = 200, exit_sma: int = 5) -> Result:
    sig = {a: etf_rsi2_signals(frames[a], buy_below, trend, exit_sma).loc[dates[0]:dates[-1]]
           for a in assets if a in frames}
    return simulate(sig, capital, max_positions=len(sig), costs=costs, stop_atr=1e9, cash_yield_pct=cash_yield_pct)


def turn_of_month_signals(df: pd.DataFrame, before: int = 1, after: int = 3) -> pd.DataFrame:
    """Signals at the close so that the position is held from the open of the `before`-th last session
    of the month to the open after the `after`-th session of the next month."""
    out = df.copy()
    out["atr"] = atr(out, 14)
    idx = out.index
    month = pd.Series(idx.to_period("M"), index=idx)
    pos_from_end = month.groupby(month).cumcount(ascending=False)      # 0 = last session of the month
    pos_from_start = month.groupby(month).cumcount()                    # 0 = first session
    # enter at the next open when tomorrow is the `before`-th last session
    out["entry"] = (pos_from_end == before).values
    out["exit"] = (pos_from_start == after - 1).values
    out["score"] = 1.0
    return out


def turn_of_month(frames, dates, capital: float, costs: Costs = ETF_COSTS, cash_yield_pct: float = 6.0,
                  asset: str = "NIFTYBEES", before: int = 1, after: int = 3) -> Result:
    sig = {asset: turn_of_month_signals(frames[asset], before, after).loc[dates[0]:dates[-1]]}
    return simulate(sig, capital, max_positions=1, costs=costs, stop_atr=1e9, cash_yield_pct=cash_yield_pct)
