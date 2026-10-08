"""Addendum 16 (research/PREREGISTRATION.md): Dalio's All Weather and risk parity with Indian G-Secs, and a
valuation tilt on L1 (the investment committee's Graham / Marks / Templeton lens).

  python research/allweather16.py [--hist research/data/hist]
Writes research/allweather16_results.csv.

Same sleeves, costs, bands and simulation as research/allocation20.py (Addendum 13). Added: NSE's G-Sec total-return
indices (Oct 2015 on, NSE's daily index files) and the Nifty 50's daily P/E, P/B and dividend yield since 1999
(niftyindices.com, pepb.csv). Sharpe here is over the liquid sleeve (what idle cash earns), not over zero.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import allocation20 as AL                                            # noqa: E402
from backtest.metrics import cagr, max_drawdown                       # noqa: E402

GSEC = {"GS48": "Nifty 4-8 yr G-Sec Index", "GS813": "Nifty 8-13 yr G-Sec", "GS15": "Nifty 15 yr and above G-Sec Index"}
GSEC_EXPENSE = 0.20                                                   # % a year (Addendum 13)
AW = {"N50": 0.30, "GS15": 0.40, "GS48": 0.15, "GOLD": 0.15}
L1G = {**{k: v for k, v in AL.L1.items() if k != "LIQ"}, "GS48": 0.10}
RP_SLEEVES = ["N50", "MON100", "GOLD", "GS813"]
RP_DAYS = 756                                                         # 3 years of sessions
PERIODS = {"A": ("2006-01-01", "2015-12-31"), "B": ("2016-01-01", "2099-12-31")}
ROWS: List[dict] = []


def add_gsec(rets: pd.DataFrame, hist: Path) -> pd.DataFrame:
    ix = pd.read_csv(hist / "indices.csv", usecols=["date", "index", "close"])
    ix["date"] = pd.to_datetime(ix["date"])
    days = rets.index.to_series().diff().dt.days.fillna(1).clip(lower=1)
    for k, name in GSEC.items():
        s = ix[ix["index"] == name].drop_duplicates("date").set_index("date")["close"].sort_index()
        lv = s.reindex(rets.index).ffill()
        r = lv.pct_change(fill_method=None)
        r.loc[r.index <= s.index[0]] = np.nan
        rets[k] = r - GSEC_EXPENSE / 100 * days / 365
    return rets


def valuation(hist: Path) -> pd.DataFrame:
    v = pd.read_csv(hist / "pepb.csv")
    v["date"] = pd.to_datetime(v["date"])
    v = v.drop_duplicates("date").set_index("date").sort_index()
    for col in ("div_yield", "pe"):                                   # percentile among all values up to that day
        x = v[col].astype(float).to_numpy()
        v[f"{col}_pct"] = [(x[:i + 1] <= x[i]).mean() * 100 for i in range(len(x))]
    return v


def stats(eq: pd.Series, liq: pd.Series, a: str, b: str) -> dict:
    x = eq.loc[a:b].dropna()
    r = x.pct_change().dropna()
    rf = liq.reindex(r.index).fillna(0)
    sh = float((r - rf).mean() / r.std() * math.sqrt(252)) if r.std() > 0 else float("nan")
    return {"cagr": cagr(x), "dd": max_drawdown(x), "sh": sh, "sh_raw": float(r.mean() / r.std() * math.sqrt(252))}


def report(section: str, rule: str, res: dict, liq: pd.Series, windows: Dict[str, tuple]) -> dict:
    row = {"section": section, "rule": rule, "rebalances": res["rebalances"], "turnover_pct_yr": round(res["turnover"], 1)}
    parts = []
    for tag, (a, b) in windows.items():
        s = stats(res["equity"], liq, a, b)
        row.update({f"{k}_{tag}": v for k, v in s.items()})
        parts.append(f"{tag}: {s['cagr']:6.2f}% / {s['dd']:6.1f}% / {s['sh']:5.2f} (raw {s['sh_raw']:4.2f})")
    ROWS.append(row)
    print(f"  {rule:<60} " + "   ".join(parts), flush=True)
    return row


def tilt(rets: pd.DataFrame, val: pd.DataFrame, col: str, cut: float, expensive_high: bool):
    """L1, with the Nifty 50 sleeve cut to 30 (liquid 25) when the market is expensive and raised to 55 (liquid 0)
    when it is cheap; `col`'s percentile decides. expensive_high: a high value means expensive (P/E)."""
    pct = val[f"{col}_pct"]
    rich = {**AL.L1, "N50": 0.30, "LIQ": 0.25}
    cheap = {**AL.L1, "N50": 0.55, "LIQ": 0.0}

    def fn(d, w):
        p = pct.loc[:d]
        if p.empty:
            mix = AL.L1
        else:
            q = p.iloc[-1]
            hi, lo = q >= 100 - cut, q <= cut
            expensive, cheap_now = (hi, lo) if expensive_high else (lo, hi)
            mix = rich if expensive else cheap if cheap_now else AL.L1
        return AL.fixed_mix(rets, mix)(d, w)
    return fn


