"""Addendum 10 (research/PREREGISTRATION.md): park idle cash in a liquid ETF?

  python research/cash_parking.py
Writes research/cash_parking_results.csv and prints the table and the verdict.

The live rules (momentum rotation; ETF trend) re-simulated with explicit cash handling:
  park     idle cash >= threshold at the evening -> liquid ETF at the next open; the whole ETF is sold with the
           evening's sells when a review has buys to make. It earns the overnight rate minus the expense ratio.
  delay    "none": every order at the next open (research timing);
           "live": a buy fills one session later when the money for it was not free at the evening it was
           decided (the live engine: buys funded by same-night sells, or by the ETF sold that night, wait a day).
"""
from __future__ import annotations

import math
import sys
from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from backtest.engine import Costs                                    # noqa: E402
from backtest.etf import ETF_COSTS                                   # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe              # noqa: E402
from backtest.rotation import market_ok, momentum_scores             # noqa: E402
from swing_research import ETFS, load                                # noqa: E402

THRESHOLD = 25_000
TER = 0.23
# approximate overnight rate (TREPS / NIFTY 1D rate), % a year, from RBI policy decisions: repo - 0.25,
# but the reverse repo (3.35%) from Apr 2020 until the standing deposit facility in Apr 2022. Approximate
# after mid-2025.
RATE_PATH = [(date(2016, 1, 1), 6.50), (date(2016, 4, 5), 6.25), (date(2016, 10, 4), 6.00), (date(2017, 8, 2), 5.75),
             (date(2018, 6, 6), 6.00), (date(2018, 8, 1), 6.25), (date(2019, 2, 7), 6.00), (date(2019, 4, 4), 5.75),
             (date(2019, 6, 6), 5.50), (date(2019, 8, 7), 5.15), (date(2019, 10, 4), 4.90), (date(2020, 3, 27), 4.00),
             (date(2020, 4, 17), 3.35), (date(2022, 4, 8), 3.75), (date(2022, 5, 4), 4.15), (date(2022, 6, 8), 4.65),
             (date(2022, 8, 5), 5.15), (date(2022, 9, 30), 5.65), (date(2022, 12, 7), 6.00), (date(2023, 2, 8), 6.25),
             (date(2025, 2, 7), 6.00), (date(2025, 4, 9), 5.75), (date(2025, 6, 6), 5.25), (date(2025, 12, 5), 5.00)]
_KEYS = [d for d, _ in RATE_PATH]


def overnight(d) -> float:
    return RATE_PATH[max(0, bisect_right(_KEYS, pd.Timestamp(d).date()) - 1)][1]


def park_costs(value: float, side: str) -> float:
    """Liquid ETF: Rs 10 brokerage + 18% GST, exchange fees, 0.02% slippage; stamp duty on buys, DP on sells."""
    c = 10 * 1.18 + value * 0.0000297 * 1.18 + value * 0.0002
    return c + (value * 0.00015 if side == "buy" else 21.83)


def review_days(dates: pd.DatetimeIndex) -> set:
    return {dates[i] for i in range(len(dates) - 1) if dates[i].month != dates[i + 1].month}


def simulate(frames: Dict[str, pd.DataFrame], choose: Callable, dates: pd.DatetimeIndex, capital: float,
             slots: int, costs: Costs, park: bool = False, delay: str = "none",
             yield_fn: Callable = lambda d: 0.0, flat_cash_yield: float = 0.0) -> dict:
    """Same order logic as backtest.rotation.simulate_rotation (sells first at the open, equal-weight buys), plus
    explicit idle cash, an optional parking ETF and the live engine's one-session buy delay."""
    slip = costs.slippage_pct / 100
    rows = {s: f[["open", "close"]].to_dict("index") for s, f in frames.items()}
    review = review_days(dates)
    cash, parked, holdings = capital, 0.0, {}
    last_close: Dict[str, float] = {}
    pending = None                 # target decided at the previous close
    waiting: List[str] = []        # buys deferred one session (delay="live")
    unpark_next = False
    park_next = False
    trades = park_trades = 0
    paid = 0.0
    eq, prev = [], None

    def mark():
        return sum(q[0] * last_close.get(s, q[1]) for s, q in holdings.items())

    def buy(names, d, equity_now):
        nonlocal cash, paid, trades
        for s in names:
            r = rows[s].get(d)
            if r is None or len(holdings) >= slots or s in holdings:
                continue
            px = r["open"] * (1 + slip)
            qty = math.floor(min(equity_now / slots, cash) / (px * 1.003))
            if qty < 1:
                continue
            c = costs.buy(qty * px)
            if qty * px + c > cash:
                continue
            cash -= qty * px + c
            paid += c
            trades += 1
            holdings[s] = [qty, px]

    for d in dates:
        if prev is not None:
            days = (d - prev).days
            if parked:
                parked *= (1 + max(0.0, yield_fn(d)) / 100) ** (days / 365.25)
            if flat_cash_yield and cash > 0:
                cash *= (1 + flat_cash_yield / 100) ** (days / 365.25)
        prev = d
        # ---- the open: the parking ETF first (its money is usable at this open), then stocks
        if unpark_next and parked:
            c = park_costs(parked, "sell")
            cash += parked - c
            paid += c
            parked, park_trades = 0.0, park_trades + 1
        unpark_next = False
        if waiting:                                            # yesterday's deferred buys
            buy(waiting, d, cash + parked + mark())
            waiting = []
        if pending is not None:
            target, free_at_close = pending
            pending = None
            for s in [s for s in holdings if s not in target]:
                r = rows[s].get(d)
                if r is None:
                    continue
                qty, _ = holdings.pop(s)
                px = r["open"] * (1 - slip)
                c = costs.sell(qty * px)
                cash += qty * px - c
                paid += c
                trades += 1
            new = [s for s in target if s not in holdings]
            if delay == "live":
                # only what was free at the evening goes in now (the engine checks funds when it places the order)
                now_list, budget, equity_now = [], free_at_close, cash + parked + mark()
                for s in new:
                    r = rows[s].get(d)
                    need = min(equity_now / slots, cash) if r is not None else 0
                    if budget >= need > 0:
                        now_list.append(s)
                        budget -= need
                    else:
                        waiting.append(s)
                buy(now_list, d, equity_now)
            else:
                buy(new, d, cash + parked + mark())
        if park_next and park and cash >= THRESHOLD:
            value = cash - park_costs(cash, "buy")
            paid += cash - value
            parked, cash, park_trades = parked + value, 0.0, park_trades + 1
        park_next = False
        # ---- the close
        for s, rmap in rows.items():
            r = rmap.get(d)
            if r is not None:
                last_close[s] = r["close"]
        if d in review:
            target = choose(d, list(holdings))
            buys = [s for s in target if s not in holdings]
            pending = (target, cash)                          # money free tonight (the ETF is not free)
            if buys and parked:
                unpark_next = True                            # the ETF goes with tonight's sells
        if park and not waiting and pending is None and cash >= THRESHOLD and not unpark_next:
            park_next = True
        eq.append(cash + parked + mark())
    return dict(equity=pd.Series(eq, index=dates, dtype=float), trades=trades, park_trades=park_trades, costs=paid)


