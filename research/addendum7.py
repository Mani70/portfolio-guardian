"""Addendum 7 (research/PREREGISTRATION.md): fundamentals, flows, options and events - do they improve the live
strategies?  Data: research/download_nse.py (research/data/nse/).

  python research/addendum7.py            # all sections; writes research/addendum7_results.csv
Each rule uses only data published by the evening of the decision day (rolling windows, no full-sample cut-offs).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import backtest.rotation as rot                                     # noqa: E402
from backtest.engine import Costs                                   # noqa: E402
from backtest.etf import ETF_COSTS                                  # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe             # noqa: E402
from backtest.rotation import market_ok, simulate_rotation          # noqa: E402
from swing_research import CASH, ETFS, load                         # noqa: E402

NSE = ROOT / "research" / "data" / "nse"
MOM_CAP, ETF_CAP = 345_000, 75_600
ETF_ASSETS = ["NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES"]
INDIAN_EQ = {"NIFTYBEES", "JUNIORBEES"}
ROWS: List[dict] = []

_scores = rot.momentum_scores


@lru_cache(maxsize=None)
def _cached_scores(d):
    return _scores(STOCKS, d)


def scores(d) -> pd.Series:
    return _cached_scores(pd.Timestamp(d))


# ---------------------------------------------------------------- data
def pct_rank_prev(s: pd.Series, n: int = 250) -> pd.Series:
    """Share of the previous n values below today's value (0..1); NaN until n values exist."""
    v = s.values
    out = np.full(len(v), np.nan)
    for i in range(n, len(v)):
        w = v[i - n:i]
        w = w[~np.isnan(w)]
        if len(w) >= n * 0.8 and not np.isnan(v[i]):
            out[i] = (w < v[i]).mean()
    return pd.Series(out, index=s.index)


def load_indices() -> pd.DataFrame:
    ind = pd.read_csv(NSE / "indices.csv", parse_dates=["date"])
    k = ind["index"].str.lower().str.replace(r"[\s\-]", "", regex=True)
    chains = {"nifty50": ["cnxnifty", "nifty50"], "quality30": ["nsequality30", "niftyquality30", "nifty100quality30"],
              # not chained to "CNX Low Volatility": a different index (level -9% at the join, Nifty +5%)
              "lowvol30": ["nifty100lowvolatility30"], "alphalowvol30": ["niftyalphalowvolatility30"],
              "value20": ["nifty50value20"], "momentum30": ["nifty200momentum30"]}
    out = {}
    for name, keys in chains.items():
        df = ind[k.isin(keys)].sort_values("date").drop_duplicates("date", keep="last").set_index("date")
        out[name] = df[["open", "high", "low", "close", "pe"]]
    return out


def index_frame(df: pd.DataFrame) -> pd.DataFrame:
    f = df[["open", "high", "low", "close"]].copy()
    f["open"] = f["open"].where(f["open"] > 0, f["close"])
    return f


# ---------------------------------------------------------------- simulation helpers
def stats(eq: pd.Series, mid) -> dict:
    a, b = eq.loc[:mid], eq.loc[mid:]
    return dict(cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq), sh1=sharpe(a), sh2=sharpe(b))


def momentum(dates, switch: Callable, filt: Optional[Callable] = None, adjust: Optional[Callable] = None,
             every: str = "monthly"):
    def choose(d, current):
        if not switch(d):
            return []
        ranked = list(scores(d).index)
        if adjust:
            ranked = adjust(d, ranked)
        keep = [s for s in current if s in ranked[:20]]
        cand = [s for s in ranked if s not in keep and not (filt and filt(d, s))]
        return keep + cand[:max(0, 10 - len(keep))]
    return simulate_rotation(STOCKS, choose, dates, MOM_CAP, 10, Costs(dp_per_sell=21.83), CASH, every).equity


