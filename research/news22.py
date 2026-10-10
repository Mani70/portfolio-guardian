"""Addendum 22 (research/PREREGISTRATION.md): how NSE shares reacted to each type of official news, 2012-2026.

  python research/news22.py download     # resumable; research/data/news/announcements.csv
  python research/news22.py study        # writes research/news22_results.csv
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

NEWS = ROOT / "research" / "data" / "news"
API = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={}&to_date={}"
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
                      "Safari/537.36", "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/"}
START, END = date(2012, 1, 1), date(2026, 9, 30)
WINDOW = 3

# event type: (category pattern, text pattern) - either matching counts (case-insensitive); fixed in Addendum 22
TYPES = {
    "buyback": (r"buy ?back", r"\bbuy ?back"),
    "bonus issue": (r"^bonus", r"\bbonus (issue|shares)"),
    "stock split": (r"sub-?division|split", r"sub-?division|stock split|split of (equity )?shares"),
    "rating upgrade": (None, r"(credit )?rating.*upgrad|upgrad.*(credit )?rating"),
    "rating downgrade": (None, r"(credit )?rating.*downgrad|downgrad.*(credit )?rating"),
    "auditor resigned": (None, r"resignation of (the )?(statutory )?auditor|statutory auditor.*resign"),
    "MD/CEO/CFO resigned": (None, r"resign.*(managing director|chief executive|\bceo\b|chief financial|\bcfo\b)|"
                                  r"(managing director|chief executive|\bceo\b|chief financial|\bcfo\b).*resign"),
    "order won": (r"bagging|receiving of orders|award of order", r"bagging|bagged|order (win|received|worth)|"
                                                                 r"receipt of (an? )?order|awarded (an? )?(order|contract)"),
    "acquisition": (r"^acquisition", r"\bacqui(re|sition)"),
    "fund raising": (r"qualified institution|preferential|rights issue|fund rais",
                     r"qualified institutions? placement|\bqip\b|preferential (issue|allotment)|rights issue"),
    "default / insolvency": (r"default|insolvency", r"\bdefault\b|insolvency|\bnclt\b.*admit"),
}


def download(workers: int = 4) -> None:
    """Resumable (done.txt lists finished windows); 4 connections, each pausing between its own requests."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    NEWS.mkdir(parents=True, exist_ok=True)
    out, done_p = NEWS / "announcements.csv", NEWS / "done.txt"
    done = set(done_p.read_text().split()) if done_p.exists() else set()
    windows, d = [], START
    while d <= END:
        e = min(d + timedelta(days=WINDOW - 1), END)
        if d.isoformat() not in done:
            windows.append((d, e))
        d = e + timedelta(days=1)
    print(f"{len(done)} windows done, {len(windows)} to fetch", flush=True)
    lock, local, n = threading.Lock(), threading.local(), [0]

    def job(w):
        d, e = w
        if not hasattr(local, "s"):
            local.s = requests.Session()
            local.s.headers.update(HEAD)
        rows = None
        for k in range(4):
            try:
                r = local.s.get(API.format(d.strftime("%d-%m-%Y"), e.strftime("%d-%m-%Y")), timeout=60)
                if r.status_code == 200:
                    rows = r.json()
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(3 * (k + 1))
        time.sleep(0.5)
        with lock:
            if rows is None:
                print(f"  {d}: failed, left for the next run", flush=True)
                return
            df = pd.DataFrame([{"time": x.get("an_dt"), "symbol": x.get("symbol"), "category": x.get("desc"),
                                "text": str(x.get("attchmntText") or "")[:400].replace("\n", " ")} for x in rows])
            if len(df):
                df.to_csv(out, mode="a", header=not out.exists(), index=False)
            with done_p.open("a") as fh:
                fh.write(d.isoformat() + "\n")
            n[0] += 1
            if n[0] % 100 == 0:
                print(f"  {n[0]}/{len(windows)} windows", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, windows))
    print("done", flush=True)


def classify(cat: str, text: str) -> List[str]:
    cat, text = str(cat or "").lower(), str(text or "").lower()
    out = []
    for t, (cp, tp) in TYPES.items():
        if (cp and re.search(cp, cat)) or (tp and re.search(tp, text)):
            out.append(t)
    if "rating upgrade" in out and "rating downgrade" in out:
        out = [x for x in out if x not in ("rating upgrade", "rating downgrade")]
    return out


HORIZONS = (1, 5, 20)
SPLIT = pd.Timestamp("2019-01-01")