def risk_parity(rets: pd.DataFrame):
    def fn(d, w):
        hist = rets[RP_SLEEVES].loc[:d].iloc[-RP_DAYS:]
        if len(hist) < RP_DAYS or hist.notna().sum().min() < RP_DAYS - 5:
            return None if w else {"LIQ": 1.0}                         # 3 years of every sleeve needed
        inv = 1 / hist.std()
        return (inv / inv.sum()).to_dict()
    return fn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    hist = Path(ap.parse_args(argv).hist)
    rets = add_gsec(AL.load_sleeves(hist), hist)
    liq = rets["LIQ"]
    val = valuation(hist)
    start = pd.Timestamp("2005-12-01")
    print(f"  sleeves from: " + ", ".join(f"{k} {rets[k].first_valid_index():%b %Y}" for k in rets.columns))
    last = val.iloc[-1]
    print(f"  Nifty 50 on {val.index[-1]:%d %b %Y}: P/E {last.pe:.2f} ({last.pe_pct:.0f}th percentile since 1999), "
          f"P/B {last.pb:.2f}, dividend yield {last.div_yield:.2f}% ({last.div_yield_pct:.0f}th percentile)")

    print("\nMEASURING STICK: Addendum 13 on cash-adjusted Sharpe (CAGR / worst fall / Sharpe over cash)")
    bench = report("stick", "Nifty 50 TRI held", AL.simulate(rets, AL.hold_one("N50"), start), liq, PERIODS)
    l1 = report("stick", "L1", AL.simulate(rets, AL.fixed_mix(rets, AL.L1), start), liq, PERIODS)
    ok = all(l1[f"sh_{p}"] > bench[f"sh_{p}"] and l1[f"dd_{p}"] > bench[f"dd_{p}"] for p in PERIODS)
    print(f"  -> L1 vs Nifty 50 on the corrected Sharpe: {'still PASSES' if ok else 'now FAILS'}")
    ROWS.append({"section": "verdict", "rule": "L1 vs Nifty 50, cash-adjusted Sharpe", "passes": ok})

    print("\nPART 1 - DALIO (bonds exist from Oct 2015: invested Nov 2015; RP needs 3 years of them: Nov 2018)")
    s15 = pd.Timestamp("2015-10-30")
    w15 = {"N": ("2015-11-02", "2099-12-31")}
    w18 = {"N": ("2018-11-01", "2099-12-31")}
    report("dalio", "L1 (same span)", AL.simulate(rets, AL.fixed_mix(rets, AL.L1), s15), liq, w15)
    report("dalio", "Nifty 50 TRI held (same span)", AL.simulate(rets, AL.hold_one("N50"), s15), liq, w15)
    report("dalio", "AW: N50 30 / G-Sec 15y+ 40 / G-Sec 4-8y 15 / gold 15", AL.simulate(rets, AL.fixed_mix(rets, AW), s15),
           liq, w15)
    report("dalio", "L1g: L1 with G-Sec 4-8y instead of liquid", AL.simulate(rets, AL.fixed_mix(rets, L1G), s15), liq, w15)
    half = {k: AL.L1.get(k, 0) / 2 + AW.get(k, 0) / 2 for k in set(AL.L1) | set(AW)}
    report("dalio", "half L1 / half AW", AL.simulate(rets, AL.fixed_mix(rets, half), s15), liq, w15)
    report("dalio", "L1 (from Nov 2018)", AL.simulate(rets, AL.fixed_mix(rets, AL.L1), s15), liq, w18)
    report("dalio", "RP: inverse 3-yr vol N50/MON100/gold/G-Sec 8-13y (from Nov 2018)",
           AL.simulate(rets, risk_parity(rets), s15), liq, w18)

    print("\nPART 2 - VALUATION TILT ON L1 (2006 - 2026)")
    v1 = report("valuation", "V1: dividend yield, 20% cut-offs", AL.simulate(rets, tilt(rets, val, "div_yield", 20, False), start),
                liq, PERIODS)
    nb = [report("valuation", f"  neighbour: dividend yield, {c}% cut-offs",
                 AL.simulate(rets, tilt(rets, val, "div_yield", c, False), start), liq, PERIODS) for c in (10, 30)]
    report("valuation", "  reference: P/E, 20% cut-offs (2021 method break)",
           AL.simulate(rets, tilt(rets, val, "pe", 20, True), start), liq, PERIODS)
    passed = all(v1[f"sh_{p}"] > l1[f"sh_{p}"] and v1[f"cagr_{p}"] >= l1[f"cagr_{p}"] - 0.5 for p in PERIODS) and \
        all(n[f"sh_{p}"] > l1[f"sh_{p}"] for n in nb for p in PERIODS)
    print(f"  -> V1 replaces L1: {'PASSES' if passed else 'FAILS'} (Sharpe over cash above L1 in A and B, CAGR within "
          f"0.5 point, both neighbours above L1 too)")
    ROWS.append({"section": "verdict", "rule": "V1 replaces L1", "passes": passed})
    regime = pd.Series({d: val["div_yield_pct"].loc[:d].iloc[-1] for d in sorted(AL.month_ends(rets.index[rets.index >= start]))})
    print(f"  month-ends expensive (DY bottom 20%): {(regime <= 20).mean() * 100:.0f}%, cheap (top 20%): "
          f"{(regime >= 80).mean() * 100:.0f}%")
    pd.DataFrame(ROWS).to_csv(ROOT / "research" / "allweather16_results.csv", index=False)
    print("\nWritten research/allweather16_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
