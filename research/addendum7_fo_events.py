"""Addendum 7 D1/D2 on a wider universe (exploratory robustness, after the main run): every F&O stock 2014-2026,
including the ones that later left the F&O list or the Nifty, priced by its near-month future (research/data/nse/
fo.csv; stock futures exist only for large, liquid companies). Abnormal = stock future minus Nifty future, both from
the same day. The 49 stocks of the main test are reported apart from the others (those are new evidence).

  python research/addendum7_fo_events.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
from addendum7 import NSE, insider_events                           # noqa: E402

fo = pd.read_csv(NSE / "fo.csv", parse_dates=["date"], usecols=["date", "symbol", "fut_close"])
px = fo.pivot_table(index="date", columns="symbol", values="fut_close", aggfunc="last").sort_index()
px = px[px.index >= "2016-01-01"]
nifty = px["NIFTY"]
idx = px.index
mid = idx[len(idx) // 2]
main49 = {p.stem.replace("M_M", "M&M") for p in (ROOT / "cache" / "history").glob("*.csv")}


def events(rows, label):
    out = []
    for sym, day in rows:
        if sym not in px.columns or sym in ("NIFTY", "BANKNIFTY"):
            continue
        i = idx.searchsorted(day, side="right")          # first session after the disclosure / results day
        if i < 2 or i + 60 >= len(idx):
            continue
        s = px[sym].values
        if np.isnan(s[i - 1]) or np.isnan(s[i + 59]) or np.isnan(s[i + 19]):
            continue
        n = nifty.values
        out.append(dict(symbol=sym, date=idx[i], main=sym in main49,
                        f20=s[i + 19] / s[i - 1] - 1 - (n[i + 19] / n[i - 1] - 1),
                        f60=s[i + 59] / s[i - 1] - 1 - (n[i + 59] / n[i - 1] - 1)))
    ev = pd.DataFrame(out).drop_duplicates(["symbol", "date"])
    for part, e in (("other F&O stocks", ev[~ev.main]), ("the 49 main stocks", ev[ev.main]), ("all", ev)):
        res = []
        for h in ("f20", "f60"):
            a, b = e[e.date <= mid][h], e[e.date > mid][h]
            m = e.groupby(e.date.dt.to_period("M"))[h].mean()
            t = m.mean() / (m.std(ddof=1) / math.sqrt(len(m))) if len(m) > 2 else float("nan")
            ok = len(e) >= 100 and min(abs(a.mean()), abs(b.mean())) > 0.003 and np.sign(a.mean()) == np.sign(b.mean()) \
                and abs(t) >= 2
            res.append(f"{h[1:]}d {e[h].mean() * 100:+.2f}% (halves {a.mean() * 100:+.2f}/{b.mean() * 100:+.2f}, t {t:.2f})"
                       + (" PASS" if ok else ""))
        print(f"  {label}, {part}: n={len(e)}; " + "; ".join(res))


ins = insider_events()
print("D2 promoter/director open-market buying (from the close before the first session after disclosure):")
events(zip(ins.buys.symbol, ins.buys.date), "D2")
bm = pd.DataFrame([json.loads(l) for l in open(NSE / "board_meetings.jsonl", encoding="utf-8")])
txt = (bm["bm_purpose"].fillna("") + " " + bm["bm_desc"].fillna("")).str.lower()
res = bm[txt.str.contains("financial result")].copy()
res["date"] = pd.to_datetime(res["bm_date"], format="%d-%b-%Y", errors="coerce")
res = res[["bm_symbol", "date"]].dropna().drop_duplicates()
print("D1 results-day reaction (results day and the next session), then 20/60 sessions:")
rows = []
for sym, day in zip(res.bm_symbol, res.date):
    if sym not in px.columns:
        continue
    i = idx.searchsorted(day)
    if i < 1 or i + 62 >= len(idx):
        continue
    s, n = px[sym].values, nifty.values
    if np.isnan(s[i - 1]) or np.isnan(s[i + 1]) or np.isnan(s[i + 61]):
        continue
    react = s[i + 1] / s[i - 1] - 1 - (n[i + 1] / n[i - 1] - 1)
    rows.append((sym, idx[i + 1], react))
r = pd.DataFrame(rows, columns=["symbol", "day", "react"])
for thr in (0.05, 0.04, 0.06):
    up = r[r.react > thr]
    events(zip(up.symbol, up.day - pd.Timedelta(days=0)), f"D1 +{thr:.0%}")
    dn = r[r.react < -thr]
    events(zip(dn.symbol, dn.day), f"D1 -{thr:.0%}")
