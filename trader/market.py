"""Price data for the engine. LiveMarket asks INDstocks; ReplayMarket plays back the cache with a clock,
so the exact same engine code can be tested on history before it ever runs live.

All times are naive IST. A 5-minute bar stamped 10:00 covers 10:00-10:05 and is usable from 10:05.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

log = logging.getLogger(__name__)
BAR = timedelta(minutes=5)
CLOSE = time(15, 30)


def completed(df: pd.DataFrame, since: Optional[datetime], now: datetime) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    m = df.index + BAR <= now
    if since is not None:
        m &= df.index >= since
    return df[m]


class ReplayMarket:
    def __init__(self, daily: Dict[str, pd.DataFrame], intraday: Dict[str, pd.DataFrame],
                 close_fill: Iterable[str] = ()):
        self.daily_frames = daily
        self.intra = intraday
        # instruments whose daily OPEN is unreliable (ETFs often print yesterday's close): without 5-minute
        # bars, fill their opening orders at that day's close instead (the conservative choice)
        self.close_fill = set(close_fill)
        days = set()
        for df in (intraday or daily).values():
            days |= set(df.index.normalize().date)
        self.sessions: List[date] = sorted(days)
        self._by_day: Dict[str, Dict[date, pd.DataFrame]] = {}

    def _day(self, symbol: str, d: date) -> pd.DataFrame:
        cache = self._by_day.setdefault(symbol, {})
        if d not in cache:
            df = self.intra.get(symbol)
            if df is None or df.empty:
                cache[d] = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            else:
                start = pd.Timestamp(d)
                cache[d] = df.loc[start:start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)]
        return cache[d]

    def today_bars(self, symbols: Iterable[str], now: datetime) -> Dict[str, pd.DataFrame]:
        return {s: completed(self._day(s, now.date()), None, now) for s in symbols}

    def bars(self, symbol: str, since: datetime, now: datetime) -> pd.DataFrame:
        if since.date() == now.date():
            return completed(self._day(symbol, now.date()), since, now)
        df = self.intra.get(symbol)
        return completed(df.loc[pd.Timestamp(since):pd.Timestamp(now)] if df is not None else None, since, now)

    def ltp(self, symbols: Iterable[str], now: datetime) -> Dict[str, float]:
        out = {}
        for s in symbols:
            b = completed(self._day(s, now.date()), None, now)
            if len(b):
                out[s] = float(b["close"].iloc[-1])
                continue
            d = self.daily_frames.get(s)
            if d is None:
                continue
            today = pd.Timestamp(now.date())
            if time(9, 15) <= now.time() < CLOSE and today in d.index:
                # no 5-minute bars: during the session the best daily proxy is today's open
                # (its close for instruments whose daily open is unreliable)
                out[s] = float(d.loc[today, "close" if s in self.close_fill else "open"])
                continue
            prior = d.loc[:today - pd.Timedelta(days=1)] if now.time() < CLOSE else d.loc[:today]
            if len(prior):
                out[s] = float(prior["close"].iloc[-1])
        return out

    def daily(self, symbols: Iterable[str], now: datetime) -> Dict[str, pd.DataFrame]:
        cut = pd.Timestamp(now.date()) if now.time() >= CLOSE else pd.Timestamp(now.date()) - pd.Timedelta(days=1)
        return {s: self.daily_frames[s].loc[:cut] for s in symbols if s in self.daily_frames}

    def day_open(self, symbol: str, d: date) -> Optional[float]:
        b = self._day(symbol, d)
        if len(b):
            return float(b["open"].iloc[0])
        df = self.daily_frames.get(symbol)
        if df is not None and pd.Timestamp(d) in df.index:
            return float(df.loc[pd.Timestamp(d), "close" if symbol in self.close_fill else "open"])
        return None

    def next_session_after(self, t: datetime) -> Optional[date]:
        later = [d for d in self.sessions if d > t.date()]
        return later[0] if later else None

    def is_session(self, d: date) -> bool:
        return d in set(self.sessions)


class LiveMarket:
    """INDstocks data. Daily history comes from the research cache (topped up); today's 5-minute
    bars are fetched at most once per bar."""

    def __init__(self, client, instruments, root: Path):
        self.client = client
        self.inst = instruments
        self.root = Path(root)
        self._bars: Dict[str, pd.DataFrame] = {}
        self._slot: Dict[str, datetime] = {}       # symbol -> 5-minute slot its bars were fetched in
        self._daily: Dict[str, pd.DataFrame] = {}

    def _codes(self, symbols) -> Dict[str, str]:
        out = {}
        for s in symbols:
            sid = self.inst.security_id(s)
            if sid:
                out[s] = f"NSE_{sid}"
        return out

    def daily(self, symbols: Iterable[str], now: datetime, years: float = 2.0) -> Dict[str, pd.DataFrame]:
        from backtest.data import PriceStore, update_history
        frames = update_history(self.client, self._codes(symbols), years, PriceStore(self.root))
        cut = pd.Timestamp(now.date()) if now.time() >= CLOSE else pd.Timestamp(now.date()) - pd.Timedelta(days=1)
        self._daily = {s: f.loc[:cut] for s, f in frames.items()}
        if now.time() >= CLOSE and now.weekday() < 5:
            self._fill_today(now)
        return self._daily

    def _fill_today(self, now: datetime) -> None:
        """INDstocks may publish the day's daily candle only some time after the close. Until then, build today's
        candle from its 5-minute bars (kept in memory only; the published candle replaces it in the cache later).
        No 5-minute bars (an exchange holiday) = no candle, so the evening run still skips holidays."""
        today = pd.Timestamp(now.date())
        stale = [s for s, f in self._daily.items() if len(f) and f.index[-1] < today]
        if not stale:
            return
        try:
            bars = self.today_bars(stale, now)
        except Exception as e:                                   # noqa: BLE001
            log.warning("Today's 5-minute bars unavailable (%s); daily data ends %s", e, self._daily[stale[0]].index[-1].date())
            return
        built = []
        for s in stale:
            b = bars.get(s)
            if b is None or len(b) < 12:                         # not a real session's worth of bars
                continue
            row = pd.DataFrame({"open": [float(b["open"].iloc[0])], "high": [float(b["high"].max())],
                                "low": [float(b["low"].min())], "close": [float(b["close"].iloc[-1])],
                                "volume": [float(b["volume"].sum())]}, index=pd.DatetimeIndex([today], name="date"))
            self._daily[s] = pd.concat([self._daily[s], row[self._daily[s].columns]])
            built.append(s)
        if built:
            log.warning("Today's daily candle not published yet for %d symbols; built from 5-minute bars", len(built))

    def today_bars(self, symbols: Iterable[str], now: datetime) -> Dict[str, pd.DataFrame]:
        slot = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        symbols = list(symbols)
        stale = [s for s in symbols if self._slot.get(s) != slot]
        if stale:
            from backtest.data import candles_to_frame
            start = datetime.combine(now.date(), time(9, 15))
            codes = self._codes(stale)
            back = {c: s for s, c in codes.items()}
            got = self.client.candles_many(list(codes.values()), "5minute",
                                           int(_epoch_ms(start)), int(_epoch_ms(now)))
            for code, candles in got.items():
                if code in back:
                    self._bars[back[code]] = candles_to_frame(candles, intraday=True)
            for s in stale:
                self._slot[s] = slot
        return {s: completed(self._bars.get(s), None, now) for s in symbols}

    def bars(self, symbol: str, since: datetime, now: datetime) -> pd.DataFrame:
        return completed(self.today_bars([symbol], now).get(symbol), since, now)

    def ltp(self, symbols: Iterable[str], now: datetime) -> Dict[str, float]:
        codes = self._codes(symbols)
        back = {c: s for s, c in codes.items()}
        return {back[c]: p for c, p in self.client.ltp(list(codes.values())).items() if c in back}

    def day_open(self, symbol: str, d: date) -> Optional[float]:
        now = _now_ist()
        if d == now.date():
            b = self.today_bars([symbol], now).get(symbol)
            if b is not None and len(b):
                return float(b["open"].iloc[0])
            return None
        df = self._daily.get(symbol)
        if df is not None and pd.Timestamp(d) in df.index:
            return float(df.loc[pd.Timestamp(d), "open"])
        return None

    def set_holidays(self, days) -> None:
        """The engine passes NSE's list plus trader.yaml's `holidays:` (the same set the strategies use)."""
        self._hol = set(days)

    def _holidays(self) -> set:
        if getattr(self, "_hol", None) is None:
            from .holidays import load
            self._hol = load(self.root)                 # NSE's list; empty if never downloaded (weekdays only)
        return self._hol

    def next_session_after(self, t: datetime) -> Optional[date]:
        d = t.date() + timedelta(days=1)
        while d.weekday() >= 5 or d in self._holidays():
            d += timedelta(days=1)
        return d

    def is_session(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays()


def _now_ist() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)


def _epoch_ms(t: datetime) -> float:
    from zoneinfo import ZoneInfo
    return t.replace(tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1000
