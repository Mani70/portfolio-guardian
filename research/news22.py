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


def download() -> None:
    NEWS.mkdir(parents=True, exist_ok=True)
    out, done_p = NEWS / "announcements.csv", NEWS / "done.txt"
    done = set(done_p.read_text().split()) if done_p.exists() else set()
    s = requests.Session()
    s.headers.update(HEAD)
    d, n = START, 0
    while d <= END:
        e = min(d + timedelta(days=WINDOW - 1), END)
        key = d.isoformat()
        if key not in done:
            rows = None
            for k in range(4):
                try:
                    r = s.get(API.format(d.strftime("%d-%m-%Y"), e.strftime("%d-%m-%Y")), timeout=60)
                    if r.status_code == 200:
                        rows = r.json()
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(3 * (k + 1))
            if rows is None:
                print(f"  {d}: failed, left for the next run", flush=True)
            else:
                df = pd.DataFrame([{"time": x.get("an_dt"), "symbol": x.get("symbol"), "category": x.get("desc"),
                                    "text": str(x.get("attchmntText") or "")[:400].replace("\n", " ")} for x in rows])
                if len(df):
                    df.to_csv(out, mode="a", header=not out.exists(), index=False)
                with done_p.open("a") as fh:
                    fh.write(key + "\n")
                n += 1
                if n % 50 == 0:
                    print(f"  {d} ({len(rows)} announcements)", flush=True)
            time.sleep(0.5)
        d = e + timedelta(days=1)
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
