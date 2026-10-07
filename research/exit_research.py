"""Protective exits between the monthly decisions of the two live strategies (PREREGISTRATION Addendum 4).

  python research/exit_research.py      -> research/exit_results.csv
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from backtest.engine import Costs                                       # noqa: E402
from backtest.etf import ETF_COSTS                                      # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe                 # noqa: E402
from backtest.rotation import _rebalance_days, market_ok, momentum_scores  # noqa: E402
from swing_research import ETFS, load                                   # noqa: E402

CAP, CASH = 150_000, 6.0


def sim(frames, choose, dates, slots, costs, fill, exit_rule=None):
    """Monthly rotation with an optional daily exit rule. fill: 'open' (next open) or 'close' (next close)."""
    slip = costs.slippage_pct / 100
    px = {s: f[fill].to_dict() for s, f in frames.items()}
    cl = {s: f["close"].to_dict() for s, f in frames.items()}
    review = _rebalance_days(dates, "monthly")
    cash, hold, last, eq, ntr = CAP, {}, {}, [], 0      # hold: sym -> dict(qty, entry, peak)
    pend_target, pend_exit, prev = None, set(), None
    for d in dates:
        if prev is not None and cash > 0:
            cash *= (1 + CASH / 100) ** ((d - prev).days / 365.25)
        prev = d
        sells = set(pend_exit) | ({s for s in hold if s not in pend_target} if pend_target is not None else set())
        for s in list(sells):
            p = px[s].get(d)
            if s in hold and p == p and p is not None:
                h = hold.pop(s)
                v = h["qty"] * p * (1 - slip)
                cash += v - costs.sell(v)
                ntr += 1
        pend_exit = set()
        if pend_target is not None:
            equity = cash + sum(h["qty"] * last.get(s, h["entry"]) for s, h in hold.items())
            for s in pend_target:
                p = px[s].get(d)
                if s in hold or p is None or p != p or len(hold) >= slots:
                    continue
                p *= 1 + slip
                qty = math.floor(min(equity / slots, cash) / (p * 1.003))
                if qty < 1 or qty * p + costs.buy(qty * p) > cash:
                    continue
                cash -= qty * p + costs.buy(qty * p)
                hold[s] = {"qty": qty, "entry": p, "peak": p}
            pend_target = None
        for s in frames:
            c = cl[s].get(d)
            if c is not None and c == c:
                last[s] = c
                if s in hold:
                    hold[s]["peak"] = max(hold[s]["peak"], c)
        if d in review:
            pend_target = choose(d, list(hold))
        elif exit_rule is not None:
            pend_exit = {s for s, h in hold.items() if s in last and exit_rule(s, d, h, last[s])}
        eq.append(cash + sum(h["qty"] * last.get(s, h["entry"]) for s, h in hold.items()))
    return pd.Series(eq, index=dates), ntr


def overlays(frames, bench, kind):
    sma = {s: f["close"].rolling(200).mean().to_dict() for s, f in frames.items()}
    sma100 = {s: f["close"].rolling(100).mean().to_dict() for s, f in frames.items()}
    bsma = (bench["close"] > bench["close"].rolling(200).mean()).to_dict()
    o = {"base": None}
    for x in (0.10, 0.15, 0.20):
        o[f"trail {x:.0%}"] = lambda s, d, h, c, x=x: c < h["peak"] * (1 - x)
    for x in (0.08, 0.12):
        o[f"stop {x:.0%}"] = lambda s, d, h, c, x=x: c < h["entry"] * (1 - x)
    o["take-profit 25%"] = lambda s, d, h, c: c > h["entry"] * 1.25
    if kind == "etf":
        o["200d exit daily"] = lambda s, d, h, c: c < (sma[s].get(d) or 0)
        o["200d exit weekly"] = lambda s, d, h, c: d.dayofweek == 4 and c < (sma[s].get(d) or 0)
    else:
        o["market filter daily"] = lambda s, d, h, c: bsma.get(d) is False
        o["100d exit daily"] = lambda s, d, h, c: c < (sma100[s].get(d) or 0)
    return o


def main():
    frames = load()
    bench = frames["NIFTYBEES"]
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    etfs = {a: frames[a] for a in ("NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES")}
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= "2016-01-01"]
    mid = dates[len(dates) // 2]

    def gtaa_choose(d, cur):
        return [a for a, f in etfs.items()
                if len(f["close"].loc[:d]) >= 200 and f["close"].loc[:d].iloc[-1] > f["close"].loc[:d].iloc[-200:].mean()]

    def mom_choose(d, cur):
        if not market_ok(bench, d):
            return []
        ranked = list(momentum_scores(stocks, d).index)
        keep = [s for s in cur if s in ranked[:20]]
        return keep + [s for s in ranked if s not in keep][:max(0, 10 - len(keep))]

    rows = []
    for name, fr, choose, slots, costs, fill, kind in (
            ("ETF trend", etfs, gtaa_choose, 4, ETF_COSTS, "close", "etf"),
            ("Momentum", stocks, mom_choose, 10, Costs(), "open", "stock")):
        for label, rule in overlays(fr, bench, kind).items():
            eq, n = sim(fr, choose, dates, slots, costs, fill, rule)
            rows.append(dict(strategy=name, exit=label, cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq),
                             sharpe_h1=sharpe(eq.loc[:mid]), sharpe_h2=sharpe(eq.loc[mid:]),
                             cagr_h1=cagr(eq.loc[:mid]), cagr_h2=cagr(eq.loc[mid:]), sells=n))
            print(f"{name:<10} {label:<20} CAGR {rows[-1]['cagr']:5.1f}%  DD {rows[-1]['maxdd']:6.1f}%  "
                  f"Sharpe {rows[-1]['sharpe']:.2f} ({rows[-1]['sharpe_h1']:.2f}/{rows[-1]['sharpe_h2']:.2f})  sells {n}",
                  flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "exit_results.csv", index=False)


if __name__ == "__main__":
    main()
