"""Swing strategies for cash stocks (long only).

Each strategy takes one stock's daily OHLC frame and returns it with three extra columns,
all computed from data up to and including that day's CLOSE:

    entry  - True: buy at the NEXT day's open
    exit   - True: sell at the NEXT day's open
    score  - ranks competing entries when there are more signals than free slots (higher first)

The engine also uses `atr` (14-day average true range) for the protective stop.
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np
import pandas as pd


# ---------- indicators ----------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0)


def _base(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    return out


# ---------- strategies ----------
def sma_trend(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    """Trend following: buy when the fast average crosses above the slow one, sell on the cross back."""
    out = _base(df)
    f, s = sma(out["close"], fast), sma(out["close"], slow)
    above = f > s
    out["entry"] = above & ~above.shift(1, fill_value=False) & s.notna()
    out["exit"] = ~above & above.shift(1, fill_value=False)
    out["score"] = (out["close"] / s - 1).fillna(0)          # stronger trend first
    return out


def donchian_breakout(df: pd.DataFrame, entry_n: int = 50, exit_n: int = 20, trend: int = 200) -> pd.DataFrame:
    """Breakout: buy a close above the prior N-day high (in an uptrend), sell below the prior M-day low."""
    out = _base(df)
    hi = out["high"].rolling(entry_n, min_periods=entry_n).max().shift(1)   # prior days only
    lo = out["low"].rolling(exit_n, min_periods=exit_n).min().shift(1)
    t = sma(out["close"], trend)
    out["entry"] = (out["close"] > hi) & (out["close"] > t)
    out["exit"] = out["close"] < lo
    out["score"] = (out["close"] / hi - 1).fillna(0)
    return out


def rsi2_pullback(df: pd.DataFrame, rsi_n: int = 2, buy_below: float = 10, trend: int = 200,
                  exit_sma: int = 5) -> pd.DataFrame:
    """Mean reversion: in an uptrend, buy a sharp short-term dip; sell when price recovers above a short average."""
    out = _base(df)
    r = rsi(out["close"], rsi_n)
    t = sma(out["close"], trend)
    out["entry"] = (out["close"] > t) & (r < buy_below)
    out["exit"] = out["close"] > sma(out["close"], exit_sma)
    out["score"] = (buy_below - r).fillna(0)                  # deeper dip first
    return out


def rs_rating(closes: pd.DataFrame) -> pd.DataFrame:
    """Relative strength percentile (1-100) across the universe each day, in the style of
    O'Neil/IBD and Minervini: 40% weight on the 3-month return, 20% each on 6, 9 and 12 months."""
    raw = (0.4 * closes.pct_change(63) + 0.2 * closes.pct_change(126) + 0.2 * closes.pct_change(189)
           + 0.2 * closes.pct_change(252))
    return raw.rank(axis=1, pct=True) * 100


def _rs(out: pd.DataFrame, rs) -> pd.Series:
    return rs.reindex(out.index) if rs is not None else pd.Series(100.0, index=out.index)


def minervini_breakout(df: pd.DataFrame, rs=None, base_days: int = 20, max_base_pct: float = 15,
                       vol_mult: float = 1.4, rs_min: float = 70) -> pd.DataFrame:
    """Minervini SEPA, simplified: full Trend Template + RS >= 70, then a breakout above a tight
    `base_days` base (range <= max_base_pct%) on volume >= vol_mult x the 50-day average.
    Exit on a close below the 50-day average. Run with an 8% stop (stop_pct) as Minervini does."""
    out = _base(df)
    c = out["close"]
    s50, s150, s200 = sma(c, 50), sma(c, 150), sma(c, 200)
    hi52, lo52 = c.rolling(252).max(), c.rolling(252).min()
    template = ((c > s150) & (c > s200) & (s150 > s200) & (s200 > s200.shift(21)) & (s50 > s150)
                & (c > s50) & (c >= 1.3 * lo52) & (c >= 0.75 * hi52))
    r = _rs(out, rs)
    top = out["high"].rolling(base_days).max().shift(1)
    bottom = out["low"].rolling(base_days).min().shift(1)
    tight = (top - bottom) / top <= max_base_pct / 100
    vol = out["volume"] if "volume" in out else pd.Series(1.0, index=out.index)
    vol_ok = vol >= vol_mult * vol.rolling(50).mean().shift(1) if "volume" in out else True
    out["entry"] = template & (r >= rs_min) & tight & (c > top) & vol_ok
    out["exit"] = c < s50
    out["score"] = r.fillna(0)
    return out


