"""Intraday research lab: published day-trading methods on the same cached 5-minute data.

Run `python -m backtest.intraday` first (it downloads/tops up cache/intraday_5m), then:
  python -m backtest.intraday_lab                # every method, all stocks and the 10 most volatile
  python -m backtest.intraday_lab --top 5

Methods (each reported long+short, long only and short only):
  ORB 15/30 min       opening range breakout (Crabel; Zarattini, Barbon & Aziz 2024), stop at the other side
  Noise area          Zarattini, Aziz & Barbon 2024 "Beat the Market" (SPY), applied to single stocks
  VWAP trend          Zarattini & Aziz 2023: long above VWAP, short below
  Late-day momentum   Gao, Han, Li & Zhou 2018: first half-hour return predicts the last half-hour
  Gap fade / gap-go   opening gaps > 1%, faded or followed
  Overreaction fade   moves > 1.5 daily ATR by 10:15 faded until 15:15

Trades fill at a 5-minute bar's close (slippage is in the cost figure) and close by 15:15.
"Most volatile" stocks are chosen BEFORE the open from yesterday's ATR as % of price.
Research only: places no orders.
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .intraday import IntradayCosts

ROOT = Path(__file__).resolve().parent.parent
BARS = 75                     # 09:15 to 15:25, 5-minute bars
T = lambda h, m: (h * 60 + m - (9 * 60 + 15)) // 5      # index of the bar starting at h:m
EXIT = T(15, 10)              # the 15:10 bar closes at 15:15
COST = 0.0012                 # replaced in main() from IntradayCosts and the position size


def load(cache: Path):
    O, daily = {}, {}
    for p in sorted(glob.glob(str(cache / "intraday_5m" / "*.csv"))):
        s = os.path.basename(p)[:-4]
        d = cache / "history" / f"{s}.csv"
        if not d.exists():
            continue
        O[s] = pd.read_csv(p, parse_dates=["date"]).set_index("date")
        daily[s] = pd.read_csv(d, parse_dates=["date"]).set_index("date")
    if not O:
        raise SystemExit("No cached 5-minute data. Run `python -m backtest.intraday` first.")
    days = sorted(set.intersection(*[set(df.index.normalize()) for df in O.values()]))
    syms = list(O)
    A = np.full((len(syms), len(days), BARS, 5), np.nan)
    dpos = {d: i for i, d in enumerate(days)}
    for k, s in enumerate(syms):
        df = O[s]
        di = np.array([dpos.get(x, -1) for x in df.index.normalize()])
        bi = ((df.index.hour * 60 + df.index.minute) - 555) // 5
        ok = (di >= 0) & (bi >= 0) & (bi < BARS)
        A[k, di[ok], bi[ok]] = df[["open", "high", "low", "close", "volume"]].values[ok]
    # special or broken sessions (Diwali muhurat trading, missing opening bar): drop the stock-day
    n_bars = np.isfinite(A[:, :, :, 3]).sum(axis=2)
    broken = (n_bars < 70) | ~np.isfinite(A[:, :, 0, 0]) | ~np.isfinite(A[:, :, EXIT, 3])
    A[broken] = np.nan
    days_idx = pd.DatetimeIndex(days)
    ctx = {n: np.full((len(syms), len(days)), np.nan) for n in ["prev_close", "atr", "sma20"]}
    for k, s in enumerate(syms):
        d = daily[s]
        pc = d["close"].shift(1)
        tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
        frame = pd.DataFrame({"prev_close": pc, "atr": tr.rolling(14).mean().shift(1),
                              "sma20": d.close.rolling(20).mean().shift(1)}).reindex(days_idx)
        for n in ctx:
            ctx[n][k] = frame[n].values
    return syms, days_idx, A, ctx


def vwap(A):
    tp = (A[..., 1] + A[..., 2] + A[..., 3]) / 3
    v = A[..., 4]
    return np.nancumsum(tp * v, axis=2) / np.nancumsum(v, axis=2)


def orb(A, ctx, minutes=15, stop="range", target_r=0.0, trend_filter=False, market_filter=False, relvol_min=0.0):
    n_s, n_d = A.shape[:2]
    k = minutes // 5
    hi = np.nanmax(A[:, :, :k, 1], axis=2); lo = np.nanmin(A[:, :, :k, 2], axis=2)
    vol = np.nansum(A[:, :, :k, 4], axis=2)
    avgvol = pd.DataFrame(vol.T).shift(1).rolling(14).mean().T.values
    relvol = vol / avgvol
    # market direction at the end of the range: equal-weight basket return from open
    basket = np.nanmean(A[:, :, k - 1, 3] / A[:, :, 0, 0] - 1, axis=0)
    rows = []
    for s in range(n_s):
        for d in range(n_d):
            if np.isnan(A[s, d, 0, 0]):
                continue
            if not (relvol[s, d] >= relvol_min) and relvol_min > 0:
                continue
            H, L = hi[s, d], lo[s, d]
            rng_ = H - L
            if not rng_ > 0:
                continue
            bars = A[s, d]
            pc, s20 = ctx["prev_close"][s, d], ctx["sma20"][s, d]
            for i in range(k, EXIT + 1):
                c = bars[i, 3]
                side = "long" if c > H else "short" if c < L else None
                if side is None:
                    continue
                if trend_filter and not np.isnan(s20):
                    if side == "long" and not pc > s20: break
                    if side == "short" and not pc < s20: break
                if market_filter:
                    if side == "long" and not basket[d] > 0: break
                    if side == "short" and not basket[d] < 0: break
                entry = c
                sl = (L if side == "long" else H) if stop == "range" else (H + L) / 2
                risk = abs(entry - sl)
                tgt = entry + target_r * risk * (1 if side == "long" else -1) if target_r else None
                ex = bars[EXIT, 3]
                for j in range(i + 1, EXIT + 1):
                    b = bars[j]
                    if side == "long":
                        if b[2] <= sl: ex = min(b[0], sl); break
                        if tgt and b[1] >= tgt: ex = max(b[0], tgt); break
                    else:
                        if b[1] >= sl: ex = max(b[0], sl); break
                        if tgt and b[2] <= tgt: ex = min(b[0], tgt); break
                g = (ex / entry - 1) * (1 if side == "long" else -1)
                rows.append((d, s, side, g, relvol[s, d] if relvol[s, d] == relvol[s, d] else 0))
                break
    return pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])

def noise_area(A, ctx, lookback=14, check_every=6, vol_mult=1.0, first_check=T(9, 40)):
    """Zarattini, Aziz & Barbon (2024) 'Beat the Market', applied to single stocks.
    sigma(t) = mean over previous `lookback` days of |close(t)/open - 1| at the same bar.
    Upper = max(open, prev close) * (1 + sigma); lower = min(open, prev close) * (1 - sigma).
    Checked every 30 minutes; trailing stop = max(upper, VWAP) for longs, min(lower, VWAP) for shorts."""
    n_s, n_d = A.shape[:2]
    VW = vwap(A)
    mv = np.abs(A[:, :, :, 3] / A[:, :, :1, 0] - 1)
    sig = np.full(mv.shape, np.nan)
    for d in range(lookback, n_d):
        sig[:, d] = np.nanmean(mv[:, d - lookback:d], axis=1)
    rows = []
    checks = list(range(first_check, EXIT, check_every))
    for s in range(n_s):
        for d in range(lookback, n_d):
            op, pc = A[s, d, 0, 0], ctx["prev_close"][s, d]
            if np.isnan(op) or np.isnan(pc):
                continue
            ub = max(op, pc) * (1 + vol_mult * sig[s, d]); lb = min(op, pc) * (1 - vol_mult * sig[s, d])
            pos, entry, entry_i = 0, None, None
            for i in checks + [EXIT]:
                c = A[s, d, i, 3]
                if pos:
                    stop_hit = (pos > 0 and c < max(ub[i], VW[s, d, i])) or (pos < 0 and c > min(lb[i], VW[s, d, i]))
                    if stop_hit or i == EXIT:
                        rows.append((d, s, "long" if pos > 0 else "short", (c / entry - 1) * pos, sig[s, d, i]))
                        pos = 0
                if i == EXIT:
                    break
                if not pos:
                    if c > ub[i]: pos, entry = 1, c
                    elif c < lb[i]: pos, entry = -1, c
    tr = pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])
    return tr

def vwap_trend(A, ctx, every=1, start=T(9, 20)):
    """Zarattini & Aziz (2023): long above VWAP, short below, flipping; evaluated every `every` bars."""
    n_s, n_d = A.shape[:2]
    VW = vwap(A)
    rows = []
    for s in range(n_s):
        for d in range(n_d):
            if np.isnan(A[s, d, 0, 0]):
                continue
            pos, entry = 0, None
            for i in list(range(start, EXIT, every)) + [EXIT]:
                c, v = A[s, d, i, 3], VW[s, d, i]
                want = 0 if i == EXIT else (1 if c > v else -1)
                if pos and want != pos:
                    rows.append((d, s, "long" if pos > 0 else "short", (c / entry - 1) * pos, 0.0))
                    pos = 0
                if want and not pos:
                    pos, entry = want, c
    return pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])

def late_momentum(A, ctx, signal_end=T(9, 40), enter=T(14, 40), market=False):
    """Gao, Han, Li & Zhou (2018): first half-hour return (incl. overnight) predicts the last half-hour.
    Enter at 14:45 close in the signal's direction, exit 15:15 close (MIS square-off comes before 15:30)."""
    n_s, n_d = A.shape[:2]
    r1 = A[:, :, signal_end, 3] / ctx["prev_close"] - 1
    if market:
        r1 = np.broadcast_to(np.nanmean(r1, axis=0), r1.shape)
    rows = []
    for s in range(n_s):
        for d in range(n_d):
            x = r1[s, d]
            if np.isnan(x) or x == 0 or np.isnan(A[s, d, enter, 3]):
                continue
            side = 1 if x > 0 else -1
            g = (A[s, d, EXIT, 3] / A[s, d, enter, 3] - 1) * side
            rows.append((d, s, "long" if side > 0 else "short", g, abs(x)))
    return pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])