def events(changes) -> pd.DataFrame:
    """Every classified announcement (and annual result) with its time, final symbol and type."""
    import history20 as H
    a = pd.read_csv(NEWS / "announcements.csv", dtype=str).drop_duplicates()
    a["time"] = pd.to_datetime(a["time"], format="%d-%b-%Y %H:%M:%S", errors="coerce")
    a = a.dropna(subset=["time", "symbol"])
    rows = []
    for r in a.itertuples():
        for kind in classify(r.category, r.text):
            rows.append({"symbol": H.final_symbol(r.symbol, changes), "time": r.time, "type": kind})
    ev = pd.DataFrame(rows)
    res_p = ROOT / "research" / "data" / "fund" / "results.csv"
    if res_p.exists():                                                    # Addendum 18's annual results, as filed
        f = pd.read_csv(res_p)
        f = f[f["source"].isin(["html", "xbrl"])].copy()
        f["filed"] = pd.to_datetime(f["filed"], errors="coerce")
        f = f.dropna(subset=["filed"]).sort_values(["symbol", "year_end"])
        f["prev"] = f.groupby("symbol")["pat"].shift(1)
        for r in f.itertuples():
            if pd.isna(r.pat):
                continue
            kind = ("annual loss" if r.pat < 0 else
                    "annual profit up 20%+" if r.prev and r.prev > 0 and r.pat > 1.2 * r.prev else
                    "annual profit down 20%+" if r.prev and r.prev > 0 and r.pat < 0.8 * r.prev else None)
            if kind:
                rows.append({"symbol": H.final_symbol(r.symbol, changes), "time": r.filed, "type": kind})
        ev = pd.DataFrame(rows)
    return ev


def study() -> int:
    import history20 as H
    hist = ROOT / "research" / "data" / "hist"
    changes = H.symbol_changes(hist)
    print("Loading prices ...", flush=True)
    panel = H.load_panel(hist, fields=("open", "high", "low", "close", "value"))
    keep = panel["close"].index >= pd.Timestamp("2011-06-01")
    for k in ("open", "high", "low", "close", "value"):
        panel[k] = panel[k].loc[keep].where(panel["eq"].loc[keep] == 1)
    H.adjust_prices(panel, H.official_events(hist, changes), use_detection=False)
    c = panel["close"].astype(float)
    val = panel["value"].astype(float).rolling(20, min_periods=10).median().shift(1) / 1e7
    nifty = H.load_index(hist, ["nifty50"])["close"].reindex(c.index).ffill()
    idx = c.index
    ev = events(changes)
    ev = ev[(ev["time"] >= pd.Timestamp("2012-01-01")) & (ev["time"] <= pd.Timestamp("2026-09-30")) & ev["symbol"].isin(c.columns)]
    print(f"  {len(ev)} classified events", flush=True)
    # the base close: the last session before the news (news after 15:30 counts from that day's close)
    t = ev["time"]
    day = t.dt.normalize()
    after = (t.dt.hour * 60 + t.dt.minute) >= 15 * 60 + 30
    pos = idx.searchsorted(day, side="left")                              # first session on or after the news day
    on_day = (pos < len(idx)) & (idx[np.minimum(pos, len(idx) - 1)] == day)
    base = np.where(on_day & after, pos, pos - 1)
    ev = ev.assign(base=base)
    ev = ev[(ev["base"] >= 0) & (ev["base"] + max(HORIZONS) < len(idx))]
    ev = ev.sort_values("time")
    out, kept = [], []
    for (sym, kind), g in ev.groupby(["symbol", "type"]):                 # repeats within 20 sessions count once
        last = -10_000
        for b in g["base"]:
            if b - last > 20:
                kept.append((sym, kind, b))
                last = b
    E = pd.DataFrame(kept, columns=["symbol", "type", "base"])
    ci = c.columns.get_indexer(E["symbol"])
    cv, nv, vv = c.to_numpy(), nifty.to_numpy(), val.to_numpy()
    E["liquid"] = vv[E["base"], ci] >= 1.0
    for h in HORIZONS:
        s0, s1 = cv[E["base"], ci], cv[E["base"] + h, ci]
        E[f"x{h}"] = (s1 / s0 - 1) - (nv[E["base"] + h] / nv[E["base"]] - 1)
    E = E[E["liquid"] & np.isfinite(E["x5"])]
    E["date"] = idx[E["base"]]
    for kind, g in E.groupby("type"):
        per_day = g.groupby("date")["x5"].mean()
        t5 = per_day.mean() / per_day.std() * math.sqrt(len(per_day)) if len(per_day) > 5 and per_day.std() else np.nan
        a, b = g[g["date"] < SPLIT]["x5"], g[g["date"] >= SPLIT]["x5"]
        row = {"type": kind, "events": len(g), "mean1": g["x1"].mean() * 100, "mean5": g["x5"].mean() * 100,
               "mean20": g["x20"].mean() * 100, "median5": g["x5"].median() * 100, "beat5": (g["x5"] > 0).mean() * 100,
               "t5": t5, "mean5_A": a.mean() * 100 if len(a) else np.nan, "mean5_B": b.mean() * 100 if len(b) else np.nan,
               "events_A": len(a), "events_B": len(b)}
        row["consistent"] = bool(len(a) and len(b) and np.sign(row["mean5_A"]) == np.sign(row["mean5_B"])
                                 and abs(t5) >= 3)
        out.append(row)
    R = pd.DataFrame(out).sort_values("t5")
    pd.set_option("display.width", 220)
    print(R.round(2).to_string(index=False))
    R.to_csv(ROOT / "research" / "news22_results.csv", index=False)
    print("\nWritten research/news22_results.csv")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "study"])
    a = ap.parse_args(argv)
    if a.cmd == "download":
        download()
        return 0
    return study()


if __name__ == "__main__":
    sys.exit(main())