def etf_trend(frames, dates, gate: Optional[Callable] = None, mode: str = "add", assets=ETF_ASSETS):
    """Monthly 200-day trend per asset. gate(d) applies to Indian equity ETFs: 'add' = also needs the gate,
    'replace' = the gate instead of the asset's own 200-day rule, 'or' = held if either says so."""
    def choose(d, current):
        out = []
        for a in assets:
            c = frames[a]["close"].loc[:d]
            if len(c) < 200:
                continue
            trend = c.iloc[-1] > c.iloc[-200:].mean()
            if gate is not None and a in INDIAN_EQ:
                trend = gate(d) if mode == "replace" else (trend or gate(d)) if mode == "or" else (trend and gate(d))
            if trend:
                out.append(a)
        return out
    fr = {a: frames[a] for a in assets}
    return simulate_rotation(fr, choose, dates, ETF_CAP, len(assets), ETF_COSTS, CASH).equity


def record(section, rule, book, eq, mid, base=None):
    """'PASS' here = Sharpe better in both halves and worst fall not deeper only. The full pre-registered bars also
    need the neighbours (A, C), the cross-sectional spread t >= 2 (B): see research/FINDINGS.md Addendum 7."""
    r = dict(section=section, rule=rule, book=book, **stats(eq, mid))
    if base is not None:
        r["better_both_halves"] = bool(r["sh1"] > base["sh1"] and r["sh2"] > base["sh2"])
        r["fall_not_deeper"] = bool(r["maxdd"] >= base["maxdd"] - 1e-9)
        r["passes"] = r["better_both_halves"] and r["fall_not_deeper"]
    ROWS.append(r)
    print(f"  {section:<4} {book:<10} {rule:<52} CAGR {r['cagr']:6.2f}%  fall {r['maxdd']:6.1f}%  "
          f"Sharpe {r['sharpe']:.2f} ({r['sh1']:.2f}/{r['sh2']:.2f})"
          + (f"  {'PASS' if r['passes'] else '-'}" if base is not None else ""), flush=True)
    return r


def newey_west_t(x: np.ndarray, lags: int) -> float:
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    m = x.mean()
    e = x - m
    s = (e @ e) / n
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return m / math.sqrt(s / n) if s > 0 else float("nan")


def state_from_signals(buy: pd.Series, sell: pd.Series) -> pd.Series:
    """On after a buy signal, off after a sell signal, on before the first signal."""
    st, out = True, []
    for b, s in zip(buy.fillna(False), sell.fillna(False)):
        if b:
            st = True
        elif s:
            st = False
        out.append(st)
    return pd.Series(out, index=buy.index)


def at(series: pd.Series, d, default=True):
    s = series.loc[:d]
    return bool(s.iloc[-1]) if len(s) else default


