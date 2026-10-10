"""Addendum 21 (research/PREREGISTRATION.md): one disciplined search for a short-term edge.

  python research/search21.py            # writes research/search21_insample.csv and research/search21_results.csv

256 variants in three families. Each is scored on 2012-2019 only (net profit per trade after costs, t-statistic);
a variant qualifies with t >= 3.5 and profit in both 2012-15 and 2016-19; the best qualifying variant of each family
is tested once on 2020-2026.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import history20 as H                                                # noqa: E402
from backtest.engine import Costs                                    # noqa: E402

HIST = ROOT / "research" / "data" / "hist"
IN_A, IN_B, IN_END = pd.Timestamp("2012-01-01"), pd.Timestamp("2016-01-01"), pd.Timestamp("2019-12-31")
OOS, OOS_END = pd.Timestamp("2020-01-01"), pd.Timestamp("2026-09-30")
T_SELECT, T_TEST = 3.5, 2.0
K, ACCOUNT = 10, 384_000.0
POS_VALUE = ACCOUNT / K
SLIP = 0.0005


def delivery_cost() -> float:
    c = Costs(dp_per_sell=21.83)
    return (c.buy(POS_VALUE) + c.sell(POS_VALUE)) / POS_VALUE + 2 * SLIP


def intraday_cost() -> float:
    v = POS_VALUE
    brk = 2 * 10.0
    exch, sebi = 2 * v * 0.0000297, 2 * v * 1e-6
    stt, stamp = 0.00025 * v, 0.00003 * v
    return (brk + exch + sebi + stt + stamp + 0.18 * (brk + exch + sebi)) / v + 2 * SLIP


# ---------------------------------------------------------------- data
def load() -> dict:
    panel = H.load_panel(HIST, fields=("open", "high", "low", "close", "value", "volume"))
    eq = panel.pop("eq")
    keep = panel["close"].index >= pd.Timestamp("2010-06-01")
    for k in ("open", "high", "low", "close", "value", "volume"):
        panel[k] = panel[k].loc[keep].where(eq.loc[keep] == 1).astype("float64")
    panel["eq"] = eq.loc[keep]
    H.adjust_prices(panel, H.official_events(HIST, H.symbol_changes(HIST)), use_detection=False)
    for k in ("open", "high", "low", "close"):
        panel[k] = panel[k].astype("float64")
    panel["volume"] = panel["volume"] / panel["factor"].iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)
    return panel


def universe_mask(panel) -> pd.DataFrame:
    """True where a stock is among the month's 100 most traded (as of the previous month-end)."""
    uni = H.monthly_universe(panel, 100)
    c = panel["close"]
    m = pd.DataFrame(False, index=c.index, columns=c.columns)
    ends = sorted(uni)
    for i, e in enumerate(ends):
        nxt = ends[i + 1] if i + 1 < len(ends) else c.index[-1] + pd.Timedelta(days=1)
        rows = (c.index > e) & (c.index <= nxt)
        cols = [s for s in uni[e] if s in m.columns]
        m.loc[rows, cols] = True
    return m


def market_ok(index: pd.DatetimeIndex) -> pd.Series:
    n = H.load_index(HIST, ["nifty50"])["close"].reindex(index).ffill()
    return (n > n.rolling(200).mean()).fillna(False)


def rsi2(c: pd.DataFrame) -> pd.DataFrame:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ---------------------------------------------------------------- F1 / F2 signals: a score where higher = picked first
def f1_signals(p, uni) -> Dict[str, pd.DataFrame]:
    c, v = p["close"], p["volume"]
    sig = {}
    for n in (1, 3, 5, 10):
        sig[f"reversal {n}d"] = -(c / c.shift(n) - 1)
    mom126 = c.shift(1) / c.shift(127) - 1
    for n in (20, 55, 120, 250):
        at_high = c >= c.rolling(n, min_periods=n).max()
        sig[f"breakout {n}d"] = mom126.where(at_high)
    for n in (21, 63, 126, 252):
        sig[f"momentum {n}d"] = c.shift(1) / c.shift(1 + n) - 1
    r2 = rsi2(c)
    up = c > c.rolling(200, min_periods=200).mean()
    for thr in (5, 10, 20):
        sig[f"pullback rsi2<{thr}"] = (-r2).where(up & (r2 < thr))
    vr = v / v.shift(1).rolling(20, min_periods=15).mean()
    for k in (2, 3):
        sig[f"volume surge {k}x"] = vr.where((c > c.shift(1)) & (vr >= k))
    return {k: s.where(uni) for k, s in sig.items()}


