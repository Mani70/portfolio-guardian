"""Swing research on the 12-year cache, judged against research/PREREGISTRATION.md.

  python research/swing_research.py
Writes research/swing_results.csv and prints the verdict per strategy.
"""
from __future__ import annotations

import glob
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data import adjust_splits, suspicious_jumps            # noqa: E402
from backtest.engine import Costs, simulate                          # noqa: E402
from backtest.etf import dual_momentum, etf_rsi2, gtaa, turn_of_month  # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe              # noqa: E402
from backtest.rotation import (ROTATION_STRATEGIES, index_trend)     # noqa: E402
from backtest.strategies import ENGINE_DEFAULTS, NEEDS_RS, STRATEGIES, rs_rating  # noqa: E402

CAP, CASH, FD = 500_000, 6.0, 6.2
ETFS = {"NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES", "MON100", "LIQUIDBEES", "ITBEES",
        "PHARMABEES", "PSUBNKBEES", "CPSEETF"}


def load():
    frames = {}
    for p in sorted(glob.glob(str(ROOT / "cache" / "history" / "*.csv"))):
        s = os.path.basename(p)[:-4].replace("M_M", "M&M")
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        df = adjust_splits(df)[0]
        j = suspicious_jumps(df)
        if j:
            df = df.loc[j[-1][0]:]
        frames[s] = df
    return frames


def summary(name, eq: pd.Series, trades: int, mid, ew_sharpe=None, group="") -> dict:
    a, b = eq.loc[:mid], eq.loc[mid:]
    return dict(strategy=name, group=group, cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq),
                h1=cagr(a), h2=cagr(b), trades=trades)


def main():
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    bench = frames["NIFTYBEES"]
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    start = pd.Timestamp("2016-01-01")
    dates = full[full >= start]
    mid = dates[len(dates) // 2]
    costs = Costs()
    rows = []

    # benchmarks
    nb = CAP * bench["close"].reindex(dates).ffill() / bench["close"].reindex(dates).ffill().iloc[0]
    c = pd.DataFrame({s: f["close"] for s, f in stocks.items()}).reindex(dates).ffill()
    ew = CAP * (c / c.bfill().iloc[0]).mean(axis=1)
    rows.append(summary("NIFTYBEES hold", nb, 0, mid, group="benchmark"))
    rows.append(summary("Equal-weight hold (49 stocks)", ew, 0, mid, group="benchmark"))

    # stock strategies, default parameters
    rs = rs_rating(pd.DataFrame({s: f["close"] for s, f in stocks.items()}))
    for name, fn in STRATEGIES.items():
        extra = (lambda s: {"rs": rs[s]}) if name in NEEDS_RS else (lambda s: {})
        sig = {s: fn(df, **extra(s)).loc[dates[0]:] for s, df in stocks.items()}
        eng = ENGINE_DEFAULTS.get(name, {})
        res = simulate(sig, CAP, 5, costs, 3.0, int(eng.get("max_hold", 0)), CASH, float(eng.get("stop_pct", 0)))
        rows.append(summary(name, res.equity, len(res.trades), mid, group="stocks"))
    for name, fn in ROTATION_STRATEGIES.items():
        res = fn(stocks, bench, dates, CAP, costs, CASH)
        rows.append(summary(name, res.equity, len(res.trades), mid, group="stocks"))
    res = index_trend("NIFTYBEES", bench, dates, CAP, costs, CASH)
    rows.append(summary("index_trend", res.equity, len(res.trades), mid, group="etf"))

    # ETF strategies
    for name, res in [("dual_momentum", dual_momentum(frames, dates, CAP)),
                      ("dual_momentum top2", dual_momentum(frames, dates, CAP, top=2)),
                      ("gtaa", gtaa(frames, dates, CAP)),
                      ("etf_rsi2", etf_rsi2(frames, dates, CAP)),
                      ("turn_of_month", turn_of_month(frames, dates, CAP))]:
        rows.append(summary(name, res.equity, len(res.trades), mid, group="etf"))

    # neighbours of the leading candidates
    from backtest.rotation import equal_weight_trend, high52_rotation, momentum_rotation
    for slots in (5, 10, 15):
        for ex in (slots, 2 * slots):
            res = momentum_rotation(stocks, bench, dates, CAP, costs, CASH, slots=slots, exit_rank=ex)
            rows.append(summary(f"momentum slots{slots} exit{ex}", res.equity, len(res.trades), mid, group="nbr"))
    for every in ("biweekly", "quarterly"):
        res = momentum_rotation(stocks, bench, dates, CAP, costs, CASH, every=every)
        rows.append(summary(f"momentum {every}", res.equity, len(res.trades), mid, group="nbr"))
    res = momentum_rotation(stocks, bench, dates, CAP, costs, CASH, market_filter=False)
    rows.append(summary("momentum no filter", res.equity, len(res.trades), mid, group="nbr"))
    for n in (150, 200, 250):
        for every in ("weekly", "monthly"):
            res = equal_weight_trend(stocks, bench, dates, CAP, costs, CASH, sma_days=n, every=every)
            rows.append(summary(f"eq-weight trend {n}d {every}", res.equity, len(res.trades), mid, group="nbr"))
            res = index_trend("NIFTYBEES", bench, dates, CAP, costs, CASH, sma_days=n, every=every)
            rows.append(summary(f"index trend {n}d {every}", res.equity, len(res.trades), mid, group="nbr"))
    for lb in (126, 189, 252):
        res = dual_momentum(frames, dates, CAP, lookback=lb)
        rows.append(summary(f"dual momentum {lb}d", res.equity, len(res.trades), mid, group="nbr"))
    for n in (150, 200, 250):
        res = gtaa(frames, dates, CAP, sma_days=n)
        rows.append(summary(f"gtaa {n}d", res.equity, len(res.trades), mid, group="nbr"))
    for b in (5, 10, 15):
        res = etf_rsi2(frames, dates, CAP, buy_below=b)
        rows.append(summary(f"etf_rsi2 <{b}", res.equity, len(res.trades), mid, group="nbr"))

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "swing_results.csv", index=False)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)
    print(f"Test {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}")
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
