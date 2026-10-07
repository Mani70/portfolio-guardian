"""Three institutional strategy briefs, adapted to what this account can trade (PREREGISTRATION Addendum 6).

  python research/other_strategies.py      -> research/other_strategies.csv
A. CTA trend: 50/200-day golden cross + ADX(14) > 25, shares = 1% of equity / ATR(20)
B. Risk parity: inverse-volatility and equal-risk-contribution on NIFTYBEES / MON100 / GOLDBEES
C. Pairs: Nifty 50 pairs with 2-year log-price correlation > 0.85, spread z-score 2.5 vs 60-day mean
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from backtest.engine import Costs                                       # noqa: E402
from backtest.etf import ETF_COSTS                                      # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe                 # noqa: E402
from backtest.rotation import _rebalance_days                           # noqa: E402
from swing_research import ETFS, load                                   # noqa: E402

CAP, CASH = 420_000, 6.0
CTA_ETFS = ["NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES", "MON100", "CPSEETF", "PSUBNKBEES",
            "ITBEES", "PHARMABEES"]


# ---------------------------------------------------------------- indicators
def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr_w = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=a, adjust=False).mean() / atr_w
    ndi = 100 * ndm.ewm(alpha=a, adjust=False).mean() / atr_w
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def atr(df, n=20):
    c = df["close"]
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(), (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ---------------------------------------------------------------- A. CTA trend
def cta(frames, dates, costs, fill, adx_min=25, window=5, state=False):
    sig, ext, a20, px, cl = {}, {}, {}, {}, {}
    for s, f in frames.items():
        s50, s200 = f["close"].rolling(50).mean(), f["close"].rolling(200).mean()
        up = s50 > s200
        cross = up & ~up.shift(1, fill_value=False)
        recent = cross.astype(float).rolling(window).max().fillna(0) > 0
        a = adx(f)
        sig[s] = ((up if state else (recent & up)) & (a > adx_min)).to_dict()
        ext[s] = (~up).to_dict()
        a20[s] = atr(f).to_dict()
        px[s] = f[fill].to_dict()
        cl[s] = f["close"].to_dict()
        sig[s + "_adx"] = a.to_dict()
    slip = costs.slippage_pct / 100
    cash, hold, last, eq, ntr = CAP, {}, {}, [], 0
    pend_buy, pend_sell, prev = [], [], None
    for d in dates:
        if prev is not None:
            cash *= (1 + CASH / 100) ** ((d - prev).days / 365.25)
        prev = d
        for s in pend_sell:
            p = px[s].get(d)
            if s in hold and p == p and p:
                v = hold.pop(s) * p * (1 - slip)
                cash += v - costs.sell(v)
                ntr += 1
        equity = cash + sum(q * last.get(s, 0) for s, q in hold.items())
        for s, a in pend_buy:
            p = px[s].get(d)
            if s in hold or p is None or p != p or not a or a != a:
                continue
            p *= 1 + slip
            qty = math.floor(min(0.01 * equity / a, (cash - 50) / (p * 1.003)))
            if qty >= 1:
                cash -= qty * p + costs.buy(qty * p)
                hold[s] = qty
        pend_buy, pend_sell = [], []
        for s in frames:
            c = cl[s].get(d)
            if c is not None and c == c:
                last[s] = c
        for s in frames:
            if s in hold and ext[s].get(d):
                pend_sell.append(s)
            elif s not in hold and sig[s].get(d):
                pend_buy.append((s, a20[s].get(d)))
        pend_buy.sort(key=lambda x: -(sig[x[0] + "_adx"].get(d) or 0))
        eq.append(cash + sum(q * last.get(s, 0) for s, q in hold.items()))
    return pd.Series(eq, index=dates), ntr


# ---------------------------------------------------------------- B. risk parity
def erc_weights(cov, iters=200):
    n = len(cov)
    w = np.ones(n) / n
    for _ in range(iters):
        rc = w * (cov @ w)
        w = w * np.sqrt(rc.mean() / rc)
        w /= w.sum()
    return w


def risk_parity(frames, dates, method):
    names = list(frames)
    C = pd.DataFrame({s: f["close"] for s, f in frames.items()}).ffill()
    R = np.log(C).diff()
    review = _rebalance_days(dates, "monthly")
    slip = ETF_COSTS.slippage_pct / 100
    cash, qty, eq, pend, prev = CAP, {s: 0 for s in names}, [], None, None
    for d in dates:
        if prev is not None:
            cash *= (1 + CASH / 100) ** ((d - prev).days / 365.25)
        prev = d
        price = {s: C.at[d, s] for s in names}
        if pend is not None:
            equity = cash + sum(qty[s] * price[s] for s in names)
            for s in names:                                              # sells first
                want = math.floor(equity * pend[s] / price[s])
                if want < qty[s]:
                    v = (qty[s] - want) * price[s] * (1 - slip)
                    cash += v - ETF_COSTS.sell(v)
                    qty[s] = want
            for s in names:
                want = math.floor(equity * pend[s] / price[s] / 1.003)
                if want > qty[s]:
                    n = min(want - qty[s], math.floor((cash - 50) / (price[s] * (1 + slip) * 1.003)))
                    if n >= 1:
                        v = n * price[s] * (1 + slip)
                        cash -= v + ETF_COSTS.buy(v)
                        qty[s] += n
            pend = None
        if d in review:
            hist = R.loc[:d].iloc[-756:].dropna()
            if len(hist) >= 500:
                if method == "equal":
                    w = np.ones(len(names)) / len(names)
                elif method == "inverse vol":
                    v = hist.std().values
                    w = (1 / v) / (1 / v).sum()
                else:
                    w = erc_weights(hist.cov().values * 252)
                pend = dict(zip(names, w))
        eq.append(cash + sum(qty[s] * price[s] for s in names))
    return pd.Series(eq, index=dates)


# ---------------------------------------------------------------- C. pairs
def pairs(stocks, dates, corr_min=0.85, z_in=2.5, z_stop=4.5, max_days=30, cost=0.0024):
    C = pd.DataFrame({s: f["close"] for s, f in stocks.items()}).reindex(dates.union(
        pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()]))))).sort_index()
    L = np.log(C)
    R = C.pct_change()
    idx = L.index
    pos = {d: i for i, d in enumerate(idx)}
    months = [d for d in dates if d == dates[dates.to_period("M") == d.to_period("M")][0]]
    trades = []
    for m0 in months:
        i0 = pos[m0]
        win = L.iloc[i0 - 504:i0].dropna(axis=1)
        if len(win) < 504 or win.shape[1] < 10:
            continue
        cm = win.corr().values
        cols = list(win.columns)
        month_end = idx[min(len(idx) - 1, i0 + 22)]
        for a in range(len(cols)):
            for b in range(a + 1, len(cols)):
                if cm[a, b] <= corr_min:
                    continue
                A, B = cols[a], cols[b]
                beta = np.polyfit(win[B].values, win[A].values, 1)[0]
                S = (L[A] - beta * L[B])
                mu, sd = S.rolling(60).mean(), S.rolling(60).std()
                z = ((S - mu) / sd).values
                ra, rb = R[A].values, R[B].values
                i, in_trade = i0, False
                while i < len(idx) - 1 and (in_trade or idx[i] <= month_end):
                    zi = z[i]
                    if not in_trade:
                        if np.isfinite(zi) and abs(zi) > z_in and idx[i] <= month_end:
                            side, entry_i, pnl = np.sign(zi), i + 1, 0.0      # fill at the next close
                            in_trade = True
                        i += 1
                        continue
                    if i >= entry_i + 1 or i == entry_i:
                        if i > entry_i:
                            pnl += -side * ra[i] + side * rb[i]                # short rich leg, long cheap leg
                    done = (np.isfinite(zi) and (np.sign(zi) != side or abs(zi) > z_stop)) or (i - entry_i >= max_days)
                    if done:
                        trades.append(dict(entry=idx[entry_i], exit=idx[min(i + 1, len(idx) - 1)], a=A, b=B,
                                           ret=pnl / 2 + (-side * ra[min(i + 1, len(idx) - 1)] + side * rb[min(i + 1, len(idx) - 1)]) / 2
                                           - cost, days=i - entry_i))
                        in_trade = False
                        if idx[i] > month_end:
                            break
                    i += 1
    t = pd.DataFrame(trades)
    return t.drop_duplicates(["a", "b", "entry"]) if not t.empty else t


def week_t(t):
    w = t.groupby(t["entry"].dt.to_period("W"))["ret"].mean()
    return float(w.mean() / w.std() * math.sqrt(len(w))) if len(w) > 2 and w.std() > 0 else float("nan")


def main():
    frames = load()
    stocks = {s: f for s, f in frames.items() if s not in ETFS}
    full = pd.DatetimeIndex(sorted(set().union(*[f.index for f in stocks.values()])))
    dates = full[full >= "2016-01-01"]
    mid = dates[len(dates) // 2]
    rows = []

    def add(group, name, eq, n=None, **extra):
        rows.append(dict(group=group, strategy=name, cagr=cagr(eq), maxdd=max_drawdown(eq), sharpe=sharpe(eq),
                         sharpe_h1=sharpe(eq.loc[:mid]), sharpe_h2=sharpe(eq.loc[mid:]), cagr_h1=cagr(eq.loc[:mid]),
                         cagr_h2=cagr(eq.loc[mid:]), trades=n, **extra))
        r = rows[-1]
        print(f"{group:<12} {name:<34} CAGR {r['cagr']:5.1f}%  DD {r['maxdd']:6.1f}%  Sharpe {r['sharpe']:.2f} "
              f"({r['sharpe_h1']:.2f}/{r['sharpe_h2']:.2f})  CAGR halves {r['cagr_h1']:.1f}/{r['cagr_h2']:.1f}"
              + (f"  trades {n}" if n is not None else ""), flush=True)

    nb = frames["NIFTYBEES"]["close"].reindex(dates).ffill()
    add("benchmark", "NIFTYBEES hold", CAP * nb / nb.iloc[0])
    import exit_research as E
    etfs4 = {a: frames[a] for a in ("NIFTYBEES", "JUNIORBEES", "MON100", "GOLDBEES")}
    E.CAP = CAP

    def gchoose(d, cur):
        return [a for a, f in etfs4.items()
                if len(f["close"].loc[:d]) >= 200 and f["close"].loc[:d].iloc[-1] > f["close"].loc[:d].iloc[-200:].mean()]
    eq, n = E.sim(etfs4, gchoose, dates, 4, ETF_COSTS, "close")
    add("benchmark", "current ETF trend (live)", eq, n)

    # A. CTA
    etfs = {s: frames[s] for s in CTA_ETFS if s in frames}
    for label, kw in (("ADX>25, cross 5d", {}), ("ADX>20", dict(adx_min=20)), ("ADX>30", dict(adx_min=30)),
                      ("cross 10d", dict(window=10)), ("state (50>200 & ADX>25)", dict(state=True))):
        eq, n = cta(etfs, dates, ETF_COSTS, "close", **kw)
        add("CTA ETFs", label, eq, n)
        eq, n = cta(stocks, dates, Costs(), "open", **kw)
        add("CTA stocks", label, eq, n)

    # B. risk parity
    rp = {s: frames[s] for s in ("NIFTYBEES", "MON100", "GOLDBEES")}
    for m in ("equal", "inverse vol", "equal risk contribution"):
        add("risk parity", m, risk_parity(rp, dates, m))
    C3 = pd.DataFrame({s: f["close"] for s, f in rp.items()}).loc[:dates[-1]]
    R3 = np.log(C3).diff().iloc[-756:]
    vol = R3.std() * math.sqrt(252)
    w = erc_weights(R3.cov().values * 252)
    rc = w * (R3.cov().values * 252 @ w)
    print("\nRisk parity today (3-year data): vol " + ", ".join(f"{s} {v:.1%}" for s, v in vol.items()))
    print("  equal-risk weights " + ", ".join(f"{s} {x:.1%}" for s, x in zip(rp, w))
          + " | risk shares " + ", ".join(f"{x / rc.sum():.1%}" for x in rc))

    # C. pairs
    for label, kw in (("z2.5 corr0.85", {}), ("z2.0", dict(z_in=2.0)), ("z3.0", dict(z_in=3.0)),
                      ("corr0.80", dict(corr_min=0.80)), ("corr0.90", dict(corr_min=0.90))):
        t = pairs(stocks, dates, **kw)
        h1, h2 = t[t.entry < mid], t[t.entry >= mid]
        rows.append(dict(group="pairs", strategy=label, trades=len(t), avg_net=t.ret.mean() * 100,
                         h1=h1.ret.mean() * 100, h2=h2.ret.mean() * 100, win=(t.ret > 0).mean() * 100, t_week=week_t(t),
                         avg_days=t.days.mean()))
        r = rows[-1]
        print(f"pairs        {label:<34} trades {r['trades']:5d}  avg net {r['avg_net']:+.2f}%  halves "
              f"{r['h1']:+.2f}/{r['h2']:+.2f}%  win {r['win']:.0f}%  t {r['t_week']:.2f}  days {r['avg_days']:.1f}",
              flush=True)
    pd.DataFrame(rows).to_csv(ROOT / "research" / "other_strategies.csv", index=False)


if __name__ == "__main__":
    main()