def f2_signals(p, uni) -> Dict[str, Tuple[pd.DataFrame, int]]:
    """Scores known at the OPEN of day t (gap uses today's open), side +1 long / -1 short."""
    c, o = p["close"], p["open"]
    gap = o / c.shift(1) - 1
    uni_prev = uni.shift(1, fill_value=False)
    sig = {}
    for g in (1, 2, 3, 4):
        sig[f"buy gap-down {g}%"] = ((-gap).where(gap <= -g / 100), +1)
        sig[f"short gap-up {g}%"] = (gap.where(gap >= g / 100), -1)
    ret1 = (c / c.shift(1) - 1).shift(1)                                # yesterday's move, known at today's open
    sig["buy yesterday's losers"] = (-ret1, +1)
    sig["short yesterday's gainers"] = (ret1, -1)
    return {k: (s.where(uni_prev), side) for k, (s, side) in sig.items()}


def top_k(score: pd.DataFrame, k: int = K) -> pd.DataFrame:
    r = score.rank(axis=1, ascending=False, method="first")
    return (r <= k) & score.notna()


def nw_t(x: pd.Series, lags: int) -> float:
    x = x.dropna()
    n = len(x)
    if n < 20 or x.std() == 0:
        return float("nan")
    e = (x - x.mean()).to_numpy()
    var = (e * e).sum() / n
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[:-k] * e[k:]).sum() / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def f1_trades(p, picks: pd.DataFrame, h: int, cost: float) -> pd.Series:
    """Per signal date: the mean net return of the day's picks (bought next open, sold at the close h sessions after
    the signal; a stock that stops trading is sold at its last close)."""
    o, c = p["open"], p["close"]
    entry = o.shift(-1)
    exit_ = c.ffill(limit=h).shift(-h)
    r = (exit_ / entry - 1).where(picks)
    per_day = r.mean(axis=1) - cost
    return per_day.where(picks.any(axis=1))


def f2_trades(p, picks: pd.DataFrame, side: int, cost: float) -> pd.Series:
    r = (p["close"] / p["open"] - 1).where(picks) * side
    per_day = r.mean(axis=1) - cost
    return per_day.where(picks.any(axis=1))


def score_series(name: str, fam: str, per: pd.Series, lag: int) -> dict:
    ins = per.loc[IN_A:IN_END].dropna()
    a, b = ins.loc[:IN_B - pd.Timedelta(days=1)], ins.loc[IN_B:]
    return {"family": fam, "variant": name, "n": len(ins), "mean_pct": ins.mean() * 100 if len(ins) else np.nan,
            "t": nw_t(ins, lag), "mean_A_pct": a.mean() * 100 if len(a) else np.nan,
            "mean_B_pct": b.mean() * 100 if len(b) else np.nan, "win_pct": (ins > 0).mean() * 100 if len(ins) else np.nan}


# ---------------------------------------------------------------- F3 options
def f3_variants():
    sys.path.insert(0, str(ROOT / "research"))
    import fo20 as F
    out = []
    for cad in ("monthly", "weekly"):
        for m in (2, 3, 4, 5):
            for w in (2, 3):
                legs = [F.Leg("PE", 1 - m / 100, -1), F.Leg("PE", 1 - (m + w) / 100, +1)]
                for trend in (False, True):
                    for tp in (False, True):
                        out.append((f"bull put {m}%/{m + w}% {cad}{' trend' if trend else ''}{' take-profit' if tp else ''}",
                                    cad, legs, trend, tp))
        for m in (3, 4, 5, 6):
            for w in (2, 3):
                legs = [F.Leg("PE", 1 - m / 100, -1), F.Leg("PE", 1 - (m + w) / 100, +1),
                        F.Leg("CE", 1 + m / 100, -1), F.Leg("CE", 1 + (m + w) / 100, +1)]
                for tp in (False, True):
                    out.append((f"iron condor {m}%/{m + w}% {cad}{' take-profit' if tp else ''}", cad, legs, False, tp))
        out.append((f"long straddle {cad}", cad, [F.Leg("CE", 1.0, +1), F.Leg("PE", 1.0, +1)], False, False))
        out.append((f"long strangle 2% {cad}", cad, [F.Leg("CE", 1.02, +1), F.Leg("PE", 0.98, +1)], False, False))
    return out