def momentum_choose(stocks, bench):
    def choose(d, current):
        if not market_ok(bench, d):
            return []
        ranked = list(momentum_scores(stocks, d).index)
        keep = [s for s in current if s in ranked[:20]]
        return keep + [s for s in ranked if s not in keep][:max(0, 10 - len(keep))]
    return choose


def etf_choose(frames, assets=("NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES")):
    def choose(d, current):
        out = []
        for a in assets:
            c = frames[a]["close"].loc[:d]
            if len(c) >= 200 and c.iloc[-1] > c.iloc[-200:].mean():
                out.append(a)
        return out
    return choose


def stats(eq: pd.Series, mid) -> dict:
    a, b = eq.loc[:mid], eq.loc[mid:]
    return dict(cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq), sh1=sharpe(a), sh2=sharpe(b),
                cagr1=cagr(a), cagr2=cagr(b))


def main():
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    bench = frames["NIFTYBEES"]
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= pd.Timestamp("2016-01-01")]
    mid = dates[len(dates) // 2]
    path = lambda d: overnight(d) - TER                      # noqa: E731
    minus1 = lambda d: overnight(d) - 1 - TER                 # noqa: E731
    flat4 = lambda d: 4.0                                     # noqa: E731
    avg = sum(path(d) for d in dates) / len(dates)
    books = {"momentum": (stocks, momentum_choose(stocks, bench), 345_000, 10, Costs(dp_per_sell=21.83)),
             "etf_trend": ({a: frames[a] for a in ("NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES")}, etf_choose(frames),
                           75_600, 4, ETF_COSTS)}
    variants = [("research timing, cash earns 6%", dict(flat_cash_yield=6.0)),
                ("(a) idle cash, research timing", {}),
                ("(a) idle cash, live timing (today's engine)", dict(delay="live")),
                ("(b) parked, live timing", dict(park=True, delay="live", yield_fn=path)),
                ("(b) parked, live timing, rate -1 pt", dict(park=True, delay="live", yield_fn=minus1)),
                ("(b) parked, live timing, flat 4%", dict(park=True, delay="live", yield_fn=flat4)),
                ("(c) parked, research timing", dict(park=True, yield_fn=path))]
    rows = []
    for book, (fr, choose, cap, slots, costs) in books.items():
        for label, kw in variants:
            r = simulate(fr, choose, dates, cap, slots, costs, **kw)
            rows.append(dict(book=book, variant=label, trades=r["trades"], park_trades=r["park_trades"],
                             costs=round(r["costs"]), **stats(r["equity"], mid)))
            print(f"{book:<10} {label:<45} {rows[-1]['cagr']:6.2f}%  sh {rows[-1]['sharpe']:.2f}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "cash_parking_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"\nTest {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}; "
          f"average parking yield (path - expense ratio) {avg:.2f}%")
    for book in books:
        t = out[out.book == book].drop(columns="book").set_index("variant")
        print(f"\n=== {book} ===\n{t.round(2).to_string()}")
        a = t.loc["(a) idle cash, live timing (today's engine)"]
        for v in ("(b) parked, live timing", "(b) parked, live timing, rate -1 pt"):
            b = t.loc[v]
            ok = (b.cagr - a.cagr >= 0.5 and b.sh1 > a.sh1 and b.sh2 > a.sh2 and b.maxdd >= a.maxdd - 1.0)
            print(f"{v}: CAGR {b.cagr - a.cagr:+.2f} pt, Sharpe {b.sh1:.2f}/{b.sh2:.2f} vs {a.sh1:.2f}/{a.sh2:.2f}, "
                  f"worst fall {b.maxdd:.1f} vs {a.maxdd:.1f} -> {'PASS' if ok else 'not passed'}")


if __name__ == "__main__":
    main()
