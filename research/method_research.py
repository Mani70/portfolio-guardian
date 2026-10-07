"""The "5-section analyst report" method (technical setup + trade plan), tested as rules on 12 years of data.
Rules and pass criteria: research/PREREGISTRATION.md, Addendum 2 (written before this ran).

  python research/method_research.py
Writes research/method_results.csv and research/method_trades.csv.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from backtest.engine import Costs                                     # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe               # noqa: E402
from swing_research import ETFS, load                                 # noqa: E402

CAP, CASH_PCT, FD = 150_000, 6.0, 6.2
START = pd.Timestamp("2016-01-01")
SLIP = 0.0005                     # 0.05% per side, as backtest.engine
COSTS = Costs()


# ---------------------------------------------------------------- indicators (data up to t's close)
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def indicators(df: pd.DataFrame, market_up: pd.Series) -> pd.DataFrame:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    x = pd.DataFrame(index=df.index)
    x["close"], x["high"], x["low"], x["open"] = c, h, l, df["open"]
    x["ema50"], x["ema200"] = ema(c, 50), ema(c, 200)
    wk = c.resample("W-FRI").last().dropna()
    e20 = ema(wk, 20)
    wup = (wk > e20) & (e20 > e20.shift(4))
    x["weekly_up"] = wup.reindex(df.index, method="ffill").fillna(False).astype(bool)
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    x["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    macd = ema(c, 12) - ema(c, 26)
    x["macd"], x["macd_sig"] = macd, ema(macd, 9)
    x["hist"] = macd - x["macd_sig"]
    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    x["bb_mid"], x["bb_lo"] = mid, mid - 2 * sd
    bw = 4 * sd / mid
    x["squeeze_recent"] = (bw <= bw.rolling(126).quantile(0.25)).astype(float).rolling(10).max().shift(1) > 0
    x["vol_ratio"] = v / v.rolling(20).mean().shift(1)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    x["res60"] = h.rolling(60).max().shift(1)
    lo60 = l.rolling(60).min().shift(1)
    rng = x["res60"] - lo60
    x["fib_top"], x["fib_bot"] = x["res60"] - 0.382 * rng, x["res60"] - 0.618 * rng
    x["market_up"] = market_up.reindex(df.index, method="ffill").fillna(False).astype(bool)
    x["uptrend"] = (c > x["ema200"]) & (x["ema50"] > x["ema200"]) & x["weekly_up"]
    return x


def setups(x: pd.DataFrame) -> Dict[str, pd.Series]:
    c = x["close"]
    near_ema50 = (x["low"] <= x["ema50"] * 1.01).astype(float).rolling(3).max() > 0
    in_fib = ((x["low"] <= x["fib_top"]) & (x["low"] >= x["fib_bot"])).astype(float).rolling(3).max() > 0
    a = (x["uptrend"] & (near_ema50 | in_fib) & (x["rsi"].rolling(5).min() < 45)
         & (c > x["high"].shift(1)) & (x["hist"] > x["hist"].shift(1)))
    b = (x["uptrend"] & (c > x["res60"]) & (x["vol_ratio"] >= 1.5) & x["rsi"].between(55, 80) & x["squeeze_recent"])
    cc = (c < x["bb_lo"]) & (c > x["ema200"]) & x["weekly_up"] & (x["rsi"] < 35)
    cross = (x["macd"] > x["macd_sig"]) & (x["macd"].shift(1) <= x["macd_sig"].shift(1))
    dd = cross & (c > x["ema50"]) & (x["ema50"] > x["ema200"]) & x["weekly_up"] & (x["vol_ratio"] >= 1.2)
    score = ((c > x["ema50"]).astype(int) + (x["ema50"] > x["ema200"]).astype(int) + x["weekly_up"].astype(int)
             + x["rsi"].between(50, 70).astype(int) + (x["macd"] > x["macd_sig"]).astype(int)
             + (c > x["bb_mid"]).astype(int) + (x["vol_ratio"] > 1).astype(int) + x["market_up"].astype(int))
    e = (score >= 7) & (score.shift(1) < 7)
    return {"A pullback": a, "B breakout": b, "C bollinger": cc, "D macd": dd, "E checklist": e}


def results_season(d: pd.Timestamp) -> bool:
    return d.month in (1, 4, 7, 10) and d.day >= 15 or d.month in (2, 5, 8, 11) and d.day <= 15


# ---------------------------------------------------------------- trade plan
@dataclass
class Plan:
    sym: str
    signal_day: pd.Timestamp
    stop: float
    t1: float
    t2: float
    rr: float


def make_plan(sym, x: pd.DataFrame, i: int, stop_lb: int, rr_min: float) -> Plan | None:
    row = x.iloc[i]
    stop = float(x["low"].iloc[max(0, i - stop_lb + 1):i + 1].min() - 0.25 * row["atr"])
    c = float(row["close"])
    risk = c - stop
    if not np.isfinite(risk) or risk <= 0 or risk / c > 0.08:
        return None
    t1 = float(row["res60"]) if row["res60"] > c else c + 2 * risk
    rr = (t1 - c) / risk
    if rr < rr_min:
        return None
    return Plan(sym, x.index[i], stop, t1, max(c + 3 * risk, t1), rr)


@dataclass
class Pos:
    plan: Plan
    qty: int
    entry: float
    entry_day: pd.Timestamp
    stop: float
    cost: float
    risk_rs: float
    t1_done: bool = False
    bars: int = 0
    proceeds: float = 0.0


def step(p: Pos, o, h, l, c, max_hold):
    """Advance one session. Returns list of (qty, price) sells; position done when qty reaches 0."""
    sells = []
    half = p.qty // 2 if not p.t1_done and p.plan.t2 > p.plan.t1 else p.qty
    half = half or p.qty                                # a 1-share position exits whole at T1

    def sell(q, px):
        nonlocal sells
        sells.append((q, px))
        p.qty -= q

    if o <= p.stop:
        sell(p.qty, o)
        return sells
    if o >= p.plan.t2 or (not p.t1_done and o >= p.plan.t1 and half == p.qty):
        sell(p.qty, o)
        return sells
    if not p.t1_done and o >= p.plan.t1:
        sell(half, o)
        p.t1_done, p.stop = True, p.entry
    elif l <= p.stop:
        sell(p.qty, p.stop)
        return sells
    if not p.t1_done and h >= p.plan.t1:
        sell(half, p.plan.t1)
        p.t1_done, p.stop = True, max(p.stop, p.entry)
    if p.qty and p.t1_done and h >= p.plan.t2:
        sell(p.qty, p.plan.t2)
    p.bars += 1
    if p.qty and p.bars >= max_hold:
        sell(p.qty, c)
    return sells


def sell_value(q, px):
    v = q * px * (1 - SLIP)
    return v - COSTS.sell(v)


# ---------------------------------------------------------------- portfolio simulation
def simulate(X: Dict[str, pd.DataFrame], sig: Dict[str, pd.Series], dates, *, rr_min=2.0, max_hold=15, stop_lb=10,
             market_filter=True, skip_results=False, slots=5, capital=CAP):
    cash, pos, pending, eq, trades = capital, {}, [], [], []
    idx = {s: {d: i for i, d in enumerate(x.index)} for s, x in X.items()}
    for d in dates:
        # 1. fill yesterday's plans at today's open
        for pl in pending:
            x = X[pl.sym]
            if d not in idx[pl.sym] or pl.sym in pos:
                continue
            o = float(x.at[d, "open"])
            if not (pl.stop < o < pl.t1):              # opened beyond the stop or the target: order cancelled
                continue
            equity = cash + sum(p.qty * float(X[s].at[d, "open"]) for s, p in pos.items() if d in idx[s])
            px = o * (1 + SLIP)
            qty = int(min(0.01 * equity / (px - pl.stop), 0.25 * equity / px))
            if qty < 1:
                continue
            cost = qty * px + COSTS.buy(qty * px)
            if cost > cash:
                qty = int((cash - 30) / (px * 1.0012))
                if qty < 1:
                    continue
                cost = qty * px + COSTS.buy(qty * px)
            cash -= cost
            pos[pl.sym] = Pos(pl, qty, px, d, pl.stop, cost, qty * (px - pl.stop))
        pending = []
        # 2. manage open positions on today's bar
        for s in list(pos):
            if d not in idx[s]:
                continue
            p, r = pos[s], X[s].loc[d]
            for q, px in step(p, float(r.open), float(r.high), float(r.low), float(r.close), max_hold):
                v = sell_value(q, px)
                cash += v
                p.proceeds += v
            if p.qty == 0:
                pnl = p.proceeds - p.cost
                trades.append(dict(sym=s, setup="", entry_day=p.entry_day, exit_day=d, pnl=pnl,
                                   ret=pnl / p.cost, r=pnl / p.risk_rs if p.risk_rs > 0 else np.nan, rr=p.plan.rr))
                del pos[s]
        cash *= 1 + CASH_PCT / 100 / 252
        eq.append(cash + sum(p.qty * float(X[s]["close"].asof(d)) for s, p in pos.items()))
        # 3. new plans from today's close
        free = slots - len(pos)
        if free <= 0 or (skip_results and results_season(d)):
            continue
        cands = []
        for s, x in X.items():
            if s in pos or d not in idx[s] or not sig[s].get(d, False):
                continue
            if market_filter and not x.at[d, "market_up"]:
                continue
            pl = make_plan(s, x, idx[s][d], stop_lb, rr_min)
            if pl:
                cands.append(pl)
        pending = sorted(cands, key=lambda p: -p.rr)[:free]
    return pd.Series(eq, index=dates), pd.DataFrame(trades)


def all_signal_trades(X, sig, dates, *, rr_min=2.0, max_hold=15, stop_lb=10, market_filter=True, size=30_000):
    """Every signal traded on its own (no slot limit), ₹30k each: the cleanest test of the setup's edge."""
    out = []
    lo, hi = dates[0], dates[-1]
    for s, x in X.items():
        days = x.index
        busy = -1                                       # one trade per stock at a time
        for i in np.flatnonzero(sig[s].reindex(days).fillna(False).values):
            d = days[i]
            if i <= busy or d < lo or d > hi or i + 1 >= len(days) or (market_filter and not x["market_up"].iloc[i]):
                continue
            pl = make_plan(s, x, i, stop_lb, rr_min)
            if not pl:
                continue
            o = float(x["open"].iloc[i + 1])
            if not (pl.stop < o < pl.t1):
                continue
            px = o * (1 + SLIP)
            qty = max(1, int(size / px))
            cost = qty * px + COSTS.buy(qty * px)
            p = Pos(pl, qty, px, days[i + 1], pl.stop, cost, qty * (px - pl.stop))
            j = i + 1
            while p.qty and j < len(days):
                r = x.iloc[j]
                for q, spx in step(p, float(r.open), float(r.high), float(r.low), float(r.close), max_hold):
                    p.proceeds += sell_value(q, spx)
                j += 1
            busy = j - 1
            if p.qty:                                   # still open at the end of the data
                continue
            pnl = p.proceeds - p.cost
            out.append(dict(sym=s, entry_day=days[i + 1], exit_day=days[j - 1], ret=pnl / p.cost,
                            r=pnl / p.risk_rs, rr=pl.rr))
    return pd.DataFrame(out)


