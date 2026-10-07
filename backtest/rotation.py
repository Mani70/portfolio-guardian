"""Scheduled-rebalance strategies (low turnover, held for weeks to months).

momentum_rotation
    Evidence: NSE's Nifty200 Momentum 30 (6- and 12-month returns, each divided by volatility)
    returned ~19.2%/yr vs ~15.1% for the Nifty 200 since 2005, but with deeper crashes and bad
    years (2008, 2009, 2018, 2022, 2025). Here:
      - score = average z-score of volatility-adjusted 6- and 12-month returns
      - only stocks passing a Minervini-style trend template are eligible:
        close > 50-day > 200-day average, and within 25% of the 52-week high
      - market filter: if the Nifty ETF is below its 200-day average, hold cash (liquid-fund yield)
      - month-end review: keep holdings still ranked in the top `exit_rank`; fill empty slots
        with the best-ranked new names (the buffer cuts needless turnover)

index_trend
    Hold the Nifty ETF while it is above its 200-day average at month-end, else cash. A few
    trades a year; the aim is to sidestep long bear markets, not to beat the index in bull runs.

Orders are decided at a month-end CLOSE and filled at the next day's OPEN.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from .engine import Costs, Result, Trade
from .strategies import sma


def _rebalance_days(dates: pd.DatetimeIndex, every: str = "monthly") -> Set[pd.Timestamp]:
    """Last trading day of each week, fortnight, month or quarter in `dates`."""
    out = set()
    if every in ("weekly", "biweekly"):
        ends = [dates[i] for i in range(len(dates) - 1)
                if dates[i].isocalendar()[:2] != dates[i + 1].isocalendar()[:2]]
        return set(ends if every == "weekly" else ends[1::2])
    for i in range(len(dates) - 1):
        d, nxt = dates[i], dates[i + 1]
        if d.month != nxt.month and (every == "monthly" or d.month in (3, 6, 9, 12)):
            out.add(d)
    return out


def momentum_scores(frames: Dict[str, pd.DataFrame], d: pd.Timestamp) -> pd.Series:
    """Volatility-adjusted 6m/12m momentum, z-scored across eligible stocks, as of day d's close."""
    rows = {}
    for s, df in frames.items():
        c = df["close"].loc[:d]
        if len(c) < 260 or c.index[-1] != d:
            continue
        price = c.iloc[-1]
        sma50, sma200 = c.iloc[-50:].mean(), c.iloc[-200:].mean()
        hi52 = c.iloc[-252:].max()
        if not (price > sma50 > sma200 and price >= 0.75 * hi52):      # trend template (subset)
            continue
        vol = np.log(c.iloc[-252:]).diff().std() * math.sqrt(252)
        if not vol or np.isnan(vol):
            continue
        r6, r12 = price / c.iloc[-126] - 1, price / c.iloc[-252] - 1
        rows[s] = (r6 / vol, r12 / vol)
    if not rows:
        return pd.Series(dtype=float)
    m = pd.DataFrame(rows, index=["m6", "m12"]).T
    z = (m - m.mean()) / m.std(ddof=0).replace(0, 1)
    return z.mean(axis=1).sort_values(ascending=False)


def market_ok(bench: Optional[pd.DataFrame], d: pd.Timestamp, n: int = 200) -> bool:
    if bench is None or bench.empty:
        return True
    c = bench["close"].loc[:d]
    if len(c) < n:
        return True
    return bool(c.iloc[-1] > c.iloc[-n:].mean())


