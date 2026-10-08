"""The Nifty 50's valuation (P/E, P/B, dividend yield) and the long-term core's valuation tilt.

research/PREREGISTRATION.md Addenda 16 / 16a: at each month's review the Nifty 50's dividend yield is ranked among all
its daily values since 1999. In the most expensive 20% of days (lowest yield) the core holds less Nifty 50 and more
cash; in the cheapest 20% more Nifty 50 and no cash; otherwise its usual weights. No data, or stale data: no tilt.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "cache" / "valuation"
COLS = ("pe", "pb", "div_yield")
log = logging.getLogger("trader.valuation")


def update(today: Optional[date] = None, vdir: Path = DIR) -> None:
    """Fetch the history from niftyindices.com (the first time since 1999, then the current year). Best effort."""
    today = today or date.today()
    try:
        sys.path.insert(0, str(ROOT / "research"))
        import download_history as DH                               # noqa: E402 - proven niftyindices client
        import download_nse as DN                                   # noqa: E402
        DH.index_history(DN.Store(vdir), 1999, today.year, pepb=True)
        v = load(vdir)                                              # the current year is fetched again each run
        if v is not None:
            v.reset_index().assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d")).to_csv(vdir / "pepb.csv",
                                                                                            index=False)
    except Exception as e:                                          # noqa: BLE001 - callers go on without it
        log.warning("valuation history not updated: %s", e)


def load(vdir: Path = DIR) -> Optional[pd.DataFrame]:
    p = Path(vdir) / "pepb.csv"
    if not p.exists():
        return None
    v = pd.read_csv(p)
    v["date"] = pd.to_datetime(v["date"])
    v = v.drop_duplicates("date", keep="last").set_index("date").sort_index()
    return v[[c for c in COLS if c in v.columns]].astype(float)


def percentile(v: pd.DataFrame, col: str, asof) -> Optional[dict]:
    """The latest value of `col` on or before `asof` and its percentile among all values up to then."""
    x = v[col].dropna().loc[:pd.Timestamp(asof)]
    if len(x) < 250:
        return None
    last = float(x.iloc[-1])
    return {"value": last, "pct": float((x <= last).mean() * 100), "date": x.index[-1].date()}


def anchor(d: date, sessions=None, holidays=()) -> date:
    """The review's session if it is the month's last, else the previous month's last day (Addendum 16a)."""
    from .strategies.momentum import last_session_of_month
    return d if last_session_of_month(d, sessions, holidays) else d.replace(day=1) - timedelta(days=1)


def regime(params: dict, d: date, sessions=None, holidays=(), vdir: Optional[Path] = None) -> dict:
    """{"name": "expensive" | "cheap" | "neutral", "weights": overrides or {}, "why": text}."""
    measure = params.get("measure", "div_yield")
    cut = float(params.get("cut", 20))
    at = anchor(d, sessions, holidays)
    v = load(Path(vdir or params.get("_dir") or DIR))
    p = percentile(v, measure, at) if v is not None and measure in v.columns else None
    if p is None:
        return {"name": "neutral", "weights": {}, "why": "valuation tilt off: no Nifty 50 valuation data"}
    label = "dividend yield" if measure == "div_yield" else measure.upper().replace("_", " ")
    if (at - p["date"]).days > int(params.get("max_age_days", 10)):
        return {"name": "neutral", "weights": {},
                "why": f"valuation tilt off: the latest Nifty 50 {label} is from {p['date']:%d %b %Y}"}
    high_is_cheap = measure == "div_yield"
    rich = p["pct"] <= cut if high_is_cheap else p["pct"] >= 100 - cut
    cheap = p["pct"] >= 100 - cut if high_is_cheap else p["pct"] <= cut
    name = "expensive" if rich else "cheap" if cheap else "neutral"
    why = (f"Nifty 50 {label} {p['value']:.2f} on {p['date']:%d %b %Y}, above {p['pct']:.0f}% of days since 1999: "
           f"{name}")
    return {"name": name, "weights": dict((params.get(name) or {}) if name != "neutral" else {}), "why": why}