def week_t(t: pd.DataFrame, col="ret") -> float:
    if len(t) < 3:
        return float("nan")
    w = t.groupby(t["entry_day"].dt.to_period("W"))[col].mean()
    return float(w.mean() / w.std() * math.sqrt(len(w))) if len(w) > 2 and w.std() > 0 else float("nan")


def describe(name, eq, tr, at, mid, group):
    a, b = eq.loc[:mid], eq.loc[mid:]
    h1, h2 = at[at.entry_day < mid], at[at.entry_day >= mid]
    return dict(strategy=name, group=group, cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq),
                h1=cagr(a), h2=cagr(b), trades=len(tr), win=(tr.pnl > 0).mean() * 100 if len(tr) else np.nan,
                avg_r=tr.r.mean() if len(tr) else np.nan,
                sig_trades=len(at), sig_avg_pct=at.ret.mean() * 100 if len(at) else np.nan,
                sig_h1_pct=h1.ret.mean() * 100 if len(h1) else np.nan, sig_h2_pct=h2.ret.mean() * 100 if len(h2) else np.nan,
                sig_t_week=week_t(at), sig_avg_r=at.r.mean() if len(at) else np.nan)


def main():
    frames = load()
    bench = frames["NIFTYBEES"]
    mkt = bench["close"] > bench["close"].rolling(200).mean()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    X = {s: indicators(f, mkt) for s, f in stocks.items()}
    S = {s: setups(x) for s, x in X.items()}
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= START]
    mid = dates[len(dates) // 2]
    rows = []

    nb = CAP * bench["close"].reindex(dates).ffill() / bench["close"].reindex(dates).ffill().iloc[0]
    c = pd.DataFrame({s: f["close"] for s, f in stocks.items()}).reindex(dates).ffill()
    ew = CAP * (c / c.bfill().iloc[0]).mean(axis=1)
    for name, e in (("NIFTYBEES hold", nb), ("Equal-weight hold (49 stocks)", ew)):
        rows.append(dict(strategy=name, group="benchmark", cagr=cagr(e), maxdd=max_drawdown(e), sharpe=sharpe(e),
                         h1=cagr(e.loc[:mid]), h2=cagr(e.loc[mid:])))

    curves, all_trades = {}, []
    names = list(next(iter(S.values())))
    for setup in names:
        sig = {s: S[s][setup] for s in X}
        eq, tr = simulate(X, sig, dates)
        at = all_signal_trades(X, sig, dates)
        curves[setup] = eq
        rows.append(describe(setup, eq, tr, at, mid, "setup"))
        at["setup"] = setup
        all_trades.append(at)
        print(f"{setup}: {len(tr)} portfolio trades, {len(at)} signals", flush=True)
        for label, kw in (("rr1.5", dict(rr_min=1.5)), ("rr none", dict(rr_min=0.0)),
                          ("hold10", dict(max_hold=10)), ("hold20", dict(max_hold=20)),
                          ("stop5", dict(stop_lb=5)), ("stop20", dict(stop_lb=20)),
                          ("no market filter", dict(market_filter=False))):
            e2, t2 = simulate(X, sig, dates, **kw)
            a2 = all_signal_trades(X, sig, dates, **kw)
            rows.append(describe(f"{setup} | {label}", e2, t2, a2, mid, "neighbour"))
        e3, t3 = simulate(X, sig, dates, skip_results=True)
        rows.append(describe(f"{setup} | skip results season", e3, t3, at[[not results_season(d) for d in at.entry_day]],
                             mid, "proxy"))

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "research" / "method_results.csv", index=False)
    pd.concat(all_trades).to_csv(ROOT / "research" / "method_trades.csv", index=False)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.max_columns", 30)
    print(f"Test {dates[0]:%d %b %Y} - {dates[-1]:%d %b %Y}; halves split {mid:%d %b %Y}; capital ₹{CAP:,}")
    print(out.round(2).to_string(index=False))

    # does any setup add to the current portfolio? (daily-rebalanced mix of return series)
    from backtest.etf import gtaa
    from backtest.rotation import momentum_rotation
    g = gtaa(frames, dates, CAP).equity
    m = momentum_rotation(stocks, bench, dates, CAP, COSTS, CASH_PCT).equity
    base = pd.concat([g.pct_change(), m.pct_change()], axis=1).mean(axis=1).fillna(0)
    base_eq = CAP * (1 + base).cumprod()
    print(f"\nCurrent 50/50 portfolio: CAGR {cagr(base_eq):.1f}%, worst fall {max_drawdown(base_eq):.1f}%, Sharpe {sharpe(base_eq):.2f}")
    for setup, eq in curves.items():
        r = eq.pct_change().fillna(0)
        mix = CAP * (1 + (2 * base + r) / 3).cumprod()
        print(f"  + 1/3 {setup:<12}: CAGR {cagr(mix):.1f}%, worst fall {max_drawdown(mix):.1f}%, Sharpe {sharpe(mix):.2f}, "
              f"corr {base.corr(r):.2f}")


if __name__ == "__main__":
    main()