def simulate_rotation(frames: Dict[str, pd.DataFrame], choose: Callable[[pd.Timestamp, List[str]], List[str]],
                      dates: pd.DatetimeIndex, capital: float = 500_000, slots: int = 10,
                      costs: Optional[Costs] = None, cash_yield_pct: float = 0.0,
                      every: str = "monthly") -> Result:
    """choose(d, current_holdings) -> target symbols, decided at d's close."""
    costs = costs or Costs()
    slip = costs.slippage_pct / 100
    rows = {s: f[["open", "close"]].to_dict("index") for s, f in frames.items()}
    review = _rebalance_days(dates, every)
    cash, holdings = capital, {}            # sym -> [qty, entry_px, entry_date, entry_cost, bars]
    last_close: Dict[str, float] = {}
    trades: List[Trade] = []
    costs_paid, pending = 0.0, None
    eq, expo = [], []
    prev_day = None

    def mark():
        return sum(q[0] * last_close.get(s, q[1]) for s, q in holdings.items())

    for d in dates:
        if prev_day is not None and cash > 0 and cash_yield_pct:
            cash *= (1 + cash_yield_pct / 100) ** ((d - prev_day).days / 365.25)
        prev_day = d
        if pending is not None:
            target = pending
            pending = None
            for s in [s for s in holdings if s not in target]:               # sells first
                r = rows[s].get(d)
                if r is None:
                    continue
                qty, epx, edate, ecost, bars = holdings.pop(s)
                px = r["open"] * (1 - slip)
                c = costs.sell(qty * px)
                cash += qty * px - c
                costs_paid += c
                inv = qty * epx + ecost
                pnl = qty * px - c - inv
                trades.append(Trade(s, edate, epx, d, px, qty, pnl, pnl / inv * 100, bars, "rebalance"))
            new = [s for s in target if s not in holdings]
            equity_now = cash + mark()
            for s in new:
                r = rows[s].get(d)
                if r is None or len(holdings) >= slots:
                    continue
                px = r["open"] * (1 + slip)
                budget = min(equity_now / slots, cash)
                qty = math.floor(budget / (px * 1.003))
                if qty < 1:
                    continue
                c = costs.buy(qty * px)
                if qty * px + c > cash:
                    continue
                cash -= qty * px + c
                costs_paid += c
                holdings[s] = [qty, px, d, c, 0]
        for s, rmap in rows.items():
            r = rmap.get(d)
            if r is not None:
                last_close[s] = r["close"]
                if s in holdings:
                    holdings[s][4] += 1
        if d in review:
            pending = choose(d, list(holdings))
        inv = mark()
        eq.append(cash + inv)
        expo.append(inv / (cash + inv) if cash + inv else 0)

    for s, (qty, epx, edate, ecost, bars) in list(holdings.items()):
        px = last_close.get(s, epx)
        c = costs.sell(qty * px)
        cash += qty * px - c
        costs_paid += c
        inv = qty * epx + ecost
        pnl = qty * px - c - inv
        trades.append(Trade(s, edate, epx, dates[-1], px, qty, pnl, pnl / inv * 100, bars, "end"))
    if eq:
        eq[-1] = cash
    return Result(pd.Series(eq, index=dates, dtype=float), trades, costs_paid,
                  pd.Series(expo, index=dates, dtype=float), dict(slots=slots, every=every))


def momentum_rotation(frames, bench, dates, capital, costs, cash_yield_pct, slots: int = 10,
                      exit_rank: int = 20, every: str = "monthly", market_filter: bool = True) -> Result:
    def choose(d, current):
        if market_filter and not market_ok(bench, d):
            return []
        ranked = list(momentum_scores(frames, d).index)
        keep = [s for s in current if s in ranked[:exit_rank]]
        fill = [s for s in ranked if s not in keep][:max(0, slots - len(keep))]
        return keep + fill
    return simulate_rotation(frames, choose, dates, capital, slots, costs, cash_yield_pct, every)


def _keep_fill(ranked: List[str], current: List[str], slots: int, exit_rank: int) -> List[str]:
    keep = [s for s in current if s in ranked[:exit_rank]]
    return keep + [s for s in ranked if s not in keep][:max(0, slots - len(keep))]