def gap_trade(A, ctx, min_gap=0.01, mode="fade", stop_atr=0.5):
    """Opening gap > min_gap: 'fade' bets on a move back toward yesterday's close, 'go' on continuation.
    Enter at the first 5-minute bar's close; stop at stop_atr x daily ATR; exit 15:15."""
    n_s, n_d = A.shape[:2]
    rows = []
    for s in range(n_s):
        for d in range(n_d):
            pc, a = ctx["prev_close"][s, d], ctx["atr"][s, d]
            op = A[s, d, 0, 0]
            if np.isnan(pc) or np.isnan(a) or np.isnan(op):
                continue
            gap = op / pc - 1
            if abs(gap) < min_gap:
                continue
            side = (-1 if gap > 0 else 1) if mode == "fade" else (1 if gap > 0 else -1)
            entry = A[s, d, 0, 3]
            sl = entry - side * stop_atr * a
            ex = A[s, d, EXIT, 3]
            for j in range(1, EXIT + 1):
                b = A[s, d, j]
                if side > 0 and b[2] <= sl: ex = min(b[0], sl); break
                if side < 0 and b[1] >= sl: ex = max(b[0], sl); break
            rows.append((d, s, "long" if side > 0 else "short", (ex / entry - 1) * side, abs(gap)))
    return pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])

