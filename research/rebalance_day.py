"""Addendum 8 (research/PREREGISTRATION.md): does the review day of the month matter?

  python research/rebalance_day.py
Writes research/rebalance_day_results.csv and prints the table and the verdict.

The live momentum rotation and the live ETF trend are run unchanged except for the session of the month on which
they decide (orders at the next open, as live). The last session is the live rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import backtest.rotation as rot                                      # noqa: E402
from backtest.engine import Costs                                    # noqa: E402
from backtest.etf import gtaa                                        # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe              # noqa: E402
from backtest.rotation import momentum_rotation                     # noqa: E402
from swing_research import CAP, CASH, ETFS, load                     # noqa: E402

_original = rot._rebalance_days


def review_days(dates: pd.DatetimeIndex, every: str):
    """'start:k' = k-th session of each month; 'end:k' = k-th last (end:1 = last); 'twice' = last + 10th;
    'multi:a,b' = the a-th and b-th sessions (exploratory)."""
    if every.startswith("multi:"):
        return set().union(*[review_days(dates, f"start:{k}") for k in every[6:].split(",")])
    if not (every.startswith(("start:", "end:")) or every == "twice"):
        return _original(dates, every)
    s = pd.Series(dates, index=dates)
    out = set()
    for _, g in s.groupby(dates.to_period("M")):
        g = list(g)
        if every == "twice":
            out.add(g[-1])
            out.add(g[min(9, len(g) - 1)])
        elif every.startswith("start:"):
            out.add(g[min(int(every[6:]), len(g)) - 1])
        else:
            out.add(g[max(len(g) - int(every[4:]), 0)])
    # the last month in the data may be incomplete: drop days after the data ends (no next open to trade at)
    out.discard(dates[-1])
    return out


rot._rebalance_days = review_days                                    # gtaa and momentum both use simulate_rotation


def stats(eq: pd.Series, mid) -> dict:
    a, b = eq.loc[:mid], eq.loc[mid:]
    return dict(cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq),
                sh1=sharpe(a), sh2=sharpe(b), cagr1=cagr(a), cagr2=cagr(b), dd1=max_drawdown(a), dd2=max_drawdown(b))


def main():
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    bench = frames["NIFTYBEES"]
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= pd.Timestamp("2016-01-01")]
    mid = dates[len(dates) // 2]
    costs = Costs()
    variants = ([("end:1 (live)", "end:1")] + [(f"end:{k}", f"end:{k}") for k in range(2, 6)]
                + [(f"start:{k}", f"start:{k}") for k in range(1, 21)]
                + [("twice a month", "twice"), ("every 2 weeks", "biweekly"), ("weekly", "weekly")])
    rows = []
    for label, every in variants:
        m = momentum_rotation(stocks, bench, dates, CAP, costs, CASH, every=every)
        e = gtaa(frames, dates, CAP, every=every)
        blend = 0.82 * m.equity / m.equity.iloc[0] + 0.18 * e.equity / e.equity.iloc[0]   # the live split
        for name, eq, res in (("momentum", m.equity, m), ("etf_trend", e.equity, e), ("live blend", blend, None)):
            r = dict(variant=label, strategy=name, **stats(eq, mid))
            if res is not None:
                r.update(trades=len(res.trades), costs=res.costs_paid)
            rows.append(r)
        print(f"{label:<16} momentum {rows[-3]['cagr']:6.2f}% sh {rows[-3]['sharpe']:.2f} | "
              f"etf {rows[-2]['cagr']:6.2f}% sh {rows[-2]['sharpe']:.2f}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "rebalance_day_results.csv", index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 300)
    print(f"\nTest {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}")
    for name in ("momentum", "etf_trend", "live blend"):
        t = out[out.strategy == name].drop(columns="strategy").set_index("variant")
        print(f"\n=== {name} ===")
        print(t.round(2).to_string())
        verdict(t)


def verdict(t: pd.DataFrame) -> None:
    base = t.loc["end:1 (live)"]
    days = [v for v in t.index if v.startswith(("start:", "end:"))]
    beats = {v: (t.loc[v, "sh1"] >= base.sh1 + 0.10 and t.loc[v, "sh2"] >= base.sh2 + 0.10
                 and t.loc[v, "maxdd"] >= base.maxdd - 2.0) for v in days}
    # consecutive blocks along the calendar: 5th-last .. last, then 1st .. 20th (end and start are adjacent)
    order = [f"end:{k}" for k in range(5, 1, -1)] + ["end:1 (live)"] + [f"start:{k}" for k in range(1, 21)]
    run, best = 0, 0
    for v in order:
        run = run + 1 if beats.get(v) else 0
        best = max(best, run)
    sh = t.loc[days, "sharpe"]
    cg = t.loc[days, "cagr"]
    print(f"Across the 25 review days: CAGR {cg.min():.2f}% .. {cg.max():.2f}% (live {base.cagr:.2f}%), "
          f"Sharpe {sh.min():.2f} .. {sh.max():.2f} (live {base.sharpe:.2f}); live day ranks "
          f"{int((sh > base.sharpe).sum()) + 1} of {len(days)} on Sharpe")
    print(f"Days beating the live day by >=0.10 Sharpe in both halves: "
          f"{[v for v, ok in beats.items() if ok] or 'none'}; longest consecutive block {best} "
          f"-> {'CHANGE candidate' if best >= 3 else 'keep the last session'}")
    for v in ("twice a month", "every 2 weeks", "weekly"):
        if v in t.index:
            ok = t.loc[v, "sh1"] > base.sh1 and t.loc[v, "sh2"] > base.sh2
            print(f"  {v}: Sharpe {t.loc[v, 'sh1']:.2f}/{t.loc[v, 'sh2']:.2f} vs {base.sh1:.2f}/{base.sh2:.2f} "
                  f"-> {'better in both halves' if ok else 'not better'}")


def explore():
    """EXPLORATORY (not pre-registered; asked after seeing the main result): can the timing luck be reduced?
    At the live momentum size (Rs 3.45L) and real DP charges:
      - monthly, one portfolio, every review day        -> the spread to beat
      - 2 tranches: half the money each, reviewed on sessions k and k+10 (each tranche monthly)
      - 3 tranches: a third each, sessions k, k+7, k+14
      - whole portfolio reviewed twice a month on sessions k and k+10
    Each family is run for every starting session k, so its spread across k can be compared with monthly."""
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    bench = frames["NIFTYBEES"]
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= pd.Timestamp("2016-01-01")]
    mid = dates[len(dates) // 2]
    cap, costs = 345_000, Costs(dp_per_sell=21.83)
    cache = {}

    def sleeve(every, capital):
        key = (every, capital)
        if key not in cache:
            cache[key] = momentum_rotation(stocks, bench, dates, capital, costs, CASH, every=every).equity
        return cache[key]

    rows = []
    for k in range(1, 21):
        rows.append(dict(family="monthly", k=k, **stats(sleeve(f"start:{k}", cap), mid)))
    for k in range(1, 11):
        eq = sleeve(f"start:{k}", cap / 2) + sleeve(f"start:{k + 10}", cap / 2)
        rows.append(dict(family="2 tranches", k=k, **stats(eq, mid)))
        rows.append(dict(family="twice a month", k=k, **stats(sleeve(f"multi:{k},{k + 10}", cap), mid)))
        print(f"k={k} done", flush=True)
    for k in range(1, 8):
        eq = sum(sleeve(f"start:{k + 7 * i}", cap / 3) for i in range(3))
        rows.append(dict(family="3 tranches", k=k, **stats(eq, mid)))
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "rebalance_day_explore.csv", index=False)
    g = out.groupby("family")[["cagr", "maxdd", "sharpe", "sh1", "sh2"]]
    summary = pd.concat({"mean": g.mean(), "min": g.min(), "max": g.max()}, axis=1).round(2)
    pd.set_option("display.width", 220)
    print(summary.to_string())


def tranches():
    """Addendum 9 (1): the exact proposed configuration - tranches on the 7th, 14th and last session - vs the
    control (one portfolio, last session), Rs 3.45L, real DP charge."""
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    bench = frames["NIFTYBEES"]
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= pd.Timestamp("2016-01-01")]
    mid = dates[len(dates) // 2]
    cap, costs = 345_000, Costs(dp_per_sell=21.83)
    legs = {ev: momentum_rotation(stocks, bench, dates, cap / 3, costs, CASH, every=ev)
            for ev in ("start:7", "start:14", "end:1")}
    control = momentum_rotation(stocks, bench, dates, cap, costs, CASH, every="end:1")
    eq = sum(r.equity for r in legs.values())
    rows = [dict(variant="3 tranches (7th, 14th, last)", trades=sum(len(r.trades) for r in legs.values()),
                 costs=sum(r.costs_paid for r in legs.values()), **stats(eq, mid)),
            dict(variant="control: one portfolio, last session", trades=len(control.trades),
                 costs=control.costs_paid, **stats(control.equity, mid))]
    for ev, r in legs.items():
        rows.append(dict(variant=f"  tranche {ev} alone (1/3 capital)", trades=len(r.trades), costs=r.costs_paid,
                         **stats(r.equity, mid)))
    out = pd.DataFrame(rows).set_index("variant")
    out.to_csv(ROOT / "research" / "tranche_results.csv")
    pd.set_option("display.width", 220)
    print(f"Test {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}")
    print(out.round(2).to_string())
    by_year = pd.DataFrame({"3 tranches": eq, "control": control.equity}).resample("YE").last()
    by_year.loc[pd.Timestamp("2015-12-31")] = [cap, cap]
    print((by_year.sort_index().pct_change().dropna() * 100).round(1).to_string())


if __name__ == "__main__":
    if "--explore" in sys.argv:
        explore()
    elif "--tranches" in sys.argv:
        tranches()
    else:
        main()
