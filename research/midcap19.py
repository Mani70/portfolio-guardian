"""Addendum 19 (research/PREREGISTRATION.md): the live mix (V1) with a Midcap 150 sleeve.

  python research/midcap19.py [--hist research/data/hist]
Writes research/midcap19_results.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import allocation20 as AL                                            # noqa: E402
import allweather16 as W                                             # noqa: E402
import history20 as H                                                # noqa: E402

MID_EXPENSE = 0.25
M10 = {"N50": 0.35, "NN50": 0.15, "MID": 0.10, "MON100": 0.20, "GOLD": 0.10, "LIQ": 0.10}
M15 = {"N50": 0.30, "NN50": 0.15, "MID": 0.15, "MON100": 0.20, "GOLD": 0.10, "LIQ": 0.10}
PERIODS = W.PERIODS
ROWS = []


def add_mid(rets: pd.DataFrame, hist: Path) -> pd.DataFrame:
    t = pd.read_csv(hist / "tri.csv")
    t["index"] = t["index"].str.upper()
    s = t[t["index"] == "NIFTY MIDCAP 150"].assign(date=lambda x: pd.to_datetime(x["date"])).drop_duplicates(
        "date").set_index("date")["tri"].sort_index().astype(float)
    days = rets.index.to_series().diff().dt.days.fillna(1).clip(lower=1)
    r = s.reindex(rets.index).ffill().pct_change(fill_method=None)
    r.loc[r.index <= s.index[0]] = np.nan
    rets["MID"] = r - MID_EXPENSE / 100 * days / 365
    return rets


def tilted(rets: pd.DataFrame, val: pd.DataFrame, base: Dict[str, float]):
    """The Addendum 16 tilt on any base mix: expensive = Nifty 50 -15 points to liquid; cheap = Nifty 50 +10 points,
    liquid 0 (for L1 this is exactly V1)."""
    pct = val["div_yield_pct"]
    rich = {**base, "N50": base["N50"] - 0.15, "LIQ": base["LIQ"] + 0.15}
    cheap = {**base, "N50": base["N50"] + 0.10, "LIQ": 0.0}

    def fn(d, w):
        p = pct.loc[:d]
        q = p.iloc[-1] if len(p) else 50
        mix = rich if q <= 20 else cheap if q >= 80 else base
        return AL.fixed_mix(rets, mix)(d, w)
    return fn


def tracking(hist: Path, rets: pd.DataFrame) -> None:
    panel = H.load_panel(hist, fields=("open", "high", "low", "close", "prevclose", "value"), symbols=["MID150BEES"])
    H.adjust_prices(panel, H.official_events(hist, H.symbol_changes(hist)), use_detection=False)
    etf = panel["close"]["MID150BEES"].astype(float).dropna() if "MID150BEES" in panel["close"] else pd.Series(dtype=float)
    if etf.empty:
        print("  MID150BEES prices not found")
        return
    sleeve = (1 + rets["MID"].fillna(0)).cumprod().reindex(etf.index)
    for y in sorted(set(etf.index.year)):
        e, s = etf[etf.index.year == y], sleeve[sleeve.index.year == y]
        if len(e) > 100:
            print(f"  {y}: ETF {(e.iloc[-1] / e.iloc[0] - 1) * 100:+6.1f}%   sleeve {(s.iloc[-1] / s.iloc[0] - 1) * 100:+6.1f}%")
    yrs = (etf.index[-1] - etf.index[0]).days / 365.25
    ce = (etf.iloc[-1] / etf.iloc[0]) ** (1 / yrs) - 1
    cs = (sleeve.iloc[-1] / sleeve.iloc[0]) ** (1 / yrs) - 1
    print(f"  {etf.index[0]:%b %Y} - {etf.index[-1]:%b %Y}: ETF {ce * 100:.2f}%/yr, sleeve {cs * 100:.2f}%/yr "
          f"(difference {(ce - cs) * 100:+.2f} points a year)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    hist = Path(ap.parse_args(argv).hist)
    rets = add_mid(AL.load_sleeves(hist), hist)
    liq = rets["LIQ"]
    val = W.valuation(hist)
    start = pd.Timestamp("2005-12-01")
    print(f"  Midcap 150 sleeve from {rets['MID'].first_valid_index():%b %Y}")
    print("\nCAGR / worst fall / Sharpe over cash")
    rows = {}
    for name, fn in (("Nifty 50 TRI held", AL.hold_one("N50")),
                     ("Midcap 150 sleeve held", AL.hold_one("MID")),
                     ("V1 (live: L1 + valuation tilt)", tilted(rets, val, AL.L1)),
                     ("M10: V1 with Midcap 150 10%", tilted(rets, val, M10)),
                     ("M15: V1 with Midcap 150 15%", tilted(rets, val, M15))):
        rows[name] = W.report("midcap", name, AL.simulate(rets, fn, start), liq, PERIODS)
    v1, m10, m15 = rows["V1 (live: L1 + valuation tilt)"], rows["M10: V1 with Midcap 150 10%"], rows["M15: V1 with Midcap 150 15%"]
    ok = all(m10[f"sh_{p}"] > v1[f"sh_{p}"] and m10[f"dd_{p}"] >= v1[f"dd_{p}"] - 2 and m15[f"sh_{p}"] > v1[f"sh_{p}"]
             for p in PERIODS)
    print(f"  -> M10 replaces V1: {'PASSES' if ok else 'FAILS'} (Sharpe over cash above V1 in A and B, worst fall "
          f"within 2 points, M15 also above V1)")
    W.ROWS.append({"section": "verdict", "rule": "M10 replaces V1", "passes": ok})
    print("\nTracking: MID150BEES (NETFMID150 before May 2022) against the sleeve")
    tracking(hist, rets)
    pd.DataFrame(W.ROWS).to_csv(ROOT / "research" / "midcap19_results.csv", index=False)
    print("\nWritten research/midcap19_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