def first_hour_reversal(A, ctx, at=T(10, 10), z=1.5):
    """Short-term overreaction: stocks that moved > z daily-ATR-scaled amounts by 10:15 are faded to 15:15."""
    n_s, n_d = A.shape[:2]
    rows = []
    for s in range(n_s):
        for d in range(n_d):
            pc, a = ctx["prev_close"][s, d], ctx["atr"][s, d]
            if np.isnan(pc) or np.isnan(a):
                continue
            c = A[s, d, at, 3]
            if np.isnan(c):
                continue
            mv = (c - pc) / a
            if abs(mv) < z:
                continue
            side = -1 if mv > 0 else 1
            rows.append((d, s, "long" if side > 0 else "short", (A[s, d, EXIT, 3] / c - 1) * side, abs(mv)))
    return pd.DataFrame(rows, columns=["day", "sym", "side", "gross", "score"])


METHODS = {
    "ORB 15m": lambda A, c: orb(A, c, 15),
    "ORB 30m": lambda A, c: orb(A, c, 30),
    "ORB 30m + market filter": lambda A, c: orb(A, c, 30, market_filter=True),
    "Noise area": lambda A, c: noise_area(A, c),
    "Noise area 1.5x": lambda A, c: noise_area(A, c, vol_mult=1.5),
    "VWAP trend 5m": lambda A, c: vwap_trend(A, c, 1),
    "VWAP trend 30m": lambda A, c: vwap_trend(A, c, 6),
    "Late-day momentum": lambda A, c: late_momentum(A, c),
    "Gap fade >1%": lambda A, c: gap_trade(A, c, 0.01, "fade"),
    "Gap and go >1%": lambda A, c: gap_trade(A, c, 0.01, "go"),
    "Overreaction fade": lambda A, c: first_hour_reversal(A, c),
}


