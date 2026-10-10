"""F&O practice run (trader/fo_paper.py): opens after an expiry, values daily, settles at expiry, paper only."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trader import fo_paper as P

COLS = ["date", "kind", "expiry", "strike", "close", "settle", "traded", "oi", "lot", "spot"]


def _day(d: date, spot: float, expiries, monthly: date) -> pd.DataFrame:
    rows = [{"date": d, "kind": "FUT", "expiry": monthly, "strike": 0.0, "close": spot, "settle": spot, "traded": 100,
             "oi": 1, "lot": 65, "spot": spot}]
    for e in expiries:
        days = max((e - d).days, 0) + 1
        for k in np.arange(9000, 11050, 50):
            put = max(k - spot, 0) + 40 * np.exp(-abs(k - spot) / 300) * np.sqrt(days / 7)
            call = max(spot - k, 0) + 40 * np.exp(-abs(k - spot) / 300) * np.sqrt(days / 7)
            for kind, px in (("PE", put), ("CE", call)):
                rows.append({"date": d, "kind": kind, "expiry": e, "strike": float(k), "close": round(max(px, 0.05), 2),
                             "settle": round(max(px, 0.05), 2), "traded": 50, "oi": 1, "lot": 65, "spot": spot})
    return pd.DataFrame(rows, columns=COLS)


def _store(tmp, days):
    store = tmp / "fo"
    store.mkdir()
    for d, df in days.items():
        df.to_csv(store / f"fo_{d:%Y%m%d}.csv", index=False)
    return store


def test_weekly_cycle_opens_after_expiry_values_and_settles(tmp_path):
    mon = date(2026, 10, 12)
    w1, w2, m1 = mon + timedelta(days=1), mon + timedelta(days=8), date(2026, 10, 27)
    days = {}
    for i in range(0, 9):
        d = mon + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        exps = [e for e in (w1, w2) if e >= d] + [m1]
        days[d] = _day(d, 10000.0, sorted(set(exps)), m1)
    store = _store(tmp_path, days)
    state = tmp_path / "s.json"
    said = {}
    for d in sorted(days):
        msgs = []
        P.run(msgs.append, today=d, fetch=False, state_path=state, store=store, trend_fn=lambda: True)
        said[d] = "\n".join(msgs)
    st = P.load_state(state)
    assert "No practice position yet" in said[mon]
    assert "Opened: Bull put spread (weekly)" in said[w1 + timedelta(days=1)]          # the session after the expiry
    assert "Iron condor (weekly)" in said[w1 + timedelta(days=1)] and "Most it can lose" in said[w1 + timedelta(days=1)]
    assert "S1 monthly" not in st["open"]                                               # no monthly expiry has passed
    closed = {c["name"]: c for c in st["closed"]}
    assert set(closed) == {"S1 weekly", "S2 weekly"} and closed["S1 weekly"]["pnl"] > 0  # Nifty flat: fees kept
    assert "Practice run so far: 2 finished, 2 profitable" in said[w2]
    assert "paper only: no real money, no orders" in said[w2] and "1-minute guide" in said[w2]
    assert P.run(print, today=w2, fetch=False, state_path=state, store=store) == "fo-paper: already reported today"


def test_a_crash_below_the_protection_loses_at_most_the_spread(tmp_path):
    mon = date(2026, 10, 12)
    w1, w2, m1 = mon + timedelta(days=1), mon + timedelta(days=8), date(2026, 10, 27)
    days = {d: _day(d, s, sorted({e for e in (w1, w2) if e >= d} | {m1}), m1)
            for d, s in ((mon, 10000.0), (w1, 10000.0), (w1 + timedelta(days=1), 10000.0), (w2, 8800.0))}
    store = _store(tmp_path, days)
    state = tmp_path / "s.json"
    for d in sorted(days):
        P.run(lambda m: None, today=d, fetch=False, state_path=state, store=store, trend_fn=lambda: True)
    st = P.load_state(state)
    s1 = next(c for c in st["closed"] if c["name"] == "S1 weekly")
    width = (9700 - 9400) * 65                                                         # 3% / 6% below 10,000
    assert -width - 2000 < s1["pnl"] < -width + 3000                                   # the bought put caps the loss


def test_missing_file_waits(tmp_path):
    (tmp_path / "fo").mkdir()
    assert "not out yet" in P.run(print, today=date(2026, 10, 12), fetch=False, state_path=tmp_path / "s.json",
                                  store=tmp_path / "fo")
