"""Addendum 17 (research/PREREGISTRATION.md): NSE quality / value / low-volatility indices after their launch.

  python research/factor_indices17.py [--hist research/data/hist]
Writes research/factor_indices17_results.csv.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import allocation20 as AL                                            # noqa: E402
from backtest.metrics import cagr, max_drawdown                       # noqa: E402

COST = 0.30                                                           # points a year: factor ETF vs broad ETF
INDICES = [  # (index, launch date, benchmark)
    ("NIFTY100 QUALITY 30", "2015-03-19", "NIFTY 100"),
    ("NIFTY50 VALUE 20", "2014-03-28", "NIFTY 50"),
    ("NIFTY100 LOW VOLATILITY 30", "2016-07-08", "NIFTY 100"),
    ("NIFTY ALPHA LOW-VOLATILITY 30", "2017-07-10", "NIFTY 200"),
    ("NIFTY QUALITY LOW-VOLATILITY 30", "2017-07-10", "NIFTY 200"),
    ("NIFTY200 QUALITY 30", "2018-04-17", "NIFTY 200"),
    ("NIFTY MIDCAP150 QUALITY 50", "2019-10-24", "NIFTY MIDCAP 150"),
    ("NIFTY DIVIDEND OPPORTUNITIES 50", "2011-03-22", "NIFTY 500"),
]
REPORT_ONLY = [("NIFTY200 VALUE 30", "2024-06-12", "NIFTY 200"), ("NIFTY500 QUALITY 50", "2024-12-20", "NIFTY 500"),
               ("NIFTY500 VALUE 50", None, "NIFTY 500")]


def levels(hist: Path) -> pd.DataFrame:
    t = pd.read_csv(hist / "tri.csv")
    t["index"] = t["index"].str.upper().str.strip()
    t["date"] = pd.to_datetime(t["date"])
    t = t.drop_duplicates(["index", "date"], keep="last")
    return t.pivot(index="date", columns="index", values="tri").sort_index()


def stats(x: pd.Series, liq: pd.Series) -> dict:
    x = x.dropna()
    r = x.pct_change().dropna()
    rf = liq.reindex(r.index).fillna(0)
    return {"cagr": cagr(x), "dd": max_drawdown(x), "sh": float((r - rf).mean() / r.std() * math.sqrt(252))}


def compare(lv: pd.DataFrame, liq: pd.Series, name: str, bench: str, start, end=None) -> dict:
    both = lv[[name, bench]].dropna()
    both = both.loc[pd.Timestamp(start):] if start is not None else both
    both = both.loc[:pd.Timestamp(end)] if end is not None else both
    m = both.resample("ME").last()
    if len(m) < 13:
        return {}
    first = m.index[0]                                                # from the first month-end in the window
    both = both.loc[first:]
    mr = m.pct_change().dropna()
    ex = mr[name] - COST / 100 / 12 - mr[bench]
    a, b = stats(both[name] * np.exp(-COST / 100 * np.arange(len(both)) / 252), liq), stats(both[bench], liq)
    return {"index": name, "benchmark": bench, "from": f"{first:%Y-%m}", "to": f"{m.index[-1]:%Y-%m}",
            "months": len(ex), "cagr": a["cagr"], "bench_cagr": b["cagr"], "dd": a["dd"], "bench_dd": b["dd"],
            "sharpe": a["sh"], "bench_sharpe": b["sh"], "excess_pct_yr": ex.mean() * 12 * 100,
            "t": float(ex.mean() / ex.std() * math.sqrt(len(ex))), "beat_months_pct": float((ex > 0).mean() * 100)}


def verdict(r: dict) -> str:
    if not r:
        return "too short"
    if r["excess_pct_yr"] <= 0:
        return "NO EDGE"
    if r["t"] >= 2 and r["sharpe"] > r["bench_sharpe"]:
        return "EDGE SHOWN"
    return "CONSISTENT, NOT PROVEN"


def line(tag: str, r: dict, v: str = "") -> str:
    if not r:
        return f"  {tag}: too short"
    return (f"  {tag} {r['from']}..{r['to']} ({r['months']} m): {r['cagr']:5.1f}% vs {r['bench_cagr']:5.1f}%, "
            f"fall {r['dd']:5.1f}% vs {r['bench_dd']:5.1f}%, Sharpe {r['sharpe']:4.2f} vs {r['bench_sharpe']:4.2f}, "
            f"excess {r['excess_pct_yr']:+5.2f}%/yr t {r['t']:+4.2f}, beat {r['beat_months_pct']:.0f}% of months"
            + (f"  -> {v}" if v else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    hist = Path(ap.parse_args(argv).hist)
    lv = levels(hist)
    liq = AL.load_sleeves(hist)["LIQ"]
    rows = []
    print(f"Factor indices vs their parent index, total return; factor index charged {COST} point a year more")
    for name, launch, bench in INDICES:
        live = compare(lv, liq, name, bench, pd.Timestamp(launch) + pd.offsets.MonthEnd(0))
        back = compare(lv, liq, name, bench, None, launch)
        v = verdict(live)
        print(f"\n{name} (launched {launch}; vs {bench})")
        print(line("LIVE", live, v))
        print(line("back-calculated", back))
        rows += [{**live, "period": "live", "verdict": v}, {**back, "period": "back-calculated"}]
    print("\nReported only (too little live history):")
    for name, launch, bench in REPORT_ONLY:
        if launch:
            live = compare(lv, liq, name, bench, pd.Timestamp(launch) + pd.offsets.MonthEnd(0))
            print(f"{name} (launched {launch}):" + line(" LIVE", live))
            rows.append({**live, "period": "live (report only)"})
        back = compare(lv, liq, name, bench, None, launch)
        print(f"{name}:" + line(" back-calculated", back))
        rows.append({**back, "period": "back-calculated (report only)"})
    pd.DataFrame(rows).to_csv(ROOT / "research" / "factor_indices17_results.csv", index=False)
    print("\nWritten research/factor_indices17_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
