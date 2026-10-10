"""Addendum 18 / 18a (research/PREREGISTRATION.md): the owner's 7-pillar framework, scored blind from NSE filings.

  python research/framework18.py            # needs research/data/fund/results.csv (fundamentals18.py download)
Writes research/framework18_scores.csv (every company, every decision date), research/framework18_results.csv.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import allocation20 as AL                                            # noqa: E402
import history20 as H                                                # noqa: E402
from backtest.engine import Costs                                    # noqa: E402
from backtest.metrics import cagr, max_drawdown                       # noqa: E402

FUND = ROOT / "research" / "data" / "fund"
WEIGHTS = {"economics": 20, "growth": 15, "strength": 15, "governance": 15, "valuation": 20, "inflection": 10,
           "resilience": 5}
MEASURES = {  # pillar: [(measure, higher is better)]
    "economics": [("roe3", True), ("roe_stab", True), ("margin3", True)],
    "growth": [("rev_cagr", True), ("pat_cagr", True)],
    "strength": [("icov", True)],
    "governance": [("dilution", False), ("delay", False), ("dividend", True)],
    "valuation": [("ey", True), ("btp", True)],
    "inflection": [("accel", True), ("pat_g1", True)],
    "resilience": [("loss_years", False), ("vol", False)],
}
MONEY = ["revenue", "total_income", "other_income", "interest", "pbt", "pat", "equity_capital", "reserves"]
COHORT = ["TITAN", "ASIANPAINT", "EICHERMOT", "INFY", "HDFCBANK", "BAJFINANCE", "SATYAMCOMP", "YESBANK", "DHFL", "KFA"]
TOP_N, IDEA_VALUE = 20, 100_000
COSTS = Costs(dp_per_sell=21.83)
SLIP = COSTS.slippage_pct / 100
FIRST_KNOWN = pd.Timestamp("2007-07-01")                            # FY2005-06 results: no filing date on NSE


# ---------------------------------------------------------------- data
def load_results(changes) -> pd.DataFrame:
    r = pd.read_csv(FUND / "results.csv")
    r = r[r["source"].isin(["html", "xbrl"])].copy()
    r["symbol"] = r["symbol"].map(lambda s: H.final_symbol(s, changes))
    r["year_end"] = pd.to_datetime(r["year_end"])
    r["filed"] = pd.to_datetime(r["filed"], errors="coerce")
    r["known"] = r["filed"].fillna(FIRST_KNOWN)
    for c in MONEY + ["face_value"]:
        r[c] = pd.to_numeric(r[c], errors="coerce")
    r.loc[r["reserves"] == 0, "reserves"] = np.nan                    # 18a: 0 = not given
    r.loc[(r["pbt"] == 0) & r["pat"].fillna(0).ne(0), "pbt"] = np.nan  # 18a: misread bank form
    for c in MONEY:                                                   # 18a: unit slips (x50 off the company's median)
        med = r.groupby("symbol")[c].transform(lambda s: s.abs().median())
        bad = (r[c].abs() > 0) & ((r[c].abs() > 50 * med) | (r[c].abs() < med / 50))
        r.loc[bad, c] = np.nan
    r["fin"] = r["bank"].astype(str).eq("True") | (r["interest"] >= 0.35 * r["revenue"])
    r["shares"] = r["equity_capital"] * 1e7 / r["face_value"]
    r["book"] = r["equity_capital"] + r["reserves"]
    r["roe"] = np.where(r["book"] > 0, r["pat"] / r["book"], np.nan)
    r["margin"] = np.where(r["fin"], r["pat"] / r["revenue"], (r["pbt"] + r["interest"].fillna(0)) / r["revenue"])
    r.loc[~(r["revenue"] > 0), "margin"] = np.nan
    return r.sort_values(["symbol", "year_end"]).drop_duplicates(["symbol", "year_end"], keep="first")


def dividends(hist: Path, changes) -> Dict[str, List[pd.Timestamp]]:
    c = pd.read_csv(hist / "corp_actions.csv", dtype=str, usecols=["series", "symbol", "ex_date", "purpose"])
    c = c[(c["series"] == "EQ") & c["purpose"].str.contains(r"\bDIV", case=False, na=False)]
    c["symbol"] = c["symbol"].map(lambda s: H.final_symbol(s, changes))
    c["ex_date"] = pd.to_datetime(c["ex_date"], errors="coerce")
    return {s: sorted(g.dropna().tolist()) for s, g in c.groupby("symbol")["ex_date"]}


def cagr_between(new: float, old: float, years: float) -> float:
    if not (years >= 1.5) or not (new > 0 and old > 0):
        return -np.inf if years >= 1.5 else np.nan
    return (new / old) ** (1 / years) - 1


# ---------------------------------------------------------------- measures on one date
def measures(sym: str, past: pd.DataFrame, d: pd.Timestamp, px: dict, divs, cutoff_div) -> Optional[dict]:
    if past.empty:
        return None
    last = past.iloc[-1]
    if (d - last["year_end"]).days > 16 * 30.5:
        return None
    h3 = past[past["year_end"] >= last["year_end"] - pd.DateOffset(years=3, days=20)]
    h5 = past[past["year_end"] >= last["year_end"] - pd.DateOffset(years=5, days=20)]
    roe3 = h3["roe"].dropna().tail(3)
    old = h3.iloc[0]
    yrs = (last["year_end"] - old["year_end"]).days / 365.25
    prev = past.iloc[-2] if len(past) >= 2 else None
    rev_g1 = (last["revenue"] / prev["revenue"] - 1) if prev is not None and prev["revenue"] > 0 and last["revenue"] > 0 else np.nan
    m = {"symbol": sym, "fin": bool(last["fin"]), "year_end": last["year_end"], "filed": last["known"],
         "roe3": roe3.mean() if len(roe3) >= 2 else np.nan,
         "roe_stab": -h5["roe"].dropna().std() if h5["roe"].notna().sum() >= 2 else np.nan,
         "margin3": h3["margin"].dropna().tail(3).mean() if h3["margin"].notna().sum() >= 2 else np.nan,
         "rev_cagr": cagr_between(last["revenue"], old["revenue"], yrs),
         "pat_cagr": cagr_between(last["pat"], old["pat"], yrs),
         "pat_g1": ((last["pat"] / prev["pat"] - 1) if prev is not None and prev["pat"] > 0 and last["pat"] > 0
                    else -np.inf if prev is not None else np.nan),
         "loss_years": float((h5["pat"] < 0).sum()) if h5["pat"].notna().any() else np.nan,
         "pat": last["pat"], "book": last["book"]}
    if not m["fin"]:
        i, ebit = last["interest"], last["pbt"] + (0 if pd.isna(last["interest"]) else last["interest"])
        m["icov"] = 50.0 if (pd.isna(i) or i <= 0) and ebit > 0 else min(50.0, ebit / i) if i > 0 else np.nan
    m["accel"] = rev_g1 - m["rev_cagr"] if np.isfinite(m["rev_cagr"]) else np.nan
    if pd.notna(last["filed"]) and pd.notna(old["filed"]) and old["filed"] >= pd.Timestamp("2010-01-01") and yrs >= 1.5:
        f = px["factor"][sym].loc[old["filed"]:last["filed"]].iloc[1:].prod() if sym in px["factor"].columns else 1.0
        m["dilution"] = last["shares"] * f / old["shares"] if old["shares"] > 0 else np.nan
    m["delay"] = (last["filed"] - last["year_end"]).days if pd.notna(last["filed"]) else np.nan
    if d >= cutoff_div:
        ds = divs.get(sym, [])
        m["dividend"] = float(any(d - pd.Timedelta(days=365) < x <= d for x in ds))
    # valuation: market value at the filing, moved by the adjusted return to d (18a)
    c, raw = px["close"], px["raw"]
    if sym in c.columns:
        k = c.index.searchsorted(last["known"])
        if k < len(c.index) and c.index[k] < d:
            a0, r0, a1 = c[sym].iloc[k], raw[sym].iloc[k], c[sym].loc[:d].dropna()
            if pd.notna(a0) and pd.notna(r0) and len(a1) and last["shares"] > 0:
                mcap = last["shares"] * r0 * (a1.iloc[-1] / a0) / 1e7
                m["ey"], m["btp"] = last["pat"] / mcap, last["book"] / mcap
        lr = np.log(c[sym].loc[:d].dropna()).diff().iloc[-252:]
        m["vol"] = lr.std() * math.sqrt(252) if lr.notna().sum() > 150 else np.nan
    m["qualified"] = bool(last["pat"] > 0 and not (last["book"] <= 0)                # missing reserves: not "zero"
                          and (m["fin"] or not (m.get("icov", 99) < 1.5)))
    return m


def score(df: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    pill = {}
    for p, ms in MEASURES.items():
        cols = []
        for name, up in ms:
            if name not in df:
                continue
            x = df[name].replace([np.inf, -np.inf], [1e9, -1e9])
            r = x.rank(pct=True) * 100
            cols.append(r if up else 100 - r + 100 / max(1, x.notna().sum()))
        pill[p] = pd.concat(cols, axis=1).mean(axis=1) if cols else pd.Series(np.nan, index=df.index)
    P = pd.DataFrame(pill).fillna(50.0)
    w = pd.DataFrame({p: weights.get(p, 0) for p in WEIGHTS}, index=df.index).astype(float)
    w.loc[df["fin"], "strength"] = 0.0                                # financials: pillar 3 left out, others scaled
    return (P * w).sum(axis=1) / w.sum(axis=1)


# ---------------------------------------------------------------- portfolios
def hold_returns(close: pd.DataFrame, entry: pd.Timestamp, exit_: pd.Timestamp, syms) -> pd.DataFrame:
    """Daily values of 1 rupee in each symbol from entry close to exit close (a stock that stops: its last close)."""
    w = close.loc[entry:exit_, [s for s in syms if s in close.columns]].ffill()
    return w / w.iloc[0]


def run_portfolio(scores: pd.DataFrame, close: pd.DataFrame, entries: List[pd.Timestamp], end: pd.Timestamp,
                  pick, costs: bool = True) -> pd.Series:
    vals, level, held = [], 1.0, set()
    for i, e in enumerate(entries):
        x = entries[i + 1] if i + 1 < len(entries) else end
        syms = pick(scores[scores["entry"] == e])
        syms = [s for s in syms if s in close.columns and pd.notna(close.at[e, s])]
        if not syms:
            continue
        if costs:                                                     # costs only on what changes
            new, gone = set(syms) - held, held - set(syms)
            per = (COSTS.buy(IDEA_VALUE) + COSTS.sell(IDEA_VALUE)) / IDEA_VALUE / 2 + SLIP
            level *= 1 - per * (len(new) + len(gone)) / max(len(syms), 1)
        held = set(syms)
        v = hold_returns(close, e, x, syms).mean(axis=1) * level
        level = float(v.iloc[-1])
        vals.append(v.iloc[1:] if vals else v)
    return pd.concat(vals) if vals else pd.Series(dtype=float)


def stats(eq: pd.Series, liq: pd.Series) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    rf = liq.reindex(r.index).fillna(0)
    return {"cagr": cagr(eq), "dd": max_drawdown(eq), "sh": float((r - rf).mean() / r.std() * math.sqrt(252))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    hist = Path(ap.parse_args(argv).hist)
    changes = H.symbol_changes(hist)
    print("Loading prices ...", flush=True)
    panel = H.load_panel(hist, fields=("open", "high", "low", "close", "value"))
    raw = panel["close"].astype(float).copy()
    H.adjust_prices(panel, H.official_events(hist, changes), use_detection=False)
    px = {"close": panel["close"].astype(float), "raw": raw, "factor": panel["factor"].astype(float)}
    close = px["close"]
    res = load_results(changes)
    divs = dividends(hist, changes)
    uni = pd.read_csv(FUND / "universe.csv", parse_dates=["decision"])
    print(f"  {res['symbol'].nunique()} companies with results, {len(res)} company-years", flush=True)

    rows = []
    groups = {s: g for s, g in res.groupby("symbol")}
    for d, u in uni.groupby("decision"):
        k = close.index.searchsorted(d)
        entry = close.index[min(k + 1, len(close.index) - 1)]
        for sym in u["symbol"]:
            g = groups.get(sym)
            past = g[g["known"] < d] if g is not None else pd.DataFrame()
            m = measures(sym, past, d, px, divs, pd.Timestamp("2011-01-01")) if len(past) else None
            if m:
                rows.append({**m, "decision": d, "entry": entry})
    S = pd.DataFrame(rows)
    S["score"] = S.groupby("decision", group_keys=False).apply(lambda g: score(g, WEIGHTS))
    print(f"  scored: {S.groupby('decision').size().min()}-{S.groupby('decision').size().max()} companies a date "
          f"(of 500); qualified {S['qualified'].mean() * 100:.0f}%", flush=True)

    entries = sorted(S["entry"].unique())
    end = close.index[-1]
    # forward returns from each entry: 1 year (to the next entry) and 5 years (multibaggers)
    S["ret1"] = np.nan
    S["ret5"] = np.nan
    for i, e in enumerate(entries):
        x1 = entries[i + 1] if i + 1 < len(entries) else None
        x5 = close.index[min(close.index.searchsorted(e + pd.DateOffset(years=5)), len(close.index) - 1)]
        idx = S["entry"] == e
        syms = S.loc[idx, "symbol"]
        c0 = close.loc[e].reindex(syms).to_numpy()
        if x1 is not None:
            c1 = close.loc[:x1].ffill().iloc[-1].reindex(syms).to_numpy()
            S.loc[idx, "ret1"] = c1 / c0 - 1
        if e + pd.DateOffset(years=5) <= end:
            c5 = close.loc[:x5].ffill().iloc[-1].reindex(syms).to_numpy()
            S.loc[idx, "ret5"] = c5 / c0 - 1
    S.to_csv(ROOT / "research" / "framework18_scores.csv", index=False)

    liq = AL.load_sleeves(hist)["LIQ"]
    tri = pd.read_csv(hist / "tri.csv")
    tri["index"] = tri["index"].str.upper()
    n500 = tri[tri["index"] == "NIFTY 500"].assign(date=lambda x: pd.to_datetime(x["date"])).drop_duplicates(
        "date").set_index("date")["tri"].sort_index()

    def top(n, col="score"):
        return lambda g: list(g[g["qualified"]].sort_values(col, ascending=False)["symbol"].head(n))

    universe_all = lambda g: list(g["symbol"])                       # noqa: E731
    split = pd.Timestamp("2016-07-01")
    out = []

    def report(label, eq, bench=None):
        row = {"rule": label}
        for tag, (a, b) in {"A": (None, split), "B": (split, None), "all": (None, None)}.items():
            x = eq.loc[a:b] if a or b else eq
            row.update({f"{k}_{tag}": v for k, v in stats(x, liq).items()})
        out.append(row)
        print(f"  {label:<44} A: {row['cagr_A']:6.2f}% / {row['dd_A']:6.1f}% / {row['sh_A']:5.2f}   "
              f"B: {row['cagr_B']:6.2f}% / {row['dd_B']:6.1f}% / {row['sh_B']:5.2f}", flush=True)
        return row

    print("\nPORTFOLIOS (CAGR / worst fall / Sharpe over cash; A = Jul 2008 - Jul 2016, B = Jul 2016 on)")
    base = report("Universe: all 500, equal weights (no costs)", run_portfolio(S, close, entries, end, universe_all, False))
    port = report("FRAMEWORK: top 20 qualified, after costs", run_portfolio(S, close, entries, end, top(20)))
    n5 = n500.reindex(close.index).ffill().loc[entries[0]:]
    report("Nifty 500 TRI (with dividends)", n5 / n5.iloc[0])
    p1 = all(port[f"cagr_{p}"] > base[f"cagr_{p}"] and port[f"sh_{p}"] > base[f"sh_{p}"] for p in "AB")

    print("\nRANKING (rank correlation of the score with the next 12 months' return, every scored company)")
    ic = S.dropna(subset=["ret1"]).groupby("decision").apply(lambda g: g["score"].rank().corr(g["ret1"].rank()))
    t = ic.mean() / ic.std() * math.sqrt(len(ic))
    icA, icB = ic[ic.index < split].mean(), ic[ic.index >= split].mean()
    print("  " + "  ".join(f"{d:%Y} {v:+.2f}" for d, v in ic.items()))
    print(f"  mean {ic.mean():+.3f}, t {t:.2f}; A {icA:+.3f}, B {icB:+.3f}")
    p2 = t >= 2 and icA > 0 and icB > 0

    print("\nMULTIBAGGERS (3x or more within 5 years, prices only)")
    mb = S.dropna(subset=["ret5"]).copy()
    mb["top5th"] = mb.groupby("decision")["score"].transform(lambda s: s >= s.quantile(0.8))
    mb["x3"] = mb["ret5"] >= 2.0
    p3 = True
    for tag, sel in (("A", mb["decision"] < split), ("B", mb["decision"] >= split)):
        g = mb[sel]
        base_rate, top_rate = g["x3"].mean(), g.loc[g["top5th"], "x3"].mean()
        recall = g.loc[g["x3"], "top5th"].mean()
        print(f"  {tag}: universe {base_rate * 100:.1f}% went 3x; top fifth {top_rate * 100:.1f}% "
              f"(x{top_rate / base_rate:.2f}); {recall * 100:.0f}% of the 3x stocks were in the top fifth "
              f"({int(g['x3'].sum())} of {len(g)} company-dates)")
        p3 &= top_rate >= 1.5 * base_rate
        out.append({"rule": f"multibagger {tag}", "base_rate": base_rate, "top_rate": top_rate, "recall": recall})

    print("\nVERDICTS")
    for name, ok in (("Portfolio beats the universe (CAGR and Sharpe, A and B)", p1),
                     ("Score ranks next-year returns (t >= 2, positive in A and B)", p2),
                     ("Top fifth finds 3x stocks at >= 1.5x the base rate (A and B)", p3)):
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
        out.append({"rule": "verdict: " + name, "passes": bool(ok)})

    print("\nSENSITIVITY (not judged)")
    eq_w = {p: 1 for p in WEIGHTS}
    S["score_eq"] = S.groupby("decision", group_keys=False).apply(lambda g: score(g, eq_w))
    report("equal pillar weights, top 20", run_portfolio(S, close, entries, end, top(20, "score_eq")))
    for p in WEIGHTS:
        w = {**WEIGHTS, p: 0}
        S[f"score_no_{p}"] = S.groupby("decision", group_keys=False).apply(lambda g, w=w: score(g, w))
        report(f"without {p}, top 20", run_portfolio(S, close, entries, end, top(20, f"score_no_{p}")))
    for p in WEIGHTS:
        w = {q: (1 if q == p else 0) for q in WEIGHTS}
        S[f"score_only_{p}"] = S.groupby("decision", group_keys=False).apply(lambda g, w=w: score(g, w))
        report(f"only {p}, top 20", run_portfolio(S, close, entries, end, top(20, f"score_only_{p}")))
    for n in (10, 30, 50):
        report(f"top {n}", run_portfolio(S, close, entries, end, top(n)))
    two = entries[::2]
    report("top 20, two-year holds", run_portfolio(S, close, two, end, top(20)))

    print("\nNAMED COMPANIES (score, rank among scored, qualified; then the next 12 months and 5 years)")
    S["rank"] = S.groupby("decision")["score"].rank(ascending=False)
    S["n"] = S.groupby("decision")["score"].transform("size")
    for sym in COHORT:
        g = S[S["symbol"] == sym]
        if g.empty:
            print(f"  {sym}: never scored (no result on NSE in time, or never in the 500)")
            continue
        cells = [f"{r.decision:%Y} {r.score:4.1f} #{int(r['rank'])}/{int(r.n)}{'' if r.qualified else ' DQ'}"
                 f" ({'%+.0f%%' % (r.ret1 * 100) if pd.notna(r.ret1) else '-'}"
                 f"{', 5y %+.0f%%' % (r.ret5 * 100) if pd.notna(r.ret5) else ''})" for _, r in g.iterrows()]
        print(f"  {sym}: " + "; ".join(cells))

    picks = S[S["qualified"]].sort_values("score", ascending=False).groupby("decision").head(TOP_N)
    fp = picks[picks["ret1"] <= -0.5]
    print(f"\nFALSE POSITIVES: {len(fp)} of {len(picks.dropna(subset=['ret1']))} top-20 picks lost half or more in a year: "
          + ", ".join(f"{r.symbol} {r.decision:%Y} ({r.ret1 * 100:+.0f}%)" for _, r in fp.head(25).iterrows()))
    miss = S[(S["rank"] > S["n"] / 2) & (S["ret5"] >= 2.0)]
    print(f"MISSED WINNERS: {len(miss)} company-dates in the bottom half went 3x in 5 years, e.g. "
          + ", ".join(f"{r.symbol} {r.decision:%Y} ({r.ret5 * 100:+.0f}%)" for _, r in
                      miss.sort_values("ret5", ascending=False).head(15).iterrows()))
    pd.DataFrame(out).to_csv(ROOT / "research" / "framework18_results.csv", index=False)
    print("\nWritten research/framework18_scores.csv and research/framework18_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
