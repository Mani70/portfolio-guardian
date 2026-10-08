"""Addendum 13 / 13a (research/PREREGISTRATION.md): the final rules per horizon on 2005-2026 data.

  python research/allocation20.py [--hist research/data/hist]
Writes research/allocation20_results.csv and prints every table and verdict.

Sleeves (daily, close to close): Nifty 50 / Next 50 / Nifty200 Momentum 30 earn their NSE total-return index (tri.csv,
dividends included) minus an ETF expense ratio; GOLDBEES and MON100 are their own bhavcopy prices, adjusted as in
Addendum 12a/12b; the liquid ETF earns the overnight rate minus 0.23%. Decisions are taken at a month's last close and
traded at the next session's close, with ETF costs on every buy and sell.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import history20 as H                                                # noqa: E402
from backtest.etf import ETF_COSTS                                   # noqa: E402

CAPITAL = 420_000
EXPENSE = {"N50": 0.10, "NN50": 0.20, "M30": 0.45}                   # % a year, rounded up (Addendum 13)
TRI_NAME = {"N50": "Nifty 50", "NN50": "Nifty Next 50", "M30": "Nifty200 Momentum 30"}
ETF_SYM = {"GOLD": "GOLDBEES", "MON100": "MON100"}
L1 = {"N50": 0.45, "NN50": 0.15, "MON100": 0.20, "GOLD": 0.10, "LIQ": 0.10}
L2 = {"N50": 0.30, "NN50": 0.15, "M30": 0.15, "MON100": 0.20, "GOLD": 0.10, "LIQ": 0.10}
BAND = 0.05
ROWS: List[dict] = []


# ---------------------------------------------------------------- data
def load_sleeves(hist: Path) -> pd.DataFrame:
    """Daily total-return levels of every sleeve on the NSE trading calendar (NaN before a sleeve exists)."""
    tri = pd.read_csv(hist / "tri.csv")
    tri["date"] = pd.to_datetime(tri["date"])
    cols = {}
    for k, name in TRI_NAME.items():
        s = tri[tri["index"] == name].drop_duplicates("date").set_index("date")["tri"].sort_index().astype(float)
        cols[k] = s
    lv = pd.DataFrame(cols)
    print("Loading ETF prices (bhavcopy, corporate actions as Addendum 12a/12b) ...", flush=True)
    panel = H.load_panel(hist, fields=("open", "high", "low", "close", "prevclose", "value"), symbols=list(ETF_SYM.values()))
    official = H.official_events(hist, H.symbol_changes(hist))
    H.adjust_prices(panel, official, use_detection=False)             # the four live ETFs are always detected
    for k, sym in ETF_SYM.items():
        lv[k] = panel["close"][sym].astype(float).reindex(lv.index)
    del panel
    lv = lv.loc[lv.index >= pd.Timestamp("2005-01-01")]
    days = lv.index.to_series().diff().dt.days.fillna(1).clip(lower=1)
    rate = np.array([H.overnight(d) - 0.23 for d in lv.index]) / 100
    growth = 1 + rate * days.to_numpy() / 365                         # accrues over weekends and holidays too
    one_d = overnight_index(hist)                                     # Addendum 14: after 2025, NSE's 1D rate index
    if one_d is not None:
        r1 = one_d.reindex(lv.index).ffill().pct_change(fill_method=None)
        later = (lv.index > RATE_PATH_END) & r1.notna().to_numpy()
        growth[later] = 1 + r1.to_numpy()[later] - 0.23 / 100 * days.to_numpy()[later] / 365
    lv["LIQ"] = np.cumprod(growth)
    filled = lv.ffill()                                               # a gap's move lands on the next priced day
    rets = filled.pct_change(fill_method=None)
    for k in lv.columns:
        first = lv[k].first_valid_index()
        rets.loc[rets.index <= first, k] = np.nan                     # no return before a sleeve exists
    for k, e in EXPENSE.items():                                      # expense ratio, charged daily
        rets[k] = rets[k] - e / 100 * days / 365
    return rets


RATE_PATH_END = pd.Timestamp("2025-12-31")                           # the hand-entered overnight rates end here


def overnight_index(hist: Path) -> Optional[pd.Series]:
    """NSE's Nifty 1D Rate Index (overnight money market, total return) from index_history.csv, if downloaded."""
    p = hist / "index_history.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, usecols=["date", "index", "close"])
    df = df[df["index"].str.upper() == "NIFTY 1D RATE INDEX"]
    if df.empty:
        return None
    return df.assign(date=pd.to_datetime(df["date"])).drop_duplicates("date").set_index("date")["close"].sort_index()


def month_ends(idx: pd.DatetimeIndex) -> set:
    return {idx[i] for i in range(len(idx) - 1) if idx[i].month != idx[i + 1].month}


