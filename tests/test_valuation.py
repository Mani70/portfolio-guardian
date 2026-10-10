"""Valuation tilt of the long-term core (trader/valuation.py; research/PREREGISTRATION.md Addenda 16/16a)."""
from datetime import date

import numpy as np
import pandas as pd

from trader import valuation as V
from trader.strategies.allocation import CoreAllocation

W = {"NIFTYBEES": 0.45, "JUNIORBEES": 0.15, "MON100": 0.20, "GOLDBEES": 0.10, "LIQUIDCASE": 0.10}
TILT = {"measure": "div_yield", "cut": 20, "max_age_days": 10,
        "expensive": {"NIFTYBEES": 0.30, "LIQUIDCASE": 0.25}, "cheap": {"NIFTYBEES": 0.55, "LIQUIDCASE": 0.0}}


def _history(tmp_path, last_yield, end="2026-09-30"):
    days = pd.bdate_range(end=end, periods=1000)
    dy = np.r_[np.linspace(1.0, 2.0, 999), last_yield]
    pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "index": "Nifty 50", "pe": 25 / dy, "pb": 3.0,
                  "div_yield": dy}).to_csv(tmp_path / "pepb.csv", index=False)
    return {**TILT, "_dir": str(tmp_path)}


def _frames(d):
    idx = pd.bdate_range(end=d, periods=5)
    return {k: pd.DataFrame({"close": [100.0] * 5}, index=idx) for k in W}


def test_low_yield_is_expensive_less_nifty_more_cash(tmp_path):
    s = CoreAllocation("core", {"weights": W, "valuation": _history(tmp_path, 1.01)})
    t = s.targets(_frames(date(2026, 9, 30)), date(2026, 9, 30))
    assert s.last_regime["name"] == "expensive" and "more than on 1% of days" in s.last_regime["why"] and "EXPENSIVE" in s.last_regime["why"]
    assert abs(t["NIFTYBEES"] - 0.30) < 1e-9 and abs(t["LIQUIDCASE"] - 0.25) < 1e-9 and abs(sum(t.values()) - 1) < 1e-9
    why = s.needs_rebalance({k: v for k, v in W.items()}, t, date(2026, 9, 30), False)
    assert why.startswith("NIFTYBEES 45% vs target 30%") and "shares are EXPENSIVE" in why


def test_high_yield_is_cheap_more_nifty_no_cash(tmp_path):
    s = CoreAllocation("core", {"weights": W, "valuation": _history(tmp_path, 1.99)})
    t = s.targets(_frames(date(2026, 9, 30)), date(2026, 9, 30))
    assert s.last_regime["name"] == "cheap" and "LIQUIDCASE" not in t and abs(t["NIFTYBEES"] - 0.55) < 1e-9
    assert "LIQUIDCASE" in s.symbols([])                                  # still watched: it is sold, then bought back


def test_middle_stale_or_missing_data_means_no_tilt(tmp_path):
    s = CoreAllocation("core", {"weights": W, "valuation": _history(tmp_path, 1.5)})
    assert s.targets(_frames(date(2026, 9, 30)), date(2026, 9, 30)) == W and s.last_regime["name"] == "neutral"
    old = _history(tmp_path, 1.01, end="2026-08-14")
    r = V.regime(old, date(2026, 9, 30))
    assert r["name"] == "neutral" and "is from 14 Aug 2026" in r["why"]
    assert V.regime({**TILT, "_dir": str(tmp_path / "none")}, date(2026, 9, 30))["why"].startswith("price-level check skipped (no Nifty 50 valuation data)")


def test_regime_is_read_at_the_month_end_anchor(tmp_path):
    p = _history(tmp_path, 1.01)                                          # expensive on 30 Sep
    days = pd.bdate_range("2026-10-01", "2026-10-09")
    with open(tmp_path / "pepb.csv", "a") as f:                           # cheap from 1 Oct on
        for d in days:
            f.write(f"{d:%Y-%m-%d},Nifty 50,10,3,2.5\n")
    assert V.anchor(date(2026, 10, 8)) == date(2026, 9, 30)
    assert V.regime(p, date(2026, 10, 8))["name"] == "expensive"          # mid-month: the review's regime holds
    assert V.anchor(date(2026, 9, 30)) == date(2026, 9, 30)