def f3_run(df, spot, sma, prices, dates, pairs, legs, trend, tp):
    import fo20 as F
    rows = []
    for e0, e1 in pairs:
        nxt = dates[dates.searchsorted(e0, side="right")] if dates.searchsorted(e0, side="right") < len(dates) else None
        if nxt is None or e1 > dates[-1] or nxt >= e1:
            continue
        d = nxt
        s, fin = spot.get(d), spot.get(e1)
        if s is None or fin is None or np.isnan(s) or np.isnan(fin):
            continue
        if trend and not (s > sma.get(d, np.nan)):
            continue
        pos = F.open_position(df.get(d), e1, s, legs, d)
        if pos is None:
            continue
        credit, costs = pos["credit"], pos["costs"]
        closed = None
        if tp and credit > 0:
            for day in dates[(dates > d) & (dates < e1)]:
                debit, exit_cost, ok = 0.0, 0.0, True
                for leg, k, _ in pos["legs"]:
                    px = prices.get((e1, leg.kind, k, day))
                    if px is None or np.isnan(px):
                        ok = False
                        break
                    debit += -leg.side * px * F.LOT                     # buy back the sold, sell the bought
                    exit_cost += F.leg_cost(px, F.LOT, leg.side < 0, day)
                if ok and debit <= 0.5 * credit:
                    closed = credit - debit - costs - exit_cost
                    break
        if closed is None:
            val, stt = F.settle(pos, fin)
            closed = credit + val - costs - stt
        rows.append({"open": d, "expiry": e1, "pnl": closed, "risk": F.max_loss(pos) if credit > 0 else -credit + costs})
    return pd.DataFrame(rows)


def main() -> int:
    print("Loading the stock panel ...", flush=True)
    p = load()
    uni = universe_mask(p)
    mkt = market_ok(p["close"].index)
    dc, ic = delivery_cost(), intraday_cost()
    print(f"  delivery round trip {dc * 100:.3f}%, intraday {ic * 100:.3f}% of a ₹{POS_VALUE:,.0f} position", flush=True)
    rows, series = [], {}

    print("F1 swing stocks ...", flush=True)
    for name, sc in f1_signals(p, uni).items():
        for filt in (False, True):
            s = sc.where(mkt, axis=0) if filt else sc
            picks = top_k(s)
            for h in (1, 5, 10, 20):
                v = f"{name}, hold {h}{', market filter' if filt else ''}"
                per = f1_trades(p, picks, h, dc)
                series[("F1", v)] = (per, h)
                rows.append(score_series(v, "F1", per, max(0, h - 1)))
    print("F2 intraday stocks ...", flush=True)
    for name, (sc, side) in f2_signals(p, uni).items():
        for filt in (False, True):
            s = sc.where(mkt.shift(1, fill_value=False), axis=0) if filt else sc
            per = f2_trades(p, top_k(s), side, ic)
            v = f"{name}{', market filter' if filt else ''}"
            series[("F2", v)] = (per, 0)
            rows.append(score_series(v, "F2", per, 0))

    print("F3 Nifty options ...", flush=True)
    import fo20 as F
    df = F.load()
    spot = F.spot_series(df)
    sma = spot.rolling(200).mean()
    traded = df["traded"] > 0
    px = np.where(traded & (df["close"] > 0), df["close"], df["settle"])
    prices = dict(zip(zip(df["expiry"], df["kind"], df["strike"], df["date"]), px))
    by_day = {d: g for d, g in df.groupby("date")}
    dates = pd.DatetimeIndex(sorted(by_day))
    exps = F.monthly_expiries(df)
    pairs = {"monthly": list(zip(exps[:-1], exps[1:])), "weekly": F.weekly_pairs(df)}
    f3 = {}
    for name, cad, legs, trend, tp in f3_variants():
        tr = f3_run(by_day, spot, sma, prices, dates, pairs[cad], legs, trend, tp)
        f3[name] = tr
        per = tr.set_index("open")["pnl"] if len(tr) else pd.Series(dtype=float)
        ins = per.loc[IN_A:IN_END]
        a, b = ins.loc[:IN_B - pd.Timedelta(days=1)], ins.loc[IN_B:]
        sd = ins.std()
        rows.append({"family": "F3", "variant": name, "n": len(ins), "mean_rs": ins.mean(),
                     "t": ins.mean() / sd * math.sqrt(len(ins)) if len(ins) > 5 and sd else np.nan,
                     "mean_A_rs": a.mean() if len(a) else np.nan, "mean_B_rs": b.mean() if len(b) else np.nan,
                     "win_pct": (ins > 0).mean() * 100 if len(ins) else np.nan})
    res = pd.DataFrame(rows)
    print(f"\n{len(res)} variants scored (F1 {sum(res.family == 'F1')}, F2 {sum(res.family == 'F2')}, "
          f"F3 {sum(res.family == 'F3')})")
    assert len(res) == 256, len(res)
    res.to_csv(ROOT / "research" / "search21_insample.csv", index=False)

    out = []
    print("\nSELECTION on 2012-2019 (t >= 3.5 and profitable in 2012-15 and 2016-19)")
    for fam in ("F1", "F2", "F3"):
        g = res[res.family == fam].copy()
        ma, mb = ("mean_A_pct", "mean_B_pct") if fam != "F3" else ("mean_A_rs", "mean_B_rs")
        q = g[(g["t"] >= T_SELECT) & (g[ma] > 0) & (g[mb] > 0)]
        best = g.sort_values("t", ascending=False).head(5)
        print(f"  {fam}: {len(q)} of {len(g)} qualify. Highest t: " + "; ".join(
            f"{r.variant} (t {r.t:.2f})" for r in best.itertuples()))
        if q.empty:
            out.append({"family": fam, "choice": None, "passes": False, "note": "no variant qualified on 2012-2019"})
            continue
        ch = q.sort_values("t", ascending=False).iloc[0]
        print(f"  -> {fam} choice: {ch.variant}")
        out.append(test_choice(fam, ch.variant, series, f3, p, mkt))
    print("\nVERDICTS")
    for o in out:
        print(f"  {o['family']}: {o.get('choice') or 'no choice'} -> {'PASS' if o['passes'] else 'FAIL'}"
              + (f" ({o['note']})" if o.get("note") else ""))
    pd.DataFrame(out).to_csv(ROOT / "research" / "search21_results.csv", index=False)
    print("\nWritten research/search21_insample.csv and research/search21_results.csv")
    return 0


