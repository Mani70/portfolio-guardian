"""Intraday research on the full 5-minute cache, judged against research/PREREGISTRATION.md.

  python research/intraday_research.py
Thresholds for "strong signal" variants are chosen on the FIRST half only, then judged on the second.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backtest.intraday_lab as L                                    # noqa: E402

STOCK_COST = 0.11885 / 100     # round trip at ₹1 lakh: brokerage, STT 0.025% sell, stamp, exchange, GST, slippage 0.03%x2
ETF_COST = 0.075 / 100         # same for ETFs: STT 0.001% sell, slippage 0.02%x2
ETFS = {"NIFTYBEES", "BANKBEES"}


def evaluate(tr: pd.DataFrame, cost: float, n_days: int, mid: int, slots: int = 5) -> dict:
    if tr.empty:
        return dict(trades=0)
    t = tr.copy()
    t["net"] = t["gross"] - cost
    h1, h2 = t[t.day < mid], t[t.day >= mid]
    # portfolio of `slots` equal slots: daily sum / slots; t-stat on days with trades
    daily = t.groupby("day")["net"].sum() / slots
    tstat = daily.mean() / daily.std() * math.sqrt(len(daily)) if len(daily) > 2 and daily.std() > 0 else float("nan")
    full = daily.reindex(range(15, n_days), fill_value=0.0)
    ann = (1 + full).prod() ** (250 / len(full)) - 1
    return dict(trades=len(t), win=(t.net > 0).mean() * 100, gross_bps=t.gross.mean() * 1e4,
                net_bps=t.net.mean() * 1e4, h1_bps=h1.net.mean() * 1e4 if len(h1) else np.nan,
                h2_bps=h2.net.mean() * 1e4 if len(h2) else np.nan, n_h1=len(h1), n_h2=len(h2),
                t_day=tstat, annual_pct=ann * 100)


def main():
    syms, days, A, ctx = L.load(ROOT / "cache")
    n = len(days)
    mid = n // 2
    etf_idx = [i for i, s in enumerate(syms) if s in ETFS]
    stk_idx = [i for i, s in enumerate(syms) if s not in ETFS]
    print(f"{len(stk_idx)} stocks + {len(etf_idx)} ETFs, {n} sessions {days[0]:%d %b %Y} - {days[-1]:%d %b %Y}; "
          f"second half from {days[mid]:%d %b %Y}")
    atrp = ctx["atr"] / ctx["prev_close"]
    sub = atrp.copy()
    sub[etf_idx] = np.nan
    rank = pd.DataFrame(sub).rank(axis=0, ascending=False).values

    methods = {
        "ORB 15m": lambda: L.orb(A, ctx, 15),
        "ORB 30m": lambda: L.orb(A, ctx, 30),
        "ORB 30m mkt filter": lambda: L.orb(A, ctx, 30, market_filter=True),
        "Noise area": lambda: L.noise_area(A, ctx),
        "Noise area 1.5x": lambda: L.noise_area(A, ctx, vol_mult=1.5),
        "VWAP trend 30m": lambda: L.vwap_trend(A, ctx, 6),
        "Late-day momentum": lambda: L.late_momentum(A, ctx),
        "Gap fade >1%": lambda: L.gap_trade(A, ctx, 0.01, "fade"),
        "Gap and go >1%": lambda: L.gap_trade(A, ctx, 0.01, "go"),
        "Overreaction fade": lambda: L.first_hour_reversal(A, ctx),
    }
    rows = []
    cache = {}
    for name, fn in methods.items():
        tr = fn()
        cache[name] = tr
        groups = {"stocks": tr[tr.sym.isin(stk_idx)],
                  "top10 vol": tr[[rank[s, d] <= 10 if s in stk_idx else False for s, d in zip(tr.sym, tr.day)]],
                  "ETFs": tr[tr.sym.isin(etf_idx)]}
        for g, t in groups.items():
            cost = ETF_COST if g == "ETFs" else STOCK_COST
            slots = 2 if g == "ETFs" else (10 if g == "top10 vol" else len(stk_idx))
            for side in ("both", "long", "short"):
                tt = t if side == "both" else t[t.side == side]
                rows.append(dict(method=name, universe=g, side=side, **evaluate(tt, cost, n, mid, slots)))
    res = pd.DataFrame(rows)
    res.to_csv(ROOT / "research" / "intraday_results.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(res.round(2).to_string(index=False))

    # ---------- strong-signal variants: threshold picked on the first half ----------
    print("\nStrong-signal thresholds (picked on the first half, judged on the second):")
    strong = []
    gap = (A[:, :, 0, 0] / ctx["prev_close"] - 1)

    def pick(label, variants, cost_of, min_trades=30):
        best = None
        for thr, t in variants:
            t = t.copy()
            t["net"] = t["gross"] - [cost_of(s) for s in t.sym]
            h1 = t[t.day < mid]
            if len(h1) >= min_trades and (best is None or h1.net.mean() > best[1]):
                best = (thr, h1.net.mean(), t)
            strong.append(dict(test=label, threshold=thr, n_h1=len(h1), h1_bps=h1.net.mean() * 1e4 if len(h1) else np.nan,
                               n_h2=int((t.day >= mid).sum()),
                               h2_bps=t[t.day >= mid].net.mean() * 1e4 if (t.day >= mid).any() else np.nan))
        if best:
            thr, _, t = best
            h2 = t[t.day >= mid]
            daily = h2.groupby("day")["net"].sum()
            ts = daily.mean() / daily.std() * math.sqrt(len(daily)) if len(daily) > 2 and daily.std() > 0 else float("nan")
            print(f"  {label}: chosen {thr} -> second half {len(h2)} trades, {h2.net.mean() * 1e4:+.1f} bps net, t {ts:.2f}")

    cost_of = lambda s: ETF_COST if s in etf_idx else STOCK_COST
    for side_name, side in (("long", "long"), ("short", "short"), ("both", None)):
        gf = cache["Gap fade >1%"]
        gf = gf[gf.sym.isin(stk_idx)]
        if side:
            gf = gf[gf.side == side]
        pick(f"gap fade stocks {side_name}", [(g, gf[gf.score >= g]) for g in (0.01, 0.015, 0.02, 0.03)], cost_of)
    orb = cache["ORB 15m"]
    orb = orb[orb.sym.isin(stk_idx)]
    pick("ORB 15m stocks, relvol", [(r, orb[orb.score >= r]) for r in (1.0, 1.5, 2.0, 3.0)], cost_of)
    al = orb[[(g > 0) == (side == "long") for g, side in zip(gap[orb.sym, orb.day], orb.side)]]
    pick("ORB 15m stocks, relvol + gap aligned", [(r, al[al.score >= r]) for r in (1.0, 1.5, 2.0, 3.0)], cost_of)
    lm = cache["Late-day momentum"]
    for g, idx in (("ETFs", etf_idx), ("stocks", stk_idx)):
        t = lm[lm.sym.isin(idx)]
        pick(f"late momentum {g}, |first 30m|", [(x, t[t.score >= x]) for x in (0.0, 0.005, 0.01, 0.015)], cost_of)
    for g, idx in (("ETFs", etf_idx), ("stocks top10", None)):
        variants = []
        for vm in (1.0, 1.5, 2.0):
            t = L.noise_area(A, ctx, vol_mult=vm)
            t = t[t.sym.isin(etf_idx)] if idx is not None else t[[rank[s, d] <= 10 if s in stk_idx else False
                                                                    for s, d in zip(t.sym, t.day)]]
            variants.append((vm, t))
        pick(f"noise area {g}, band", variants, cost_of)
    ov = cache["Overreaction fade"]
    ov = ov[ov.sym.isin(stk_idx)]
    pick("overreaction fade stocks, ATR move", [(z, ov[ov.score >= z]) for z in (1.5, 2.0, 2.5, 3.0)], cost_of, 20)
    pd.DataFrame(strong).to_csv(ROOT / "research" / "intraday_strong.csv", index=False)
    print(pd.DataFrame(strong).round(2).to_string(index=False))


if __name__ == "__main__":
    main()