# ---------------------------------------------------------------- simulation
def simulate(rets: pd.DataFrame, target_fn: Callable[[pd.Timestamp, Dict[str, float]], Optional[Dict[str, float]]],
             start: pd.Timestamp, capital: float = CAPITAL) -> dict:
    """target_fn(d, weights_now) at each month's last close -> new target weights (or None = no trade); trades go
    through at the next close. Returns equity, rebalance count and turnover (% of the portfolio a year)."""
    idx = rets.index[rets.index >= start]
    me = month_ends(idx)
    hold: Dict[str, float] = {}                                       # sleeve -> rupees
    cash = capital
    pending: Optional[Dict[str, float]] = None
    eq, n_reb, traded, turn = [], 0, 0.0, 0.0
    for d in idx:
        r = rets.loc[d]
        for k in list(hold):
            x = r.get(k)
            if pd.notna(x):
                hold[k] *= 1 + x
        if pending is not None:                                       # yesterday's decision, at today's close
            total = cash + sum(hold.values())
            want = {k: total * w for k, w in pending.items() if w > 0}
            before = traded
            for k in set(hold) | set(want):
                delta = want.get(k, 0.0) - hold.get(k, 0.0)
                if abs(delta) < 1:
                    continue
                cost = ETF_COSTS.buy(delta) if delta > 0 else ETF_COSTS.sell(-delta)
                hold[k] = hold.get(k, 0.0) + delta
                cash -= delta + cost
                traded += abs(delta)
            hold = {k: v for k, v in hold.items() if v > 1}
            turn += (traded - before) / total if n_reb else 0.0          # the first purchase is not turnover
            n_reb += 1
            pending = None
        total = cash + sum(hold.values())
        eq.append(total)
        if d in me or d == idx[0]:
            w = {k: v / total for k, v in hold.items()} if total > 0 else {}
            pending = target_fn(d, w)
    equity = pd.Series(eq, index=idx)
    years = (idx[-1] - idx[0]).days / 365.25
    return dict(equity=equity, rebalances=n_reb, turnover=turn / years * 100)   # % of the portfolio traded a year


def available(d: pd.Timestamp, rets: pd.DataFrame, k: str) -> bool:
    if k == "LIQ":
        return True
    s = rets[k].loc[:d]
    return s.notna().sum() > 5


def fixed_mix(rets: pd.DataFrame, mix: Dict[str, float], band: float = BAND):
    """Target weights spread pro rata over the sleeves that exist; rebalance all when one is > band off, and at
    every year's last session."""
    def fn(d, w):
        live = {k: v for k, v in mix.items() if available(d, rets, k)}
        tot = sum(live.values())
        tgt = {k: v / tot for k, v in live.items()}
        year_end = d.month == 12
        off = max((abs(w.get(k, 0.0) - tgt.get(k, 0.0)) for k in set(tgt) | set(w)), default=1.0)
        return tgt if (not w or off > band or year_end) else None
    return fn


def hold_one(k: str):
    def fn(d, w):
        return {k: 1.0} if not w else None
    return fn


def trend_brake(rets: pd.DataFrame, k: str, n: int):
    level = (1 + rets[k].fillna(0)).cumprod()

    def fn(d, w):
        c = level.loc[:d]
        on = len(c) < n or c.iloc[-1] > c.iloc[-n:].mean()
        tgt = {k: 1.0} if on else {"LIQ": 1.0}
        cur = max(w, key=w.get) if w else None
        return tgt if cur != next(iter(tgt)) else None
    return fn


# ---------------------------------------------------------------- report
def show(section, rule, res, extra=None):
    r = dict(section=section, rule=rule, **H.stats(res["equity"]), rebalances=res["rebalances"],
             turnover_pct_yr=round(res["turnover"], 1), **(extra or {}))
    ROWS.append(r)
    print(f"  {rule:<52} A: {r.get('cagr_A', np.nan):6.2f}% / {r.get('dd_A', np.nan):6.1f}% / {r.get('sh_A', np.nan):5.2f}"
          f"   B: {r.get('cagr_B', np.nan):6.2f}% / {r.get('dd_B', np.nan):6.1f}% / {r.get('sh_B', np.nan):5.2f}"
          f"   reb {res['rebalances']:>3}  turnover {res['turnover']:5.1f}%/yr", flush=True)
    return r