def _closes(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({s: f["close"] for s, f in frames.items()})


def high52_rotation(frames, bench, dates, capital, costs, cash_yield_pct, slots: int = 10, exit_rank: int = 20,
                    every: str = "monthly", market_filter: bool = True) -> Result:
    """George & Hwang (2004): hold the stocks closest to their 52-week high."""
    closes = _closes(frames)

    def choose(d, current):
        if market_filter and not market_ok(bench, d):
            return []
        c = closes.loc[:d].iloc[-252:]
        last = c.iloc[-1]
        ratio = (last / c.max())[last.notna()].dropna().sort_values(ascending=False)
        return _keep_fill(list(ratio.index), current, slots, exit_rank)
    return simulate_rotation(frames, choose, dates, capital, slots, costs, cash_yield_pct, every)


def clenow_score(c: pd.Series, n: int = 90) -> float:
    """Annualised exponential regression slope over n days x R^2 (Clenow, Stocks on the Move)."""
    y = np.log(c.values[-n:])
    x = np.arange(n)
    b = np.polyfit(x, y, 1)[0]
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    return (math.exp(b) ** 250 - 1) * r2


def clenow_rotation(frames, bench, dates, capital, costs, cash_yield_pct, slots: int = 10, exit_rank: int = 15,
                    every: str = "weekly") -> Result:
    """Clenow (2015): rank by clenow_score; only stocks above their 100-day average with no >15%
    one-day move in 90 days; no new buys while the index is below its 200-day average."""
    def choose(d, current):
        sc = {}
        for s, df in frames.items():
            c = df["close"].loc[:d]
            if len(c) < 120 or c.index[-1] != d:
                continue
            if c.iloc[-1] < c.iloc[-100:].mean() or c.iloc[-91:].pct_change().abs().max() > 0.15:
                continue
            sc[s] = clenow_score(c)
        ranked = [s for s, v in sorted(sc.items(), key=lambda kv: -kv[1]) if v > 0]
        if not market_ok(bench, d):
            return [s for s in current if s in ranked[:exit_rank]]
        return _keep_fill(ranked, current, slots, exit_rank)
    return simulate_rotation(frames, choose, dates, capital, slots, costs, cash_yield_pct, every)


def lowvol_rotation(frames, bench, dates, capital, costs, cash_yield_pct, slots: int = 10, exit_rank: int = 20,
                    every: str = "monthly") -> Result:
    """Low-volatility anomaly (NSE Nifty100 Low Volatility 30 method): lowest 1-year volatility."""
    closes = _closes(frames)

    def choose(d, current):
        c = closes.loc[:d].iloc[-253:]
        vol = np.log(c).diff().std()[c.iloc[-1].notna()].dropna().sort_values()
        return _keep_fill(list(vol.index), current, slots, exit_rank)
    return simulate_rotation(frames, choose, dates, capital, slots, costs, cash_yield_pct, every)


def equal_weight_trend(frames, bench, dates, capital, costs, cash_yield_pct, sma_days: int = 200,
                       every: str = "monthly") -> Result:
    """Hold every stock in the universe equally while the index is above its 200-day average, else cash."""
    names = list(frames)

    def choose(d, current):
        return [s for s in names if d in frames[s].index] if market_ok(bench, d, sma_days) else []
    return simulate_rotation(frames, choose, dates, capital, len(names), costs, cash_yield_pct, every)


def index_trend(bench_name: str, bench: pd.DataFrame, dates, capital, costs, cash_yield_pct,
                sma_days: int = 200, every: str = "monthly") -> Result:
    def choose(d, current):
        return [bench_name] if market_ok(bench, d, sma_days) else []
    return simulate_rotation({bench_name: bench}, choose, dates, capital, 1, costs, cash_yield_pct, every)


ROTATION_STRATEGIES = {"momentum_rotation": momentum_rotation, "high52_rotation": high52_rotation,
                       "clenow_rotation": clenow_rotation, "lowvol_rotation": lowvol_rotation,
                       "equal_weight_trend": equal_weight_trend}

ROTATION_DESCRIPTIONS = {
    "high52_rotation": "52-week high: top 10 closest to their 52-week high, monthly, cash when Nifty < 200-day",
    "clenow_rotation": "Clenow Stocks on the Move: 90-day regression slope x R2, weekly, index filter on new buys",
    "lowvol_rotation": "Low volatility: 10 least volatile stocks (1 year), monthly",
    "equal_weight_trend": "Equal weight: all stocks while Nifty > 200-day average (monthly check), else liquid fund",
    "momentum_rotation": "Momentum: top 10 by 6/12-month risk-adjusted return, trend-template filter, "
                         "monthly review, cash when Nifty < 200-day average",
    "index_trend": "Index trend: hold Nifty ETF above its 200-day average (checked monthly), else liquid fund",
}