def test_choice(fam, variant, series, f3, p, mkt) -> dict:
    """The one out-of-sample test of a family's choice (2020-2026)."""
    import allocation20 as AL
    from backtest.metrics import cagr, max_drawdown
    liq = AL.load_sleeves(HIST)["LIQ"]
    if fam == "F3":
        import fo20 as F
        tr = f3[variant]
        x = tr[(tr["open"] >= OOS) & (tr["expiry"] <= OOS_END)]
        sd = x["pnl"].std()
        t = x["pnl"].mean() / sd * math.sqrt(len(x)) if len(x) > 5 and sd else float("nan")
        acc = F.account_curve(x, liq, OOS, OOS_END)
        bench = cagr((1 + liq.loc[OOS:OOS_END].fillna(0)).cumprod()) + 2
        ok = x["pnl"].mean() > 0 and t >= T_TEST and cagr(acc) >= bench and max_drawdown(acc) >= -25
        note = (f"2020-26: {len(x)} cycles, avg ₹{x['pnl'].mean():+,.0f}, t {t:.2f}, account {cagr(acc):.1f}%/yr vs "
                f"{bench:.1f}% needed, worst fall {max_drawdown(acc):.1f}%")
        return {"family": fam, "choice": variant, "passes": bool(ok), "note": note}
    per, h = series[(fam, variant)]
    x = per.loc[OOS:OOS_END].dropna()
    t = nw_t(x, max(0, h - 1))
    # the account: each day 1/max(h,1) of the money goes into that day's picks for h sessions (overlapping tranches)
    daily = (per.loc[OOS:OOS_END].fillna(0.0) / max(h, 1))
    acc = (1 + daily).cumprod() * ACCOUNT
    tri = pd.read_csv(HIST / "tri.csv")
    tri["index"] = tri["index"].str.upper()
    n50 = tri[tri["index"] == "NIFTY 50"].assign(date=lambda d: pd.to_datetime(d["date"])).drop_duplicates(
        "date").set_index("date")["tri"].sort_index().loc[OOS:OOS_END]
    ok = x.mean() > 0 and t >= T_TEST and cagr(acc) > cagr(n50) and max_drawdown(acc) >= -25
    note = (f"2020-26: {len(x)} trading days, avg {x.mean() * 100:+.3f}% a trade, t {t:.2f}, account "
            f"{cagr(acc):.1f}%/yr vs Nifty 50 {cagr(n50):.1f}%, worst fall {max_drawdown(acc):.1f}%")
    return {"family": fam, "choice": variant, "passes": bool(ok), "note": note}


if __name__ == "__main__":
    sys.exit(main())