def verdict(name, ok, why):
    print(f"  -> {name}: {'PASSES' if ok else 'FAILS'} ({why})")
    ROWS.append(dict(section="verdict", rule=name, passes=bool(ok), note=why))
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    a = ap.parse_args(argv)
    rets = load_sleeves(Path(a.hist))
    start = pd.Timestamp("2005-12-01")                                # trades at Dec 2005's end: invested from 2006
    print(f"  sleeves from: " + ", ".join(f"{k} {rets[k].first_valid_index():%b %Y}" for k in rets.columns))
    print("\nBENCHMARK")
    bench = show("bench", "Nifty 50 TRI held (NIFTYBEES, 0.10%)", simulate(rets, hold_one("N50"), start))

    print("\nMOMENTUM (Nifty200 Momentum 30 ETF)")
    m1 = show("momentum", "M1: hold the Momentum 30 ETF", simulate(rets, hold_one("M30"), start))
    m1_ok = verdict("M1 vs Nifty 50", all(m1[f"sh_{p}"] > bench[f"sh_{p}"] and m1[f"cagr_{p}"] > bench[f"cagr_{p}"]
                                          for p in "AB"), "Sharpe and CAGR above the benchmark in A and B")
    m2s = {n: show("momentum", f"M2: + {n}-session trend brake", simulate(rets, trend_brake(rets, "M30", n), start))
           for n in (200, 150, 250)}
    m2 = m2s[200]
    m2_ok = m1_ok and verdict("M2 replaces M1", all(
        m2[f"sh_{p}"] > m1[f"sh_{p}"] and m2[f"cagr_{p}"] >= m1[f"cagr_{p}"] - 1 for p in "AB")
        and all(m2s[n][f"sh_{p}"] > m1[f"sh_{p}"] for n in (150, 250) for p in "AB"),
        "Sharpe above M1 in A and B, CAGR within 1 point, 150/250 neighbours above M1 too")

    print("\nLONG-TERM CORE (80% equity; 5-point bands, and every year-end)")
    l1 = show("core", "L1: N50 45 / NN50 15 / MON100 20 / gold 10 / liquid 10", simulate(rets, fixed_mix(rets, L1), start))
    l1_ok = verdict("L1 vs Nifty 50", all(l1[f"sh_{p}"] > bench[f"sh_{p}"] and l1[f"dd_{p}"] > bench[f"dd_{p}"]
                                          for p in "AB"), "Sharpe above, worst fall shallower, in A and B")
    if m2_ok:
        print("  (L2's momentum sleeve uses M2: the trend brake parks it in the liquid ETF)")
    l2 = show("core", "L2: N50 30 / NN50 15 / M30 15 / MON100 20 / gold 10 / liquid 10",
              simulate(rets, l2_fn(rets, m2_ok), start))
    l2_ok = l1_ok and verdict("L2 replaces L1", all(l2[f"sh_{p}"] > l1[f"sh_{p}"] and l2[f"dd_{p}"] >= l1[f"dd_{p}"] - 2
                                                    for p in "AB"), "Sharpe above L1 in A and B, worst fall within 2 points")
    print("\nReference (not a candidate): the same mixes never rebalanced; 5-point bands without the year-end rebalance")
    show("ref", "L1 never rebalanced", simulate(rets, lambda d, w: L1norm(rets, d) if not w else None, start))
    show("ref", "L1, bands only", simulate(rets, fixed_mix_bands_only(rets, L1), start))

    live = "L2" if l2_ok else "L1" if l1_ok else "none (both live strategies to paper, cash parked)"
    print(f"\nAddendum 13 outcome: live core = {live}; momentum sleeve = "
          f"{'M2' if m2_ok else 'M1' if m1_ok else 'none'}; intraday = no real money")
    pd.DataFrame(ROWS).to_csv(ROOT / "research" / "allocation20_results.csv", index=False)
    print("Written research/allocation20_results.csv")
    return 0


def L1norm(rets, d):
    live = {k: v for k, v in L1.items() if available(d, rets, k)}
    tot = sum(live.values())
    return {k: v / tot for k, v in live.items()}


def fixed_mix_bands_only(rets, mix):
    inner = fixed_mix(rets, mix)

    def fn(d, w):
        if d.month == 12 and w:
            live = {k: v for k, v in mix.items() if available(d, rets, k)}
            tot = sum(live.values())
            tgt = {k: v / tot for k, v in live.items()}
            off = max(abs(w.get(k, 0.0) - tgt.get(k, 0.0)) for k in set(tgt) | set(w))
            return tgt if off > BAND else None
        return inner(d, w)
    return fn


def l2_fn(rets: pd.DataFrame, brake: bool):
    """L2; with brake (M2 passed) the momentum sleeve's weight goes to the liquid ETF while M30 is below its
    200-session average."""
    base = fixed_mix(rets, L2)
    if not brake:
        return base
    level = (1 + rets["M30"].fillna(0)).cumprod()

    def fn(d, w):
        c = level.loc[:d]
        on = len(c) < 200 or c.iloc[-1] > c.iloc[-200:].mean()
        mix = dict(L2) if on else {**{k: v for k, v in L2.items() if k != "M30"}, "LIQ": L2["LIQ"] + L2["M30"]}
        return fixed_mix(rets, mix)(d, {k: v for k, v in w.items()})
    return fn


if __name__ == "__main__":
    sys.exit(main())
