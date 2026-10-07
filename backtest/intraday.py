"""Intraday backtest: Opening Range Breakout (ORB) on "stocks in play", long and short.

Based on Zarattini, Barbon & Aziz, "A Profitable Day Trading Strategy for the U.S. Equity Market"
(2024): trade only stocks with unusually high opening volume, in the direction of the first
5-minute candle, with a tight stop, and close everything before the end of the day.
Their US result (2016-2023) was strong; independent replications found it degrades outside the
original sample, and its win rate is low (~17%): a few big days pay for many small stopped losses.

Daily procedure (all times IST, NSE session 09:15-15:30):
  1. Opening range = the first `or_minutes` (default 5) of the session.
  2. Relative volume = opening-range volume / its average over the previous 14 sessions.
     Candidates need relative volume >= `min_relvol`; the top `max_positions` by it are traded.
  3. Direction: bullish opening candle -> buy-stop at the range high; bearish -> sell-stop
     (short) at the range low; a flat candle is skipped.
  4. Stop = entry -/+ `stop_atr_frac` x the stock's 14-day ATR (daily, as of yesterday).
  5. Exit at the stop, else at `exit_time` (default 15:15, before the broker's auto square-off).
  6. Size: risk `risk_pct`% of equity per trade, capped at (equity x leverage / max_positions).

Honesty rules: entries need a later bar to trade through the level (never the opening-range bar
itself); if the entry bar also touches the stop we count the stop (worst case); gap-throughs fill
at the bar's open; intraday costs and slippage are charged on both legs.

Research only. Places no orders.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

from .data import PriceStore, resolve_codes, update_history
from .metrics import cagr, max_drawdown, sharpe
from .strategies import atr

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("backtest.intraday")
SESSION_OPEN = time(9, 15)


@dataclass
class IntradayCosts:
    """NSE equity intraday (MIS) charges. Approximate; check a recent contract note."""
    brokerage_per_order: float = 10.0
    stt_sell_pct: float = 0.025          # intraday STT: sell side only
    exchange_pct: float = 0.00297
    sebi_pct: float = 0.0001
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    slippage_pct: float = 0.03           # each side; liquid Nifty 50 names

    def buy(self, value: float) -> float:
        b = self.brokerage_per_order
        ex = value * (self.exchange_pct + self.sebi_pct) / 100
        return b + ex + value * self.stamp_buy_pct / 100 + (b + ex) * self.gst_pct / 100

    def sell(self, value: float) -> float:
        b = self.brokerage_per_order
        ex = value * (self.exchange_pct + self.sebi_pct) / 100
        return b + ex + value * self.stt_sell_pct / 100 + (b + ex) * self.gst_pct / 100


@dataclass
class IntradayTrade:
    symbol: str
    date: pd.Timestamp
    side: str               # long | short
    entry_time: pd.Timestamp
    entry_px: float
    exit_time: pd.Timestamp
    exit_px: float
    qty: int
    pnl: float              # after costs
    r_multiple: float       # pnl / planned risk
    return_pct: float       # pnl / position value
    bars: int
    reason: str             # stop | close
    relvol: float


def opening_stats(bars: pd.DataFrame, or_minutes: int = 5, lookback: int = 14) -> pd.DataFrame:
    """Per session: opening-range OHLCV and relative volume vs the previous `lookback` sessions."""
    if bars.empty:
        return pd.DataFrame()
    b = bars.copy()
    b["day"] = b.index.normalize()
    start = b.index.normalize() + pd.Timedelta(hours=9, minutes=15)
    in_or = (b.index >= start) & (b.index < start + pd.Timedelta(minutes=or_minutes))
    g = b[in_or].groupby("day")
    o = pd.DataFrame({"or_open": g["open"].first(), "or_high": g["high"].max(), "or_low": g["low"].min(),
                      "or_close": g["close"].last(), "or_vol": g["volume"].sum(), "n": g["open"].count()})
    o = o[o["n"] > 0]
    o["avg_vol"] = o["or_vol"].shift(1).rolling(lookback, min_periods=lookback).mean()
    o["relvol"] = o["or_vol"] / o["avg_vol"]
    return o


def simulate_orb(intra: Dict[str, pd.DataFrame], daily: Dict[str, pd.DataFrame], capital: float = 500_000,
                 max_positions: int = 5, risk_pct: float = 1.0, stop_atr_frac: float = 0.10,
                 leverage: float = 1.0, min_relvol: float = 1.0, or_minutes: int = 5,
                 exit_time: str = "15:15", sides=("long", "short"), costs: Optional[IntradayCosts] = None,
                 start: Optional[pd.Timestamp] = None):
    costs = costs or IntradayCosts()
    slip = costs.slippage_pct / 100
    ex_h, ex_m = map(int, exit_time.split(":"))
    stats = {s: opening_stats(df, or_minutes) for s, df in intra.items()}
    prev_atr = {s: atr(d, 14).shift(1) for s, d in daily.items()}           # yesterday's ATR: no look-ahead
    bars_by_day = {s: {day: g for day, g in df.groupby(df.index.normalize())} for s, df in intra.items()}
    days = sorted(set().union(*[set(st.index) for st in stats.values() if len(st)])) if stats else []
    if start is not None:
        days = [d for d in days if d >= start]

    equity = capital
    curve, trades = [], []
    costs_paid = 0.0
    for day in days:
        cands = []
        for s, st in stats.items():
            if day not in st.index or s not in prev_atr:
                continue
            r = st.loc[day]
            a = prev_atr[s].get(day)
            if not (r["relvol"] >= min_relvol) or a is None or not (a > 0):
                continue
            if r["or_close"] > r["or_open"] and "long" in sides:
                side = "long"
            elif r["or_close"] < r["or_open"] and "short" in sides:
                side = "short"
            else:
                continue
            cands.append((r["relvol"], s, side, r, a))
        day_pnl = 0.0
        for relvol, s, side, r, a in sorted(cands, key=lambda x: -x[0])[:max_positions]:
            bars = bars_by_day[s].get(day)
            if bars is None:
                continue
            or_end = day + pd.Timedelta(hours=9, minutes=15 + or_minutes)
            cutoff = day + pd.Timedelta(hours=ex_h, minutes=ex_m)
            session = bars[(bars.index >= or_end) & (bars.index < cutoff)]
            level = r["or_high"] if side == "long" else r["or_low"]
            stop_dist = stop_atr_frac * a
            entry_i = None
            for i, (ts, b) in enumerate(session.iterrows()):
                if (side == "long" and b["high"] >= level) or (side == "short" and b["low"] <= level):
                    entry_i = i
                    break
            if entry_i is None:
                continue
            eb = session.iloc[entry_i]
            raw = max(eb["open"], level) if side == "long" else min(eb["open"], level)
            stop = raw - stop_dist if side == "long" else raw + stop_dist
            entry = raw * (1 + slip) if side == "long" else raw * (1 - slip)
            per_share_risk = abs(entry - stop)
            qty = math.floor(min(equity * risk_pct / 100 / per_share_risk,
                                 equity * leverage / max_positions / entry))
            if qty < 1:
                continue
            exit_px, exit_ts, reason, nbars = None, None, "close", 0
            for j in range(entry_i, len(session)):
                b = session.iloc[j]
                nbars += 1
                hit = b["low"] <= stop if side == "long" else b["high"] >= stop
                if hit:
                    fill = min(b["open"], stop) if side == "long" else max(b["open"], stop)
                    if j == entry_i:
                        fill = stop                       # entry bar: assume the worst, stopped at the level
                    exit_px = fill * (1 - slip) if side == "long" else fill * (1 + slip)
                    exit_ts, reason = session.index[j], "stop"
                    break
            if exit_px is None:
                last = session.iloc[-1]
                exit_px = last["close"] * (1 - slip) if side == "long" else last["close"] * (1 + slip)
                exit_ts = session.index[-1]
            if side == "long":
                c = costs.buy(qty * entry) + costs.sell(qty * exit_px)
                gross = qty * (exit_px - entry)
            else:
                c = costs.sell(qty * entry) + costs.buy(qty * exit_px)
                gross = qty * (entry - exit_px)
            pnl = gross - c
            costs_paid += c
            day_pnl += pnl
            trades.append(IntradayTrade(s, day, side, session.index[entry_i], entry, exit_ts, exit_px, qty, pnl,
                                        pnl / (qty * per_share_risk), pnl / (qty * entry) * 100, nbars, reason,
                                        float(relvol)))
        equity += day_pnl
        curve.append((day, equity))
    eq = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([d for d, _ in curve]), dtype=float)
    return eq, trades, costs_paid


def summarize_intraday(eq: pd.Series, trades: List[IntradayTrade], costs_paid: float, split=None) -> dict:
    out = dict(cagr=cagr(eq), max_dd=max_drawdown(eq), sharpe=sharpe(eq), trades=len(trades),
               costs=costs_paid, final=float(eq.iloc[-1]) if len(eq) else float("nan"))
    if trades:
        pnl = np.array([t.pnl for t in trades])
        r = np.array([t.r_multiple for t in trades])
        out.update(win_rate=(pnl > 0).mean() * 100, avg_r=r.mean(), best_r=r.max(),
                   profit_factor=pnl[pnl > 0].sum() / -pnl[pnl <= 0].sum() if (pnl <= 0).any() else float("inf"),
                   stops=sum(t.reason == "stop" for t in trades) / len(trades) * 100,
                   long_pnl=sum(t.pnl for t in trades if t.side == "long"),
                   short_pnl=sum(t.pnl for t in trades if t.side == "short"),
                   days_traded=len({t.date for t in trades}))
    if split is not None and len(eq):
        out["cagr_in_sample"] = cagr(eq.loc[:split])
        out["cagr_out_of_sample"] = cagr(eq.loc[split:])
    return out


ROWS = [("cagr", "Annualised return %", "{:.1f}"), ("max_dd", "Worst drawdown %", "{:.1f}"),
        ("sharpe", "Sharpe ratio", "{:.2f}"), ("cagr_in_sample", "Annualised, first part %", "{:.1f}"),
        ("cagr_out_of_sample", "Annualised, last part %", "{:.1f}"), ("trades", "Trades", "{:.0f}"),
        ("days_traded", "Days with a trade", "{:.0f}"), ("win_rate", "Win rate %", "{:.0f}"),
        ("avg_r", "Average result (R)", "{:+.2f}"), ("best_r", "Best trade (R)", "{:+.1f}"),
        ("stops", "Stopped out %", "{:.0f}"), ("profit_factor", "Profit factor", "{:.2f}"),
        ("long_pnl", "P&L from longs ₹", "{:,.0f}"), ("short_pnl", "P&L from shorts ₹", "{:,.0f}"),
        ("costs", "Costs paid ₹", "{:,.0f}"), ("final", "Final value ₹", "{:,.0f}")]


def demo_data(n: int = 15, days: int = 160, seed: int = 3):
    """Synthetic 5-minute bars: mostly random, with occasional mild trend days on higher volume.
    Plumbing test only; a random market should NOT make money after costs."""
    rng = np.random.default_rng(seed)
    sessions = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    slots = pd.timedelta_range("09:15:00", "15:25:00", freq="5min")
    intra, daily = {}, {}
    for k in range(n):
        price, rows, drows = 500.0 * (1 + k / 10), [], []
        for d in sessions:
            trend_day = rng.random() < 0.10
            drift = rng.choice([-1, 1]) * 0.0003 if trend_day else 0.0       # ~2% over a full session
            vol_mult = rng.uniform(1.5, 3) if trend_day else rng.uniform(0.6, 1.4)
            p = price * np.exp(rng.normal(0, 0.006))
            o_, h_, l_ = p, p, p
            for i, t in enumerate(slots):
                o = p
                c = p * np.exp(drift + rng.normal(0, 0.0018))
                hi, lo = max(o, c) * (1 + abs(rng.normal(0, 0.0007))), min(o, c) * (1 - abs(rng.normal(0, 0.0007)))
                v = 1e4 * vol_mult * (3 if i == 0 else 1)
                rows.append((d + t, o, hi, lo, c, v))
                p, h_, l_ = c, max(h_, hi), min(l_, lo)
            drows.append((d, o_, h_, l_, p, 1e6))
            price = p
        cols = ["date", "open", "high", "low", "close", "volume"]
        intra[f"DEMO{k:02d}"] = pd.DataFrame(rows, columns=cols).set_index("date")
        daily[f"DEMO{k:02d}"] = pd.DataFrame(drows, columns=cols).set_index("date")
    return intra, daily


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Intraday ORB backtest (long/short) on INDstocks 5-minute data")
    ap.add_argument("--config", default=str(ROOT / "backtest.yaml"))
    ap.add_argument("--days", type=int, help="calendar days of 5-minute history to test")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)

    path = Path(args.config)
    cfg = yaml.safe_load((path if path.exists() else ROOT / "backtest.example.yaml").read_text(encoding="utf-8")) or {}
    icfg = cfg.get("intraday") or {}
    days = args.days or int(icfg.get("days", 180))
    capital = float(cfg.get("capital", 500_000))

    if args.demo:
        intra, daily = demo_data()
        print("DEMO MODE: synthetic prices. Results say nothing about real markets.\n")
    else:
        try:
            from guardian.secrets import load_secrets
            load_secrets(ROOT)
        except ImportError:
            pass
        from guardian.broker import IndStocksClient
        from guardian.main import instrument_index
        client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
        codes, missing = resolve_codes(cfg.get("universe", []), instrument_index(client, ROOT / "cache"), "NSE")
        if missing:
            print(f"Not found on NSE, skipped: {', '.join(missing)}")
        intra = update_history(client, codes, (days + 30) / 365.25, PriceStore(ROOT, "intraday_5m"),
                               refresh=args.refresh, interval="5minute", window_days=7)
        daily = update_history(client, codes, (days + 60) / 365.25, PriceStore(ROOT), interval="1day")
        intra = {s: df for s, df in intra.items() if len(df)}
        if not intra:
            print("INDstocks returned no 5-minute history.")
            return 1
        span = {s: (df.index[0], df.index[-1]) for s, df in intra.items()}
        first = min(v[0] for v in span.values())
        print(f"5-minute history available from {first:%d %b %Y} (requested {days} days)")

    all_days = sorted(set().union(*[set(df.index.normalize()) for df in intra.values()]))
    start = all_days[min(len(all_days) - 1, 15)]                # 14 sessions to build relative volume
    test_days = [d for d in all_days if d >= start]
    if len(test_days) < 20:
        print("Not enough intraday history to test (need 20+ sessions after a 14-session warm-up).")
        return 1
    split = test_days[int(len(test_days) * (1 - float(cfg.get("out_of_sample_pct", 30)) / 100))]
    print(f"Testing {len(intra)} stocks over {len(test_days)} sessions, {test_days[0]:%d %b %Y} to "
          f"{test_days[-1]:%d %b %Y}; last part from {split:%d %b %Y}\n")

    costs = IntradayCosts(**(icfg.get("costs") or {}))
    params = dict(max_positions=int(icfg.get("max_positions", 5)), risk_pct=float(icfg.get("risk_pct", 1.0)),
                  stop_atr_frac=float(icfg.get("stop_atr_frac", 0.10)), leverage=float(icfg.get("leverage", 1.0)),
                  min_relvol=float(icfg.get("min_relvol", 1.0)), or_minutes=int(icfg.get("or_minutes", 5)),
                  exit_time=str(icfg.get("exit_time", "15:15")))
    variants = {"ORB long+short": ("long", "short"), "ORB long only": ("long",), "ORB short only": ("short",)}
    out_dir = ROOT / "results" / ("intraday-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries, curves = {}, {}
    for name, sides in variants.items():
        eq, trades, cp = simulate_orb(intra, daily, capital, sides=sides, costs=costs, start=start, **params)
        summaries[name] = summarize_intraday(eq, trades, cp, split)
        curves[name] = eq
        pd.DataFrame([t.__dict__ for t in trades]).to_csv(out_dir / f"trades_{name.replace(' ', '_').replace('+', '_')}.csv",
                                                          index=False)
    fd_rate = float(cfg.get("fd_rate", 6.25))
    idx = pd.DatetimeIndex(test_days)
    fd = pd.Series(capital * (1 + fd_rate / 100) ** (np.array([(d - idx[0]).days for d in idx]) / 365.25), index=idx)
    summaries["FD"] = dict(cagr=cagr(fd), max_dd=0.0, final=float(fd.iloc[-1]))
    curves["FD"] = fd

    names = list(summaries)
    w = 17
    print(f"{'':30}" + "".join(f"{n:>{w}}" for n in names))
    for key, label, f in ROWS:
        cells = []
        for n in names:
            v = summaries[n].get(key)
            cells.append(f"{'—':>{w}}" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{f.format(v):>{w}}")
        print(f"{label:30}" + "".join(cells))
    print(f"\nSettings: top {params['max_positions']} stocks by opening relative volume (>= {params['min_relvol']}x), "
          f"{params['or_minutes']}-min range, stop {params['stop_atr_frac']:.0%} of daily ATR, "
          f"risk {params['risk_pct']}%/trade, leverage {params['leverage']}x, exit {params['exit_time']}.")
    print("R = result in units of the planned risk per trade. Costs: intraday STT, exchange, SEBI, stamp, GST,")
    print("₹10/order brokerage and slippage on both legs. A short history makes annualised figures unreliable.")
    pd.DataFrame(summaries).to_csv(out_dir / "summary.csv")
    pd.DataFrame(curves).to_csv(out_dir / "equity.csv", index_label="date")
    if not args.no_chart:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(11, 5))
            for n, c in curves.items():
                ax.plot(c.index, c.values / 1e5, label=n, linestyle="--" if n == "FD" else "-")
            ax.axvline(split, color="grey", linewidth=0.8, linestyle=":")
            ax.set_ylabel("Portfolio value (₹ lakh)")
            ax.set_title("Intraday ORB vs FD")
            ax.grid(alpha=0.3)
            ax.legend(loc="upper left", fontsize=8)
            fig.tight_layout()
            fig.savefig(out_dir / "equity.png", dpi=130)
        except ImportError:
            pass
    print(f"\nSaved results to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