def weinstein_stage2(df: pd.DataFrame, rs=None, ma_weeks: int = 30, vol_mult: float = 1.5) -> pd.DataFrame:
    """Weinstein Stage 2: a weekly close crossing above a rising 30-week average on above-average
    weekly volume. Exit on a weekly close back below the average. Signals use Friday's close."""
    out = _base(df)
    src = out if "volume" in out else out.assign(volume=1.0)
    wk = src.resample("W-FRI").agg({"close": "last", "volume": "sum"}).dropna()
    last_day = out.index.to_series().resample("W-FRI").last().dropna()
    ma = wk["close"].rolling(ma_weeks).mean()
    above = wk["close"] > ma
    entry_w = (above & ~above.shift(1, fill_value=True) & (ma > ma.shift(4))
               & (wk["volume"] >= vol_mult * wk["volume"].rolling(10).mean().shift(1)))
    out["entry"] = False
    out["exit"] = False
    for col, flags in (("entry", entry_w), ("exit", ~above & ma.notna())):
        days = last_day.reindex(flags.index)[flags.values].dropna()
        out.loc[out.index.isin(days.values), col] = True
    out["score"] = _rs(out, rs).fillna(0)
    return out


def leader_pullback(df: pd.DataFrame, rs=None, rs_min: float = 80) -> pd.DataFrame:
    """Buy a pullback in a market leader (O'Neil / Qullamaggie style): RS >= 80, above a rising
    50-day average, dipping to the 20-day EMA. Exit on a new 10-day closing high or a close below
    the 50-day average (run with max_hold 15)."""
    out = _base(df)
    c = out["close"]
    e20, s50 = c.ewm(span=20, adjust=False).mean(), sma(c, 50)
    r = _rs(out, rs)
    out["entry"] = (r >= rs_min) & (c > s50) & (s50 > s50.shift(10)) & (out["low"] <= e20) & (c > e20 * 0.97)
    out["exit"] = (c >= c.rolling(10).max().shift(1)) | (c < s50)
    out["score"] = r.fillna(0)
    return out


STRATEGIES: Dict[str, Callable[..., pd.DataFrame]] = {
    "sma_trend": sma_trend,
    "donchian_breakout": donchian_breakout,
    "rsi2_pullback": rsi2_pullback,
    "minervini_breakout": minervini_breakout,
    "weinstein_stage2": weinstein_stage2,
    "leader_pullback": leader_pullback,
}
NEEDS_RS = {"minervini_breakout", "weinstein_stage2", "leader_pullback"}
# engine settings that are part of a method's published rules
ENGINE_DEFAULTS = {"minervini_breakout": {"stop_pct": 8.0}, "leader_pullback": {"max_hold": 15}}

DESCRIPTIONS = {
    "sma_trend": "Trend: 50/200-day moving-average crossover",
    "donchian_breakout": "Breakout: new 50-day high above 200-day average; exit on 20-day low",
    "rsi2_pullback": "Pullback: RSI(2) dip below 10 in an uptrend; exit above 5-day average",
    "minervini_breakout": "Minervini SEPA: trend template + RS>=70, tight-base breakout on volume, 8% stop, exit below 50-day",
    "weinstein_stage2": "Weinstein Stage 2: weekly close above rising 30-week average on volume; exit below it",
    "leader_pullback": "Leader pullback: RS>=80 stock dips to 20-day EMA; exit at new 10-day high or below 50-day",
}
