"""Can intraday trading days be picked in advance? Research/PREREGISTRATION.md, Addendum 3.

For each intraday strategy, every pre-open feature is split into groups on the FIRST half of the sample, the
best group is chosen there, and only the SECOND half judges it (net > 0 and day t-stat >= 2).

  python research/intraday_days.py
"""
from __future__ import annotations

import glob
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backtest.intraday_lab as L                                    # noqa: E402

STOCK_COST, ETF_COST = 0.11885 / 100, 0.075 / 100
ETFS = {"NIFTYBEES", "BANKBEES"}


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / x.std() * math.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def daily_features(daily: dict, days: pd.DatetimeIndex, stock_syms) -> pd.DataFrame:
    """Everything known before the open of each day, from daily bars."""
    nb = daily["NIFTYBEES"]
    rng = nb["high"] - nb["low"]
    f = pd.DataFrame(index=nb.index)
    f["nr7"] = (rng <= rng.rolling(7).min()).shift(1)
    f["above200"] = (nb["close"] > nb["close"].rolling(200).mean()).shift(1)
    rr = []
    for s in stock_syms:
        d = daily[s]
        pc = d["close"].shift(1)
        tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        rr.append(((d.high - d.low) / atr).shift(1).rename(s))
    f["prev_range_atr"] = pd.concat(rr, axis=1).median(axis=1)
    f = f.reindex(days)
    f["dow"] = days.dayofweek
    f["expiry_week"] = (days + pd.offsets.MonthEnd(0) - days).days <= 6
    return f


def choose_and_test(ret: pd.Series, feats: pd.DataFrame, mid, min_days: int, label: str):
    rows = []
    h1, h2 = ret[ret.index < mid], ret[ret.index >= mid]
    rows.append(dict(strategy=label, feature="(all days)", group="all", h1_days=len(h1), h1_bps=h1.mean() * 1e4,
                     h2_days=len(h2), h2_bps=h2.mean() * 1e4, h2_t=tstat(h2), passed=bool(h2.mean() > 0 and tstat(h2) >= 2)))
    for col in feats.columns:
        x = feats[col].reindex(ret.index)
        if x.dropna().nunique() <= 5:
            groups = x.astype("object")
        else:
            q = x[x.index < mid].quantile([1 / 3, 2 / 3]).values
            groups = pd.Series(np.where(x <= q[0], "low", np.where(x <= q[1], "mid", "high")), index=x.index)
            groups[x.isna()] = np.nan
        best = None
        for g in pd.Series(groups.dropna().unique()):
            a = ret[(groups == g) & (ret.index < mid)]
            if len(a) >= min_days and (best is None or a.mean() > best[1]):
                best = (g, a.mean(), len(a))
        if best is None:
            continue
        b = ret[(groups == best[0]) & (ret.index >= mid)]
        rows.append(dict(strategy=label, feature=col, group=str(best[0]), h1_days=best[2], h1_bps=best[1] * 1e4,
                         h2_days=len(b), h2_bps=b.mean() * 1e4 if len(b) else np.nan, h2_t=tstat(b),
                         passed=bool(len(b) and b.mean() > 0 and tstat(b) >= 2)))
    return rows


def main():
    syms, days, A, ctx = L.load(ROOT / "cache")
    daily = {}
    for p in glob.glob(str(ROOT / "cache" / "history" / "*.csv")):
        daily[os.path.basename(p)[:-4]] = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
    stk = [i for i, s in enumerate(syms) if s not in ETFS]
    etf = [i for i, s in enumerate(syms) if s in ETFS]
    mid5 = days[len(days) // 2]
    rows = []

    # ---- 5-minute sample: ORB 15 min, stocks and ETFs
    f5 = daily_features(daily, days, [syms[i] for i in stk])
    gap = A[:, :, 0, 0] / ctx["prev_close"] - 1
    f5["mkt_gap_abs"] = np.abs(np.nanmedian(gap[stk], axis=0))
    f5["mkt_gap"] = np.nanmedian(gap[stk], axis=0)
    f5["first_bar_range_atr"] = np.nanmedian(((A[:, :, 0, 1] - A[:, :, 0, 2]) / ctx["atr"])[stk], axis=0)
    orb = L.orb(A, ctx, 15)
    for label, idx, cost in (("ORB 15m stocks", stk, STOCK_COST), ("ORB 15m ETFs", etf, ETF_COST)):
        t = orb[orb.sym.isin(idx)].copy()
        t["net"] = t.gross - cost
        r = t.groupby("day")["net"].mean()
        r.index = days[r.index]
        f = f5.copy()
        f["prev5_pnl"] = r.reindex(days, fill_value=0).rolling(5).sum().shift(1)
        rows += choose_and_test(r, f, mid5, 15, label)

    # ---- 12-year daily sample: stock-specific gap-down reversal (open -> close)
    stocks = [s for s in daily if s not in {"NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES", "MON100",
                                             "LIQUIDBEES", "ITBEES", "PHARMABEES", "PSUBNKBEES", "CPSEETF"}]
    o = pd.DataFrame({s: daily[s]["open"] for s in stocks})
    c = pd.DataFrame({s: daily[s]["close"] for s in stocks})
    g = o / c.shift(1) - 1
    oc = c / o - 1
    big = g.abs() > 0.4                                    # split/bonus days are not gaps
    g[big], oc[big] = np.nan, np.nan
    mkt = g.median(axis=1)
    ok = (g <= -0.03).mul(mkt > -0.01, axis=0)
    net = (oc - STOCK_COST).where(ok)
    r = net.mean(axis=1).dropna()
    r = r[r.index >= "2015-01-01"]
    dd = pd.DatetimeIndex(sorted(set().union(*[daily[s].index for s in stocks])))
    dd = dd[dd >= "2015-01-01"]
    fd = daily_features(daily, dd, stocks)
    fd["mkt_gap_abs"] = mkt.abs().reindex(dd)
    fd["mkt_gap"] = mkt.reindex(dd)
    fd["prev5_pnl"] = r.reindex(dd, fill_value=0).rolling(5).sum().shift(1)
    midd = r.index[len(r) // 2]
    rows += choose_and_test(r, fd, midd, 30, "Gap-down reversal (daily, 12y)")

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "intraday_days.csv", index=False)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 100)
    print(f"5-minute sample {days[0]:%d %b %Y} - {days[-1]:%d %b %Y}, second half from {mid5:%d %b %Y}; "
          f"daily gap sample second half from {midd:%d %b %Y}")
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
