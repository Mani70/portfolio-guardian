"""Daily price history from INDstocks, cached as one CSV per symbol.

API limits that shape this module (from the INDstocks docs):
- /market/historical/1day returns at most 1 year per call and at most 5 scrip codes per call.
- Longer history = more calls, one per 1-year window, walking backwards.
- Candle `ts` is the candle OPEN time in epoch seconds; request times are epoch milliseconds.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)

WINDOW_DAYS = 364
COLUMNS = ["open", "high", "low", "close", "volume"]


def _ist():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Kolkata")


def candles_to_frame(candles, intraday: bool = False) -> pd.DataFrame:
    """Candle objects -> DataFrame indexed by trading date, or by bar-open time (IST, naive) if intraday."""
    if not candles:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"))
    ist = _ist()
    stamp = (lambda t: datetime.fromtimestamp(t, ist).replace(tzinfo=None)) if intraday else \
            (lambda t: datetime.fromtimestamp(t, ist).date())
    rows = [(stamp(c.ts), c.o, c.h, c.l, c.c, c.v) for c in candles]
    df = pd.DataFrame(rows, columns=["date"] + COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates("date", keep="last").set_index("date").sort_index()


class PriceStore:
    """CSV cache under cache/<sub>/. One file per symbol plus a small meta file."""

    def __init__(self, root: Path, sub: str = "history"):
        self.dir = root / "cache" / sub
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "_meta.json"
        try:
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.meta = {}

    def _path(self, symbol: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)
        return self.dir / f"{safe}.csv"

    def load(self, symbol: str) -> pd.DataFrame:
        p = self._path(symbol)
        if not p.exists():
            return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"))
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        return df[COLUMNS].astype(float)

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df[COLUMNS].to_csv(self._path(symbol), index_label="date")

    def merge(self, symbol: str, new: pd.DataFrame) -> pd.DataFrame:
        old = self.load(symbol)
        df = pd.concat([old, new]) if len(old) else new
        df = df[~df.index.duplicated(keep="last")].sort_index()
        self.save(symbol, df)
        return df

    def save_meta(self) -> None:
        self.meta_path.write_text(json.dumps(self.meta, indent=1), encoding="utf-8")


def last_completed_session(now: datetime):
    """Most recent weekday whose session has closed (15:30 IST). Exchange holidays aren't known
    here, so on a holiday this costs one small extra download, nothing more."""
    d = now.date()
    if now.weekday() >= 5 or (now.hour, now.minute) < (15, 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _windows(start: datetime, end: datetime, days: int = WINDOW_DAYS) -> List[Tuple[datetime, datetime]]:
    """Windows of at most `days` walking backwards from end to start."""
    out = []
    w_end = end
    while w_end > start:
        w_start = max(start, w_end - timedelta(days=days))
        out.append((w_start, w_end))
        w_end = w_start
    return out


def update_history(client, symbol_codes: Dict[str, str], years: float, store: PriceStore,
                   refresh: bool = False, now: Optional[datetime] = None, interval: str = "1day",
                   window_days: int = WINDOW_DAYS) -> Dict[str, pd.DataFrame]:
    """Make sure each symbol's cache covers `years` of history up to today; return the frames.

    symbol_codes: {"RELIANCE": "NSE_2885", ...}
    interval/window_days: "1day" with 364-day windows, or e.g. "5minute" with 7-day windows.
    """
    intraday = interval != "1day" and interval.endswith("minute")
    now = now or datetime.now(_ist())
    start = now - timedelta(days=int(years * 365.25))
    need: Dict[str, datetime] = {}
    for sym in symbol_codes:
        df = store.load(sym)
        meta = store.meta.get(sym, {})
        if refresh or df.empty:
            need[sym] = start
            continue
        first, last = df.index[0], df.index[-1]
        covers_head = meta.get("history_start_reached") or first <= pd.Timestamp(start.date()) + pd.Timedelta(days=10)
        if not covers_head:
            need[sym] = start
        elif last.date() < last_completed_session(now):
            # re-fetch from the start of the last cached day, in case it was saved mid-session
            need[sym] = last.normalize().to_pydatetime().replace(tzinfo=now.tzinfo)
    if need:
        log.info("Downloading %s history for %d symbols (this can take a minute)", interval, len(need))
    syms = sorted(need, key=lambda s: need[s])
    n_batches = (len(syms) + 4) // 5
    # walking backwards, stop once this many consecutive windows come back empty (no older data)
    empty_limit = max(1, 28 // window_days)
    for i in range(0, len(syms), 5):  # API: max 5 scrip codes per call
        batch = syms[i:i + 5]
        if n_batches > 4 and (i // 5) % max(1, n_batches // 10) == 0:
            log.info("  batch %d of %d", i // 5 + 1, n_batches)
        batch_start = min(need[s] for s in batch)
        collected: Dict[str, list] = {s: [] for s in batch}
        code_to_sym = {symbol_codes[s]: s for s in batch}
        empty, got_any = 0, False
        for w_start, w_end in _windows(batch_start, now, window_days):
            got = client.candles_many(list(code_to_sym), interval,
                                      int(w_start.timestamp() * 1000), int(w_end.timestamp() * 1000))
            n = 0
            for code, candles in got.items():
                if code in code_to_sym:
                    collected[code_to_sym[code]].extend(candles)
                    n += len(candles)
            if n:
                empty, got_any = 0, True
            else:
                empty += 1
                if got_any and empty >= empty_limit:
                    break
        for s in batch:
            df = store.merge(s, candles_to_frame(collected[s], intraday))
            if need[s] == start and not df.empty and df.index[0] > pd.Timestamp(start.date()) + pd.Timedelta(days=10):
                store.meta.setdefault(s, {})["history_start_reached"] = True  # listed later than `start`
        store.save_meta()

    cutoff = pd.Timestamp(start.date())
    return {s: store.load(s).loc[cutoff:] for s in symbol_codes}


def suspicious_jumps(df: pd.DataFrame, threshold: float = 0.35) -> List[Tuple[pd.Timestamp, float]]:
    """Day-over-day close changes beyond threshold: often an unadjusted split or bonus."""
    if len(df) < 2:
        return []
    ch = df["close"].pct_change().dropna()
    hits = ch[ch.abs() > threshold]
    return [(d, float(v)) for d, v in hits.items()]


# price ratios left by common bonuses/splits (1:1 bonus = 1/2, 1:2 bonus = 2/3, 1:5 split = 1/5, ...)
SPLIT_RATIOS = (1 / 2, 1 / 3, 2 / 3, 1 / 4, 3 / 4, 1 / 5, 2 / 5, 3 / 5, 1 / 10)


def adjust_splits(df: pd.DataFrame, tol: float = 0.015) -> Tuple[pd.DataFrame, List[Tuple[pd.Timestamp, float]]]:
    """Back-adjust unadjusted bonus/split boundaries. INDstocks adjusts old prices only so far back,
    so a long history can contain a day where price jumps by a split ratio. A day counts when both the
    close-to-close move and the opening gap sit within `tol` of a ratio in SPLIT_RATIOS (or its inverse,
    for consolidations). Earlier prices are multiplied by the ratio and volumes divided by it."""
    if len(df) < 2:
        return df, []
    out = df.copy()
    found = []
    ratios = out["close"] / out["close"].shift(1)
    gaps = out["open"] / out["close"].shift(1)
    candidates = ratios.index[((ratios - 1).abs() > 0.15).fillna(False).values]
    for d in candidates[::-1]:                            # latest first, so adjustments compound correctly
        r, g = ratios[d], gaps[d]
        # a >45% overnight jump in either direction with a matching opening gap is a corporate action
        # (split, bonus, demerger), not a market move: large caps have no such days otherwise
        big = (r < 0.55 or r > 1.8) and abs(g / r - 1) <= 0.05
        for f in ((r,) if big else SPLIT_RATIOS + tuple(1 / x for x in SPLIT_RATIOS)):
            if abs(r / f - 1) <= tol and abs(g / f - 1) <= tol * 2:
                before = out.index < d
                out.loc[before, ["open", "high", "low", "close"]] *= f
                if "volume" in out:
                    out.loc[before, "volume"] /= f
                found.append((d, f))
                ratios = out["close"] / out["close"].shift(1)
                gaps = out["open"] / out["close"].shift(1)
                break
    return out, found


def resolve_codes(symbols: Iterable[str], index: Dict[str, Dict[str, str]], exchange: str = "NSE"
                  ) -> Tuple[Dict[str, str], List[str]]:
    """Map trading symbols to scrip codes using the instrument master."""
    codes, missing = {}, []
    for s in symbols:
        sid = index.get(exchange, {}).get(s.upper())
        if sid:
            codes[s.upper()] = f"{exchange}_{sid}"
        else:
            missing.append(s)
    return codes, missing