# ---------------------------------------------------------------- A. market timing
def section_a(frames, dates, mid, ind):
    print("\nA. Market timing")
    bench = frames["NIFTYBEES"]
    base_sw = lambda d: market_ok(bench, d)                                            # noqa: E731
    base_m = record("A", "baseline: Nifty 200-day switch", "momentum", momentum(dates, base_sw), mid)
    base_e = record("A", "baseline: own 200-day per ETF", "etf_trend", etf_trend(frames, dates), mid)

    # A1 valuation: P/E in the top 20% of the previous 5 years = off
    pe = ind["nifty50"]["pe"].dropna()
    pe = pe[pe > 0]
    rank5 = pct_rank_prev(pe, 1250)
    first = rank5.dropna().index.min()
    d1 = dates[dates >= first]
    mid1 = d1[len(d1) // 2]
    print(f"  A1 needs 5 years of P/E: testable from {first:%d %b %Y} (halves split {mid1:%d %b %Y})")
    b1m = record("A1", "baseline (same period)", "momentum", momentum(d1, base_sw), mid1)
    b1e = record("A1", "baseline (same period)", "etf_trend", etf_trend(frames, d1), mid1)
    for top in (0.20, 0.10, 0.30):
        cheap = rank5 < 1 - top
        sw = lambda d, cheap=cheap: at(cheap, d)                                         # noqa: E731
        both = lambda d, sw=sw: sw(d) and base_sw(d)                                     # noqa: E731
        record("A1", f"P/E not in top {top:.0%} of 5y (alone)", "momentum", momentum(d1, sw), mid1, b1m)
        record("A1", f"P/E not in top {top:.0%} of 5y + 200-day", "momentum", momentum(d1, both), mid1, b1m)
        record("A1", f"P/E not in top {top:.0%} of 5y (alone)", "etf_trend",
               etf_trend(frames, d1, sw, "replace"), mid1, b1e)
        record("A1", f"P/E not in top {top:.0%} of 5y + 200-day", "etf_trend", etf_trend(frames, d1, sw, "add"), mid1, b1e)
    forward_returns("A1", (rank5 < 0.8).where(rank5.notna()).dropna().astype(bool), bench,
                    "P/E not in top 20% (on) vs top 20% (off)")

    # A2 FII index-futures long ratio, contrarian; A3 Nifty options PCR, contrarian
    p = pd.read_csv(NSE / "participant_oi.csv", parse_dates=["date"])
    fii = p[p.who == "FII"].set_index("date").sort_index()
    ratio = (fii["Future Index Long"] / (fii["Future Index Long"] + fii["Future Index Short"])).dropna()
    fo = pd.read_csv(NSE / "fo.csv", parse_dates=["date"], usecols=["date", "symbol", "ce_oi", "pe_oi"])
    nf = fo[fo.symbol == "NIFTY"].set_index("date").sort_index()
    pcr = (nf["pe_oi"] / nf["ce_oi"]).replace([np.inf, -np.inf], np.nan).dropna()
    for code, name, series, buy_low in (("A2", "FII long ratio", ratio, True), ("A3", "PCR", pcr, False)):
        rk = pct_rank_prev(series, 250)
        for lo in (0.20, 0.10, 0.30):
            hi = 1 - lo
            # contrarian: A2 buy when FIIs are most short (low ratio); A3 buy when puts dominate (high PCR)
            buy, sell = ((rk < lo), (rk > hi)) if buy_low else ((rk > hi), (rk < lo))
            state = state_from_signals(buy, sell)
            low_now = buy
            sw = lambda d, state=state: at(state, d)                                     # noqa: E731
            over = lambda d, low_now=low_now: base_sw(d) or at(low_now, d, False)        # noqa: E731
            tag = f"{name} {lo:.0%}/{hi:.0%}"
            record(code, f"{tag}: as the switch", "momentum", momentum(dates, sw), mid, base_m)
            record(code, f"{tag}: override 200-day cash at contrarian buy", "momentum", momentum(dates, over), mid, base_m)
            record(code, f"{tag}: as the switch", "etf_trend", etf_trend(frames, dates, sw, "replace"), mid, base_e)
            record(code, f"{tag}: override at contrarian buy", "etf_trend",
                   etf_trend(frames, dates, lambda d, low_now=low_now: at(low_now, d, False), "or"), mid, base_e)
        forward_returns(code, state_from_signals(*(((rk < .2), (rk > .8)) if buy_low else ((rk > .8), (rk < .2)))),
                        bench, f"{name}: contrarian state on vs off")


def forward_returns(code, state: pd.Series, bench, label):
    c = bench["close"]
    me = c.groupby(c.index.to_period("M")).tail(1)
    rows = []
    for h in (1, 3, 6):
        fwd = me.shift(-h) / me - 1
        st = state.reindex(me.index, method="ffill")
        st = st[me.index >= state.index.min()] if len(state) else st              # no signal yet: not counted
        fwd = fwd.reindex(st.index)
        on, off = fwd[st == True].dropna(), fwd[st == False].dropna()                   # noqa: E712
        diff = (st.astype(float) - st.astype(float).mean()) * fwd                       # regression-style spread
        t = newey_west_t(diff.dropna().values, max(0, h - 1)) if len(off) > 5 else float("nan")
        rows.append(f"{h}m: on {on.mean() * 100:+.2f}% (n={len(on)}), off {off.mean() * 100:+.2f}% (n={len(off)}), t {t:.2f}")
        ROWS.append(dict(section=code, rule=f"forward {h}m: {label}", book="nifty", on=on.mean() * 100,
                         off=off.mean() * 100, n_on=len(on), n_off=len(off), t=t))
    print(f"  {code} forward Nifty returns, {label}: " + "; ".join(rows))


# ---------------------------------------------------------------- B. momentum stock filters
def section_b(frames, dates, mid):
    print("\nB. Filters on momentum picks")
    bench = frames["NIFTYBEES"]
    sw = lambda d: market_ok(bench, d)                                                   # noqa: E731
    base = record("B", "baseline", "momentum", momentum(dates, sw), mid)
    closes = pd.DataFrame({s: f["close"] for s, f in STOCKS.items()})

    fo = pd.read_csv(NSE / "fo.csv", parse_dates=["date"], usecols=["date", "symbol", "is_index", "fut_oi"])
    fo = fo[fo.is_index == False]                                                         # noqa: E712
    oi = fo.pivot_table(index="date", columns="symbol", values="fut_oi", aggfunc="last").sort_index()
    oi_chg = oi / oi.shift(20) - 1
    px_chg = closes / closes.shift(20) - 1

    dl = pd.read_csv(NSE / "delivery.csv", parse_dates=["date"])
    dp = dl.pivot_table(index="date", columns="symbol", values="deliv_pct", aggfunc="last").sort_index()
    dtrend = dp.rolling(20, min_periods=15).mean() - dp.rolling(120, min_periods=90).mean()

    ins = insider_events()

    def row_at(df, d):
        s = df.loc[:d]
        return s.iloc[-1] if len(s) else pd.Series(dtype=float)

    filters = {}
    for cut in (0.10, 0.05, 0.15):
        def f(d, s, cut=cut):
            o, p_ = row_at(oi_chg, d).get(s, np.nan), row_at(px_chg, d).get(s, np.nan)
            return bool(p_ > 0 and o < -cut)
        filters[f"B1 price up, futures OI down >{cut:.0%}"] = f
    for frac in (1 / 3, 1 / 4, 1 / 2):
        def f(d, s, frac=frac):
            r = row_at(dtrend, d).reindex(list(STOCKS)).dropna()
            if s not in r.index or len(r) < 10:
                return False
            return bool(r.rank(pct=True)[s] <= frac)
        filters[f"B2 delivery % falling (bottom {frac:.0%})"] = f
    for name, filt in filters.items():
        record("B", name, "momentum", momentum(dates, sw, filt=filt), mid, base)
        spread("B", name, dates, filt, closes, sw)

    for bonus in (5, 3, 8):
        def adj(d, ranked, bonus=bonus):
            buyers, sellers = ins.at(d)
            pos = {s: i for i, s in enumerate(ranked)}
            for s in ranked:
                if s in buyers:
                    pos[s] -= bonus + 0.5
                if s in sellers:
                    pos[s] += bonus + 0.5
            return sorted(ranked, key=lambda s: pos[s])
        record("B", f"B3 insider: promoter buys +{bonus} ranks, big sells -{bonus}", "momentum",
               momentum(dates, sw, adjust=adj), mid, base)
    spread("B", "B3 promoter buying in 60 days (top-20 names)", dates, lambda d, s: s in ins.at(d)[0], closes, sw,
           top=20, flagged_good=True)


def spread(code, name, dates, filt, closes, sw, top=10, flagged_good=False):
    """Each month-end: the top-ranked names, flagged by the filter vs not; next month's return difference."""
    review = sorted(rot._rebalance_days(dates))
    diffs, months, nflag = [], [], 0
    for i, d in enumerate(review[:-1]):
        if not sw(d):
            continue
        names = list(scores(d).index)[:top]
        flag = [s for s in names if filt(d, s)]
        keep = [s for s in names if s not in flag]
        if not flag or not keep:
            continue
        nxt = review[i + 1]
        r = (closes.loc[nxt] / closes.loc[d] - 1)
        good, bad = (flag, keep) if flagged_good else (keep, flag)
        diffs.append(r[good].mean() - r[bad].mean())
        months.append(d)
        nflag += len(flag)
    x = np.array(diffs)
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 else float("nan")
    print(f"  {code} spread {name}: {len(x)} months, {nflag} flagged picks, "
          f"{'flagged minus kept' if flagged_good else 'kept minus filtered-out'} next month {x.mean() * 100 if len(x) else float('nan'):+.2f}%, t {t:.2f}")
    ROWS.append(dict(section=code, rule=f"spread: {name}", book="cross-section", months=len(x), flagged=nflag,
                     mean_pct=x.mean() * 100 if len(x) else np.nan, t=t))


class Insider:
    def __init__(self, buys: pd.DataFrame, sells: pd.DataFrame):
        self.buys, self.sells = buys, sells

    def at(self, d, days: int = 60):
        d = pd.Timestamp(d)
        lo = d - pd.Timedelta(days=days)
        b = set(self.buys[(self.buys.date > lo) & (self.buys.date <= d)].symbol)
        s = self.sells[(self.sells.date > lo) & (self.sells.date <= d)]
        s = set(s.groupby("symbol").pct.sum().loc[lambda x: x > 0.5].index)
        return b, s


def insider_events() -> Insider:
    rows = [json.loads(l) for l in open(NSE / "insider.jsonl", encoding="utf-8")]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"].str[:11], format="%d-%b-%Y", errors="coerce").dt.normalize()
    cat = df["personCategory"].fillna("").str.lower()
    mode = df["acqMode"].fillna("").str.lower()
    typ = df["tdpTransactionType"].fillna("").str.lower()
    insider = cat.str.contains("promoter") | cat.str.contains("director")
    buys = df[insider & mode.str.contains("market purchase") & (typ == "buy")][["symbol", "date"]].dropna()
    promoter = cat.str.contains("promoter")
    sells = df[promoter & mode.str.contains("market sale") & (typ == "sell")].copy()
    sells["pct"] = (pd.to_numeric(sells["befAcqSharesPer"], errors="coerce")
                    - pd.to_numeric(sells["afterAcqSharesPer"], errors="coerce")).clip(lower=0)
    sells = sells[["symbol", "date", "pct"]].dropna()
    return Insider(buys.drop_duplicates(), sells)


# ---------------------------------------------------------------- C. factor indices
def section_c(frames, dates, mid, ind):
    print("\nC. Factor indices (price indices: no dividends; value indices understated by ~1-2%/yr)")
    fac = {name: index_frame(ind[name]) for name in ("quality30", "lowvol30", "alphalowvol30", "value20")}
    allf = dict(frames, **fac)
    start = max(f.index.min() for f in fac.values() if f is not None)
    print("  first dates: " + ", ".join(f"{k} {v.index.min():%b %Y}" for k, v in fac.items())
          + f"; momentum30 only from {ind['momentum30'].index.min():%b %Y} (left out)")
    dc = dates[dates >= pd.Timestamp("2017-01-01")]
    midc = dc[len(dc) // 2]
    base = record("C1", "live ETF basket (same period)", "etf_trend", etf_trend(allf, dc), midc)
    for label, assets in (("factor basket: quality, low-vol, alpha low-vol, value", list(fac)),
                          ("live basket with quality + low-vol in place of Nifty/Next 50",
                           ["quality30", "lowvol30", "MON100", "GOLDBEES"])):
        record("C1", label, "etf_trend", etf_trend(allf, dc, assets=assets), midc, base)
    bench = frames["NIFTYBEES"]
    q = fac["quality30"]["close"]
    base_m = record("C2", "baseline momentum", "momentum", momentum(dates, lambda d: market_ok(bench, d)), mid)
    for n in (200, 150, 250):
        qok = lambda d, n=n: (lambda c: len(c) >= n and c.iloc[-1] > c.iloc[-n:].mean())(q.loc[:d])   # noqa: E731
        record("C2", f"Quality 30 above its {n}-day average (instead)", "momentum", momentum(dates, qok), mid, base_m)
        record("C2", f"Quality 30 {n}-day AND Nifty 200-day", "momentum",
               momentum(dates, lambda d, qok=qok: qok(d) and market_ok(bench, d)), mid, base_m)


# ---------------------------------------------------------------- D. events
def section_d(frames, dates):
    print("\nD. Events (the 49 stocks with price history; survivorship bias: today's Nifty 50)")
    global BENCH_OPEN
    bench = frames["NIFTYBEES"]["close"]
    BENCH_OPEN = frames["NIFTYBEES"]["open"]
    mid = dates[len(dates) // 2]
    bm = [json.loads(l) for l in open(NSE / "board_meetings.jsonl", encoding="utf-8")]
    bm = pd.DataFrame(bm)
    txt = (bm["bm_purpose"].fillna("") + " " + bm["bm_desc"].fillna("")).str.lower()
    res = bm[txt.str.contains("financial result")].copy()
    res["date"] = pd.to_datetime(res["bm_date"], format="%d-%b-%Y", errors="coerce")
    res = res[["bm_symbol", "date"]].dropna().drop_duplicates()
    res = res[res.bm_symbol.isin(STOCKS)]
    ev = []
    for _, r in res.iterrows():
        c = STOCKS[r.bm_symbol]["close"]
        idx = c.index
        i = idx.searchsorted(r.date)                      # results day (or the next session)
        if i < 1 or i + 61 >= len(idx):
            continue
        t = idx[i]
        if t < dates[0] or t > dates[-1]:
            continue
        b = bench.reindex(idx).ffill()
        react = c.iloc[i + 1] / c.iloc[i - 1] - 1 - (b.iloc[i + 1] / b.iloc[i - 1] - 1)
        f20 = c.iloc[i + 21] / c.iloc[i + 1] - 1 - (b.iloc[i + 21] / b.iloc[i + 1] - 1)
        f60 = c.iloc[i + 61] / c.iloc[i + 1] - 1 - (b.iloc[i + 61] / b.iloc[i + 1] - 1)
        ev.append(dict(symbol=r.bm_symbol, date=t, react=react, f20=f20, f60=f60))
    ev = pd.DataFrame(ev).drop_duplicates(["symbol", "date"])
    print(f"  D1 results days found: {len(ev)}")
    for thr in (0.05, 0.04, 0.06):
        for side, sel in (("up", ev.react > thr), ("down", ev.react < -thr)):
            event_stats("D1", f"results-day reaction {'+' if side == 'up' else '-'}{thr:.0%}", ev[sel], mid)
    ins = insider_events()
    pb = ins.buys[ins.buys.symbol.isin(STOCKS)].drop_duplicates()
    ev2 = []
    for _, r in pb.iterrows():
        f = STOCKS[r.symbol]
        idx = f.index
        i = idx.searchsorted(r.date, side="right")        # first session after the disclosure day
        if i + 60 >= len(idx) or idx[i] < dates[0] or idx[i] > dates[-1]:
            continue
        b = bench.reindex(idx).ffill()
        bo = BENCH_OPEN.reindex(idx).ffill().iloc[i]
        o = f["open"].iloc[i]                             # both legs from the same open
        f20 = f["close"].iloc[i + 19] / o - 1 - (b.iloc[i + 19] / bo - 1)
        f60 = f["close"].iloc[i + 59] / o - 1 - (b.iloc[i + 59] / bo - 1)
        ev2.append(dict(symbol=r.symbol, date=idx[i], f20=f20, f60=f60))
    ev2 = pd.DataFrame(ev2).drop_duplicates(["symbol", "date"])
    event_stats("D2", "promoter/director open-market buying", ev2, mid)
    ROWS.append(dict(section="D3", rule="bulk deals", book="events", note="not tested: NSE refused the bulk-deal API"))
    print("  D3 bulk deals: not tested (no data: NSE refused that API)")


def event_stats(code, name, ev: pd.DataFrame, mid):
    out = dict(section=code, rule=name, book="events", n=len(ev))
    parts = []
    for h in ("f20", "f60"):
        if ev.empty:
            continue
        x = ev[h]
        a, b = ev[ev.date <= mid][h], ev[ev.date > mid][h]
        by_m = ev.groupby(ev.date.dt.to_period("M"))[h].mean()
        t = by_m.mean() / (by_m.std(ddof=1) / math.sqrt(len(by_m))) if len(by_m) > 2 else float("nan")
        ok = (len(ev) >= 100 and abs(a.mean()) > 0.003 and abs(b.mean()) > 0.003
              and np.sign(a.mean()) == np.sign(b.mean()) and abs(t) >= 2)
        out.update({f"{h}_mean": x.mean() * 100, f"{h}_h1": a.mean() * 100, f"{h}_h2": b.mean() * 100,
                    f"{h}_t": t, f"{h}_pass": bool(ok)})
        parts.append(f"{h[1:]}d {x.mean() * 100:+.2f}% (halves {a.mean() * 100:+.2f}/{b.mean() * 100:+.2f}, "
                     f"t {t:.2f}){' PASS' if ok else ''}")
    ROWS.append(out)
    print(f"  {code} {name}: n={len(ev)}; " + "; ".join(parts))


# ---------------------------------------------------------------- R. robustness of the near-passes
def section_r(frames, dates, mid, ind):
    """EXPLORATORY (decided after seeing A-D): Addendum 8 showed that the review day alone moves momentum by up to
    8 points a year. A rule that changes WHEN the strategy is in or out, or WHICH names it holds, also changes that
    luck. So each near-pass is re-run on 13 different review days against the baseline on the same day; a real
    improvement should win on most days, luck on about half."""
    import rebalance_day                                                    # noqa: F401 - adds start:k / end:k days
    print("\nR. Near-passes re-run on 13 review days (exploratory)")
    bench = frames["NIFTYBEES"]
    base_sw = lambda d: market_ok(bench, d)                                  # noqa: E731
    closes = pd.DataFrame({s: f["close"] for s, f in STOCKS.items()})
    fo = pd.read_csv(NSE / "fo.csv", parse_dates=["date"], usecols=["date", "symbol", "is_index", "fut_oi"])
    oi = fo[fo.is_index == False].pivot_table(index="date", columns="symbol", values="fut_oi", aggfunc="last")   # noqa: E712
    oi_chg, px_chg = oi / oi.shift(20) - 1, closes / closes.shift(20) - 1
    dl = pd.read_csv(NSE / "delivery.csv", parse_dates=["date"])
    dp = dl.pivot_table(index="date", columns="symbol", values="deliv_pct", aggfunc="last").sort_index()
    dtrend = dp.rolling(20, min_periods=15).mean() - dp.rolling(120, min_periods=90).mean()
    q = index_frame(ind["quality30"])["close"]
    nf = pd.read_csv(NSE / "fo.csv", parse_dates=["date"], usecols=["date", "symbol", "ce_oi", "pe_oi"])
    nf = nf[nf.symbol == "NIFTY"].set_index("date").sort_index()
    prk = pct_rank_prev((nf["pe_oi"] / nf["ce_oi"]).replace([np.inf, -np.inf], np.nan).dropna(), 250)
    ins = insider_events()

    def last(df, d):
        x = df.loc[:d]
        return x.iloc[-1] if len(x) else pd.Series(dtype=float)

    def b1(d, s):
        return bool(last(px_chg, d).get(s, np.nan) > 0 and last(oi_chg, d).get(s, np.nan) < -0.10)

    def b2(d, s):
        r = last(dtrend, d).reindex(list(STOCKS)).dropna()
        return bool(s in r.index and len(r) >= 10 and r.rank(pct=True)[s] <= 0.5)

    def b3(d, ranked):
        buyers, sellers = ins.at(d)
        pos = {s: i + (-8.5 if s in buyers else 0) + (8.5 if s in sellers else 0) for i, s in enumerate(ranked)}
        return sorted(ranked, key=lambda s: pos[s])

    def qma(n):
        return lambda d: (lambda c: len(c) >= n and c.iloc[-1] > c.iloc[-n:].mean())(q.loc[:d])

    q200, q150 = qma(200), qma(150)
    variants = {
        "B1 skip price up + futures OI down >10%": dict(filt=b1),
        "B2 skip delivery % falling (bottom 50%)": dict(filt=b2),
        "B3 insider +/-8 ranks": dict(adjust=b3),
        "C2 Quality 30 200-day AND Nifty 200-day": dict(switch=lambda d: q200(d) and base_sw(d)),
        "C2 Quality 30 150-day AND Nifty 200-day": dict(switch=lambda d: q150(d) and base_sw(d)),
        "A3 PCR 20/80 override of the 200-day cash": dict(
            switch=lambda d: base_sw(d) or at(prk > 0.8, d, False)),
    }
    days = ["end:1", "end:2", "end:3"] + [f"start:{k}" for k in (1, 2, 3, 5, 7, 9, 11, 13, 15, 17)]
    base = {ev: stats(momentum(dates, base_sw, every=ev), mid) for ev in days}
    for name, kw in variants.items():
        sw = kw.get("switch", base_sw)
        wins_both = wins_sh = 0
        dcagr = []
        for ev in days:
            r = stats(momentum(dates, sw, kw.get("filt"), kw.get("adjust"), every=ev), mid)
            b = base[ev]
            wins_sh += r["sharpe"] > b["sharpe"]
            wins_both += r["sh1"] > b["sh1"] and r["sh2"] > b["sh2"]
            dcagr.append(r["cagr"] - b["cagr"])
        d = np.array(dcagr)
        t = d.mean() / (d.std(ddof=1) / math.sqrt(len(d)))
        print(f"  {name:<46} better Sharpe on {wins_sh}/{len(days)} days, both halves on {wins_both}/{len(days)}; "
              f"CAGR change {d.mean():+.2f} pt (range {d.min():+.1f} to {d.max():+.1f})", flush=True)
        ROWS.append(dict(section="R", rule=name, book="momentum", days=len(days), sharpe_wins=int(wins_sh),
                         both_halves_wins=int(wins_both), cagr_change=d.mean(), cagr_min=d.min(), cagr_max=d.max(),
                         t_days=t))


# ---------------------------------------------------------------- main
def main():
    global STOCKS
    frames = load()
    STOCKS = {s: f for s, f in frames.items() if s not in ETFS}
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in STOCKS.values()])))
    dates = full[full >= pd.Timestamp("2016-01-01")]
    mid = dates[len(dates) // 2]
    print(f"Test {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}")
    ind = load_indices()
    only = set(sys.argv[1:])
    if not only or "A" in only:
        section_a(frames, dates, mid, ind)
    if not only or "B" in only:
        section_b(frames, dates, mid)
    if not only or "C" in only:
        section_c(frames, dates, mid, ind)
    if not only or "D" in only:
        section_d(frames, dates)
    if "R" in only:
        section_r(frames, dates, mid, ind)
    out = pd.DataFrame(ROWS)
    name = "addendum7_results.csv" if not only else f"addendum7_results_{''.join(sorted(only))}.csv"
    out.to_csv(ROOT / "research" / name, index=False)
    print(f"\nWritten research/{name}")


STOCKS: Dict[str, pd.DataFrame] = {}
BENCH_OPEN = pd.Series(dtype=float)

if __name__ == "__main__":
    main()
