"""Managing an existing stock portfolio: hold vs monthly exit/re-entry rules, after costs and tax.
Pre-registered: research/PREREGISTRATION.md, Addendum 5.

  python research/holdings_research.py     -> research/holdings_results.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
from swing_research import ETFS, load                                  # noqa: E402

V0, SLOTS, H = 353_000.0, 13, 30
COST = 0.0027                       # per side
EXEMPT = 125_000.0
OWNER = ["HCLTECH", "INFY", "RELIANCE", "HDFCBANK", "TCS", "LT", "ITC", "ICICIBANK", "SHRIRAMFIN"]


def monthly(frames):
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    C = pd.DataFrame({s: f["close"].resample("ME").last() for s, f in stocks.items()})
    S = {n: pd.DataFrame({s: f["close"].rolling(n).mean().resample("ME").last() for s, f in stocks.items()})
         for n in (150, 200, 250)}
    nb = frames["NIFTYBEES"]["close"]
    mkt = (nb.resample("ME").last() > nb.rolling(200).mean().resample("ME").last()).reindex(C.index)
    mom = C.shift(1) / C.shift(12) - 1
    return C, S, mkt.fillna(False), mom


class Tax:
    def __init__(self):
        self.st = self.lt = 0.0          # realized this financial year
        self.cf_st = self.cf_lt = 0.0    # losses carried forward
        self.paid = 0.0

    def realize(self, gain, months_held):
        if months_held < 12:
            self.st += gain
        else:
            self.lt += gain

    def year_end(self) -> float:
        st, lt = self.st, self.lt
        st_loss, lt_loss = max(0.0, -st), max(0.0, -lt)
        st, lt = max(0.0, st), max(0.0, lt)
        u = min(st_loss, lt); lt -= u; st_loss -= u
        u = min(self.cf_st, st); st -= u; self.cf_st -= u
        u = min(self.cf_st, lt); lt -= u; self.cf_st -= u
        u = min(self.cf_lt, lt); lt -= u; self.cf_lt -= u
        self.cf_st += st_loss
        self.cf_lt += lt_loss
        tax = 0.20 * st + 0.125 * max(0.0, lt - EXEMPT)
        self.st = self.lt = 0.0
        self.paid += tax
        return tax


def run(rule, syms, s0, C, S, mkt, mom, y, months=H, v0=V0):
    """Returns (pre-tax final, after-tax final, monthly value path)."""
    idx = C.index
    cv, ok = C.values, np.isfinite(C.values)
    col = {s: i for i, s in enumerate(C.columns)}
    gy = (1 + y) ** (1 / 12) - 1
    tax = Tax()
    slots = []
    for s in syms:
        amt = v0 / len(syms)
        slots.append(dict(sym=s, val=amt / (1 + COST), cash=0.0, inn=True, basis=amt, bm=s0, peak=cv[s0, col[s]],
                          why=""))

    def sell(sl, m):
        proceeds = sl["val"] * (1 - COST)
        tax.realize(proceeds - sl["basis"], m - sl["bm"])
        sl.update(cash=proceeds, val=0.0, inn=False)

    def buy(sl, m, sym=None):
        if sym:
            sl["sym"] = sym
        sl.update(val=sl["cash"] / (1 + COST), basis=sl["cash"], cash=0.0, inn=True, bm=m,
                  peak=cv[m, col[sl["sym"]]], why="")

    path = [v0]
    for m in range(s0 + 1, s0 + months + 1):
        for sl in slots:
            j = col[sl["sym"]]
            if sl["inn"]:
                if ok[m, j] and ok[m - 1, j]:
                    sl["val"] *= cv[m, j] / cv[m - 1, j]
                sl["peak"] = max(sl["peak"], cv[m, j]) if ok[m, j] else sl["peak"]
            else:
                sl["cash"] *= 1 + gy
        last = m == s0 + months
        if not last:
            kind, n, band = rule
            if kind in ("T", "TRAIL"):
                sma = S[n].values
                for sl in slots:
                    j = col[sl["sym"]]
                    c, a = cv[m, j], sma[m, j]
                    if not (np.isfinite(c) and np.isfinite(a)):
                        continue
                    if sl["inn"]:
                        hit = c < a * (1 - band) if kind == "T" else c < 0.75 * sl["peak"]
                        if hit:
                            sell(sl, m)
                    elif c > a * (1 + band):
                        buy(sl, m)
            elif kind == "MKT":
                up = bool(mkt.iloc[m])
                for sl in slots:
                    if sl["inn"] and not up:
                        sell(sl, m)
                    elif not sl["inn"] and up:
                        buy(sl, m)
            elif kind == "MOM":
                use_mkt = n == 1
                up = bool(mkt.iloc[m]) if use_mkt else True
                scores = pd.Series(mom.values[m], index=C.columns).dropna().sort_values(ascending=False)
                top = list(scores.index[:25])
                held = {sl["sym"] for sl in slots if sl["inn"]}
                cands = [s for s in scores.index if ok[m, col[s]] and s not in held]
                for sl in slots:
                    if sl["inn"] and (not up or (len(scores) >= 30 and sl["sym"] not in top)):
                        held.discard(sl["sym"])
                        sell(sl, m)
                        if up and cands:
                            buy(sl, m, cands.pop(0))
                            held.add(sl["sym"])
                    elif not sl["inn"] and up and cands:
                        buy(sl, m, cands.pop(0))
                        held.add(sl["sym"])
        if idx[m].month == 3:                               # financial year ends in March
            t = tax.year_end()
            if t:
                tot = sum(sl["val"] + sl["cash"] for sl in slots)
                for sl in slots:
                    f = 1 - t / tot
                    sl["val"] *= f
                    sl["cash"] *= f
        path.append(sum(sl["val"] + sl["cash"] for sl in slots))
    pre = 0.0
    for sl in slots:                                        # sell everything at the end
        if sl["inn"]:
            sell(sl, s0 + months)
        pre += sl["cash"]
    after = pre - tax.year_end()
    return pre, after, path


RULES = {"H0 hold": ("H", 0, 0), "T200": ("T", 200, 0.0), "T200 band3%": ("T", 200, 0.03),
         "T150": ("T", 150, 0.0), "T250": ("T", 250, 0.0), "T150 band3%": ("T", 150, 0.03),
         "T250 band3%": ("T", 250, 0.03), "MKT": ("MKT", 0, 0), "TRAIL25": ("TRAIL", 200, 0.0),
         "MOM": ("MOM", 0, 0), "MOM+MKT": ("MOM", 1, 0)}


def maxdd(p):
    p = np.asarray(p)
    return float((p / np.maximum.accumulate(p) - 1).min() * 100)


def main(n_portfolios=2000, seed=7):
    frames = load()
    C, S, mkt, mom = monthly(frames)
    idx = C.index
    starts = [i for i, d in enumerate(idx) if pd.Timestamp("2016-01-01") <= d <= pd.Timestamp("2023-12-31")
              and i + H < len(idx)]
    rng = np.random.default_rng(seed)
    samples = []
    while len(samples) < n_portfolios:
        s0 = int(rng.choice(starts))
        pool = [s for s in C.columns if np.isfinite(C.values[s0 - 12:s0 + H + 1, C.columns.get_loc(s)]).all()]
        if len(pool) < SLOTS:
            continue
        samples.append((s0, list(rng.choice(pool, SLOTS, replace=False))))
    owner = [(s0, OWNER) for s0 in range(len(idx)) if idx[s0] >= pd.Timestamp("2016-01-01") and s0 + H < len(idx)]
    rows = []
    for group, sample in (("random 13-stock portfolios", samples), ("owner's 9 Nifty 50 names", owner)):
        for y in (0.0, 0.06):
            base = None
            for name, rule in RULES.items():
                res = [run(rule, syms, s0, C, S, mkt, mom, y) + (s0,) for s0, syms in sample]
                pre = np.array([r[0] for r in res]); aft = np.array([r[1] for r in res])
                dd = np.array([maxdd(r[2]) for r in res]); s0s = np.array([r[3] for r in res])
                ann = (aft / V0) ** (12 / H) - 1
                first = np.array([idx[s] < pd.Timestamp("2020-01-01") for s in s0s])
                if base is None:
                    base = ann
                row = dict(group=group, cash_yield=y, rule=name,
                           med_after_tax=np.median(ann) * 100, p10_after_tax=np.percentile(ann, 10) * 100,
                           med_pre_tax=np.median((pre / V0) ** (12 / H) - 1) * 100,
                           med_maxdd=np.median(dd), p90_maxdd=np.percentile(dd, 10),
                           beats_hold_pct=(ann > base).mean() * 100,
                           h1_med=np.median(ann[first]) * 100, h1_p10=np.percentile(ann[first], 10) * 100,
                           h2_med=np.median(ann[~first]) * 100, h2_p10=np.percentile(ann[~first], 10) * 100,
                           n=len(ann))
                rows.append(row)
                print(f"{group[:14]:<14} cash {y:.0%} {name:<12} after-tax median {row['med_after_tax']:5.1f}% "
                      f"worst10% {row['p10_after_tax']:6.1f}%  maxDD med {row['med_maxdd']:6.1f}% "
                      f"(worst10% {row['p90_maxdd']:6.1f}%)  beats hold {row['beats_hold_pct']:4.0f}%  "
                      f"halves med {row['h1_med']:5.1f}/{row['h2_med']:5.1f} p10 {row['h1_p10']:6.1f}/{row['h2_p10']:6.1f}",
                      flush=True)
    pd.DataFrame(rows).to_csv(ROOT / "research" / "holdings_results.csv", index=False)


if __name__ == "__main__":
    main()
