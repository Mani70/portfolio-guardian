"""NSE index closes the strategies can read (today: the Quality 30 index for the paper experiment of Addendum 11).

INDstocks gives prices of stocks and ETFs, not of most NSE indices. NSE publishes one file a day with the close of
every index (ind_close_all_DDMMYYYY.csv, the file the research used). The evening plan reads it here, keeping the
last ~3 years in trader/state/indices/<key>.csv. The file appears in the evening; when today's isn't out yet at
16:10 the latest one is used (a one-day lag on a 150-day average). Missing days are filled at the next run.

  python -m trader.run indices       # update now; first time: seeds from research/data/nse/indices.csv if the
                                     # NSE download ran on this machine, else downloads the last 300 sessions
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd

log = logging.getLogger("trader.index_data")

URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"}
STATE = Path("trader") / "state" / "indices"
# key -> NSE names over time (lower case, spaces and hyphens removed); research/addendum7.py uses the same chain
CHAINS = {"quality30": ["nsequality30", "niftyquality30", "nifty100quality30"]}
KEEP_DAYS = 1100


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch not in " -")


def parse(text: str) -> Dict[str, float]:
    """{normalised index name: close} from one ind_close_all file."""
    out = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        r = {str(k).strip().lower(): (v or "").strip() for k, v in r.items() if k}
        name, close = r.get("index name", ""), r.get("closing index value", "")
        try:
            out[_norm(name)] = float(close.replace(",", ""))
        except ValueError:
            continue
    return out


def _path(root: Path, key: str) -> Path:
    return Path(root) / STATE / f"{key}.csv"


def load(root: Path, key: str) -> pd.Series:
    """Closes by date (empty if never fetched). Never raises."""
    try:
        df = pd.read_csv(_path(root, key), parse_dates=["date"])
        return df.set_index("date")["close"].sort_index()
    except Exception:                                             # noqa: BLE001
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], name="date"))


def _save(root: Path, key: str, s: pd.Series) -> None:
    p = _path(root, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s.index >= s.index.max() - pd.Timedelta(days=KEEP_DAYS)] if len(s) else s
    tmp = p.with_suffix(".tmp")
    s.rename("close").rename_axis("date").to_csv(tmp)
    os.replace(tmp, p)


def seed_from_research(root: Path) -> int:
    """First run on the server: the research download already holds years of index closes."""
    src = Path(root) / "research" / "data" / "nse" / "indices.csv"
    if not src.exists():
        return 0
    df = pd.read_csv(src, parse_dates=["date"], usecols=["date", "index", "close"])
    df["k"] = df["index"].map(_norm)
    n = 0
    for key, names in CHAINS.items():
        x = df[df.k.isin(names)].sort_values("date").drop_duplicates("date", keep="last")
        if len(x):
            _save(root, key, pd.concat([load(root, key), x.set_index("date")["close"]]))
            n += len(x)
    return n


def update(root: Path, today: date, get: Optional[Callable] = None, max_days: int = 15,
           is_session: Callable[[date], bool] = lambda d: d.weekday() < 5) -> str:
    """Fetch the files of sessions after the last stored day (up to `max_days` back). Returns a status line."""
    if get is None:
        import requests
        s = requests.Session()
        get = lambda url: s.get(url, headers=HEADERS, timeout=20)   # noqa: E731
    have = {k: load(root, k) for k in CHAINS}
    if any(len(v) < 200 for v in have.values()):
        seeded = seed_from_research(root)
        if seeded:
            have = {k: load(root, k) for k in CHAINS}
    last = min((v.index.max().date() for v in have.values() if len(v)), default=None)
    start = (last + timedelta(days=1)) if last else today - timedelta(days=max_days)
    start = max(start, today - timedelta(days=max_days))
    got, missing = 0, []
    d = start
    while d <= today:
        if is_session(d):
            try:
                r = get(URL.format(d=d))
                if r.status_code == 200 and "index name" in r.text[:300].lower():
                    closes = parse(r.text)
                    for key, names in CHAINS.items():
                        v = next((closes[n] for n in reversed(names) if n in closes), None)
                        if v is not None:
                            have[key].loc[pd.Timestamp(d)] = v
                    got += 1
                elif d < today:
                    missing.append(d)
            except Exception as e:                                # noqa: BLE001 - keep what we have
                log.warning("index file %s: %s", d, e)
                missing.append(d)
        d += timedelta(days=1)
    for key, s in have.items():
        if len(s):
            _save(root, key, s)
    parts = [f"{k}: {len(v)} days to {v.index.max():%d %b %Y}" if len(v) else f"{k}: none" for k, v in have.items()]
    return f"index files read {got}" + (f", missing {', '.join(f'{x:%d %b}' for x in missing)}" if missing else "") \
        + "; " + "; ".join(parts)


def above_average(s: pd.Series, d: date, days: int, max_age_days: int = 6) -> Optional[bool]:
    """Close above its `days`-session average on the latest day up to d; None if the data is missing or stale."""
    x = s.loc[:pd.Timestamp(d)]
    if len(x) < days or (pd.Timestamp(d) - x.index[-1]).days > max_age_days:
        return None
    return bool(x.iloc[-1] > x.iloc[-days:].mean())