def evaluate(tr: pd.DataFrame, rank, n_slots: int, n_days: int, days, label: str, universe: str):
    """Per-trade statistics and a simple portfolio: capital split into n_slots, one per stock."""
    rows = []
    tr = tr.copy()
    tr["net"] = tr["gross"] - COST
    if rank is not None:
        tr = tr[[rank[s, d] <= n_slots for s, d in zip(tr.sym, tr.day)]]
    for side in ("both", "long", "short"):
        t = tr if side == "both" else tr[tr.side == side]
        if t.empty:
            continue
        daily = (t.groupby("day")["net"].sum() / n_slots).reindex(range(n_days), fill_value=0.0).iloc[15:]
        ann = (1 + daily).prod() ** (250 / len(daily)) - 1
        months = daily.groupby(days[daily.index].to_period("M")).apply(lambda x: (1 + x).prod() - 1)
        eq = (1 + daily).cumprod()
        m = t["net"].mean()
        dd = daily[daily != 0]                    # trades on the same day are not independent: test by day
        rows.append({"method": label, "stocks": universe, "side": side, "trades": len(t),
                     "win%": (t.net > 0).mean() * 100, "gross bps": t.gross.mean() * 1e4,
                     "net bps": m * 1e4,
                     "t-stat": dd.mean() / dd.std() * math.sqrt(len(dd)) if len(dd) > 2 and dd.std() else np.nan,
                     "annual %": ann * 100, "max DD %": (eq / eq.cummax() - 1).min() * 100,
                     "+ months": f"{(months > 0).sum()}/{len(months)}"})
    return rows


def main(argv=None) -> int:
    global COST
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Compare published intraday methods on cached 5-minute data")
    ap.add_argument("--config", default=str(ROOT / "backtest.yaml"))
    ap.add_argument("--top", type=int, default=10, help="also test only the N most volatile stocks each day")
    args = ap.parse_args(argv)
    p = Path(args.config)
    cfg = yaml.safe_load((p if p.exists() else ROOT / "backtest.example.yaml").read_text(encoding="utf-8")) or {}
    icfg = cfg.get("intraday") or {}
    costs = IntradayCosts(**(icfg.get("costs") or {}))
    size = float(cfg.get("capital", 500_000)) / int(icfg.get("max_positions", 5))
    COST = (costs.buy(size) + costs.sell(size)) / size + 2 * costs.slippage_pct / 100
    syms, days, A, ctx = load(ROOT / "cache")
    n_d = len(days)
    print(f"{len(syms)} stocks, {n_d} sessions ({days[0]:%d %b %Y} to {days[-1]:%d %b %Y}); "
          f"round-trip cost {COST * 100:.3f}% at ₹{size:,.0f} per trade\n")
    atrp = ctx["atr"] / ctx["prev_close"]
    rank = pd.DataFrame(atrp).rank(axis=0, ascending=False).values
    rows = []
    for name, fn in METHODS.items():
        tr = fn(A, ctx)
        if tr.empty:
            continue
        rows += evaluate(tr, None, len(syms), n_d, days, name, "all")
        rows += evaluate(tr, rank, args.top, n_d, days, name, f"top {args.top} vol")
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(res.round(1).to_string(index=False))
    print("\nnet bps = average result per trade after costs (100 bps = 1%). t-stat is computed on daily portfolio"
          " results; below ~2 is indistinguishable from luck.\nWith under a year of 5-minute history, treat every figure as provisional.")
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    res.to_csv(out / f"intraday_lab_{pd.Timestamp.now():%Y%m%d-%H%M%S}.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
