"""Month-end momentum rotation (NSE momentum-index method + trend template + Nifty 200-day filter).

Same rules as backtest.rotation.momentum_rotation: on the last session of each month, keep holdings
still ranked in the top `exit_rank`, fill free slots with the best-ranked new names, hold cash when the
Nifty ETF is below its 200-day average. Orders go into the next session's opening auction.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List

from backtest.data import adjust_splits, suspicious_jumps
from backtest.rotation import market_ok, momentum_scores

from ..models import BUY, CNC, SELL, Signal
from .base import SwingStrategy


def rebalance_period(d: date, sessions=None, last=None, holidays=()):
    """The month a rebalance on session d belongs to, or None. Normally the last session of the month;
    if that was missed (server down, no data that evening) it is caught up in the first week."""
    if last_session_of_month(d, sessions, holidays):
        period = f"{d:%Y-%m}"
    elif d.day <= 7:
        period = f"{d.replace(day=1) - timedelta(days=1):%Y-%m}"
    else:
        return None
    return None if period == last else period


def last_session_of_month(d: date, sessions=None, holidays=()) -> bool:
    """sessions: the exact trading days (replays). Live: weekdays, minus NSE's holidays (trader/holidays.py)."""
    nxt = d + timedelta(days=1)
    if sessions is not None:
        later = [s for s in sessions if s > d]
        return bool(later) and later[0].month != d.month
    holidays = set(holidays or ())
    while nxt.weekday() >= 5 or nxt in holidays:
        nxt += timedelta(days=1)
    return nxt.month != d.month


def month_sessions(d: date, sessions=None, holidays=()) -> List[date]:
    """The trading sessions of d's month up to and including d (replays: exact days; live: weekdays minus NSE
    holidays)."""
    first = d.replace(day=1)
    if sessions is not None:
        return [s for s in sessions if first <= s <= d]
    holidays = set(holidays or ())
    out, x = [], first
    while x <= d:
        if x.weekday() < 5 and x not in holidays:
            out.append(x)
        x += timedelta(days=1)
    return out


def review_period(d: date, k, sessions=None, last=None, holidays=(), catch_up: int = 3):
    """The period a review on session d belongs to, for a strategy reviewed on the k-th session of each month
    (k = "last" or an int; a month with fewer than k sessions uses its last one), or None.
    A missed review (server down, no data that evening) is caught up on the next `catch_up` sessions."""
    if str(k).lower() == "last":
        done = month_sessions(d, sessions, holidays)
        if not done or done[-1] != d:
            return None
        if last_session_of_month(d, sessions, holidays):
            period = f"{d:%Y-%m}#last"
        elif last is not None and len(done) <= catch_up:  # a missed month-end; a new strategy waits for its day
            period = f"{d.replace(day=1) - timedelta(days=1):%Y-%m}#last"
        else:
            period = None
    else:
        k = int(k)
        done = month_sessions(d, sessions, holidays)
        if not done or done[-1] != d:
            return None                                 # d itself is not a session
        n = len(done)
        short = n < k and last_session_of_month(d, sessions, holidays)
        period = f"{d:%Y-%m}#s{k}" if (k <= n <= k + catch_up or short) else None
    return None if period is None or period == last else period


class MomentumRotation(SwingStrategy):
    description = "Month-end momentum rotation, top 10 of the universe, cash when Nifty < 200-day average"

    def signals(self, frames, bench, d, held, now) -> List[Signal]:
        period = self.due_period(d)
        if period is None:
            return []
        slots, exit_rank = int(self.params.get("slots", 10)), int(self.params.get("exit_rank", 20))
        stocks = {}
        uni = getattr(self, "universe_syms", None)
        for s, df in frames.items():
            if df is None or df.empty or df.index[-1].date() != d or (uni is not None and s not in uni):
                continue                                # held stocks outside the universe are only sold
            df = adjust_splits(df)[0]
            if suspicious_jumps(df.iloc[-300:]):
                continue
            stocks[s] = df
        import pandas as pd
        scores = momentum_scores(stocks, pd.Timestamp(d))
        up = market_ok(bench, pd.Timestamp(d)) if self.params.get("market_filter", True) else True
        why_out = "market below 200-day average"
        qf = self.params.get("quality_filter")
        if up and qf:                       # paper experiment (Addendum 11): quality stocks weakening = cash too
            from .. import index_data
            days = int(qf.get("days", 150))
            ok = index_data.above_average(index_data.load(getattr(self, "root", "."), qf.get("index", "quality30")),
                                          d, days)
            if ok is False:
                up, why_out = False, f"Quality 30 index below its {days}-day average"
            elif ok is None:
                import logging
                logging.getLogger("trader").warning("%s: no recent Quality 30 data; filter not applied", self.name)
        ranked = list(scores.index) if up else []
        current = list(held)
        keep = [s for s in current if s in ranked[:exit_rank]]
        targets = keep + [s for s in ranked if s not in keep][:max(0, slots - len(keep))]
        out = []
        for s in current:
            if s not in targets:
                px = float(frames[s]["close"].iloc[-1]) if s in frames and len(frames[s]) else held[s].avg_price
                out.append(Signal(self.name, s, SELL, "exit", 1.0, px, now, CNC,
                                  reason=why_out if not up else "dropped out of the top ranks"))
        for s in targets:
            if s not in held:
                out.append(Signal(self.name, s, BUY, "entry", float(scores[s]), float(stocks[s]["close"].iloc[-1]),
                                  now, CNC, reason=f"momentum rank {ranked.index(s) + 1}"))
        self._pending_period = period          # saved by commit() once the orders went out
        return out
