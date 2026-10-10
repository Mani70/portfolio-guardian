"""Addendum 23 (research/PREREGISTRATION.md): what NSE indices did after a day like today, 2012-2026.

  python research/market23.py        # writes research/market23_results.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))

from insights20 import nw_t                                               # noqa: E402

HIST = ROOT / "research" / "data" / "hist"
START, END, SPLIT = pd.Timestamp("2012-01-01"), pd.Timestamp("2026-09-30"), pd.Timestamp("2019-01-01")
# name in the Reel -> load_index keys (normalised NSE names; the first one with data wins day by day)
INDICES = {
    "Nifty 50": [["nifty50"]],
    "Nifty Bank": [["niftybank", "bankex", "banknifty"]],
    "Nifty IT": [["niftyit", "it"]],
    "Nifty Pharma": [["niftypharma", "pharma"]],
    "Nifty Auto": [["niftyauto", "auto"]],
    "Nifty FMCG": [["niftyfmcg", "fmcg"]],
    "Nifty Metal": [["niftymetal", "metal"]],
    "Nifty Realty": [["niftyrealty", "realty"]],
    "Nifty PSU Bank": [["niftypsubank", "psubank"]],
    "Nifty Energy": [["niftyenergy", "energy"]],
    "Nifty Midcap 150": [["niftymidcap150"], ["niftymidcap100", "niftyfreefloatmidcap100", "midcap"]],
    "Nifty Smallcap 250": [["niftysmallcap250"], ["niftysmallcap100", "niftyfreefloatsmallcap100", "smallcap"]],
}
BUCKETS = [(-np.inf, -3.0, "<= -3%"), (-3.0, -1.5, "-3% to -1.5%"), (-1.5, -0.5, "-1.5% to -0.5%"),
           (-0.5, 0.5, "-0.5% to +0.5%"), (0.5, 1.5, "+0.5% to +1.5%"), (1.5, 3.0, "+1.5% to +3%"),
           (3.0, np.inf, ">= +3%")]
HORIZONS = (1, 5)


def bucket(move_pct: float) -> str:
    for lo, hi, name in BUCKETS:
        if lo < move_pct <= hi or (lo == -np.inf and move_pct <= hi):
            return name
    return BUCKETS[-1][2]


def closes(name: str) -> pd.Series:
    import history20 as H
    out = None
    for keys in INDICES[name]:                                            # newer index first, older one fills the gap
        s = H.load_index(HIST, keys)["close"].astype(float)
        out = s if out is None else out.combine_first(s)
    s = out.loc[START:END].dropna()
    return s[~s.index.duplicated()]


def study() -> pd.DataFrame:
    rows = []
    for name in INDICES:
        c = closes(name)
        if len(c) < 500:
            print(f"  {name}: only {len(c)} days, skipped", flush=True)
            continue
        day = c.pct_change() * 100
        pos = pd.Series(np.arange(len(c)), index=c.index)
        for h in HORIZONS:
            fwd = (c.shift(-h) / c - 1) * 100
            base = fwd.mean()
            df = pd.DataFrame({"day": day, "x": fwd - base, "up": fwd > 0, "pos": pos}).dropna()
            df["bucket"] = df["day"].map(bucket)
            for _, _, b in BUCKETS:
                g = df[df["bucket"] == b]
                a, z = g[g.index < SPLIT]["x"], g[g.index >= SPLIT]["x"]
                t = nw_t(g["x"], g["pos"], 4) if len(g) >= 10 else np.nan
                rows.append({"index": name, "bucket": b, "horizon": h, "days": len(g),
                             "excess": g["x"].mean(), "rose_pct": g["up"].mean() * 100 if len(g) else np.nan,
                             "rose_all_pct": (fwd.dropna() > 0).mean() * 100, "t": t,
                             "excess_A": a.mean(), "excess_B": z.mean(), "days_A": len(a), "days_B": len(z),
                             "consistent": bool(len(a) >= 20 and len(z) >= 20 and np.sign(a.mean()) == np.sign(z.mean())
                                                and abs(t) >= 3.5)})
        print(f"  {name}: {c.index[0]:%Y-%m-%d} to {c.index[-1]:%Y-%m-%d}, {len(c)} days", flush=True)
    return pd.DataFrame(rows)


def main() -> int:
    r = study()
    r.to_csv(ROOT / "research" / "market23_results.csv", index=False)
    pd.set_option("display.width", 220)
    print(r.round(2).to_string(index=False))
    print(f"\n{int(r['consistent'].sum())} of {len(r)} cells show a consistent pattern")
    print(r[r["consistent"]].round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
