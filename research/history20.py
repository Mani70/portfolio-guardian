"""Addendum 12 (research/PREREGISTRATION.md): the live rules and their candidates on ~20 years of NSE history.

  python research/history20.py [--hist research/data/hist] [--only momentum,etf,intraday]
Writes research/history20_results.csv and prints every table and verdict.

Period A (2006-2015) was never used to build any rule: it is the out-of-sample test. Period B (2016-2026) is where
the rules were built. Prices: NSE bhavcopy, adjusted for splits / bonuses / rights with NSE's own previous close.
Universe: each month the 50 (neighbour: 100) most traded stocks - no survivorship bias.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from bisect import bisect_right
from datetime import date
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
from backtest.intraday import IntradayCosts                         # noqa: E402
from backtest.metrics import cagr, max_drawdown, sharpe             # noqa: E402
from backtest.rotation import market_ok, simulate_rotation          # noqa: E402

A_START, A_END, B_START = pd.Timestamp("2006-01-01"), pd.Timestamp("2015-12-31"), pd.Timestamp("2016-01-01")
MOM_CAP, ETF_CAP, INTRA_VALUE = 345_000, 75_600, 40_000
ETF_LIVE = ["NIFTYBEES", "JUNIORBEES", "GOLDBEES", "MON100"]
NOT_STOCK = re.compile(r"BEES|ETF|^LIQUID|^GOLD|^SILVER|^MON100|^MAFANG|^MAHKTECH|^N100|^M50|^SETF|^NIFTY|"
                       r"^BSLNIFTY|^UTINIFTY|^HDFCNIFTY|^KOTAKNIFTY|^KOTAKGOLD|^ICICINIFTY|^ICICIGOLD|^SBIETF|"
                       r"BHARATBOND|^EBBETF|^CPSE|^MOM|^QNIFTY|^ABSL|^AXISNIFTY|^AXISGOLD|^LICNETF|^IDFNIFTY|"
                       r"^NETF|^JUNIOR|^BANKBEES|^PSUBNK|^INFRABEES|^SHARIABEES|^HNGSNG|^NV20|^QGOLD|^IVZIN")
# approximate overnight rate (% a year): 2005-2015 from RBI policy and liquidity conditions (call money), 2016 on
# as research/cash_parking.py (repo - 0.25; reverse repo 3.35% Apr 2020 - Apr 2022). Sensitivity: minus 1 point.
RATE_PATH = [(date(2005, 1, 1), 4.75), (date(2005, 10, 26), 5.25), (date(2006, 6, 8), 5.75), (date(2006, 10, 31), 6.25),
             (date(2007, 1, 31), 7.00), (date(2007, 8, 1), 6.00), (date(2008, 1, 1), 7.00), (date(2008, 6, 11), 8.25),
             (date(2008, 10, 20), 7.00), (date(2008, 12, 8), 5.50), (date(2009, 1, 5), 4.50), (date(2009, 4, 21), 3.25),
             (date(2010, 3, 19), 4.00), (date(2010, 7, 2), 5.25), (date(2010, 11, 2), 6.00), (date(2011, 3, 17), 6.50),
             (date(2011, 5, 3), 7.00), (date(2011, 7, 26), 7.75), (date(2011, 10, 25), 8.25), (date(2012, 4, 17), 7.75),
             (date(2013, 1, 29), 7.50), (date(2013, 5, 3), 7.00), (date(2013, 7, 15), 9.00), (date(2013, 10, 29), 7.75),
             (date(2015, 1, 15), 7.50), (date(2015, 3, 4), 7.25), (date(2015, 6, 2), 7.00), (date(2015, 9, 29), 6.50),
             (date(2016, 4, 5), 6.25), (date(2016, 10, 4), 6.00), (date(2017, 8, 2), 5.75), (date(2018, 6, 6), 6.00),
             (date(2018, 8, 1), 6.25), (date(2019, 2, 7), 6.00), (date(2019, 4, 4), 5.75), (date(2019, 6, 6), 5.50),
             (date(2019, 8, 7), 5.15), (date(2019, 10, 4), 4.90), (date(2020, 3, 27), 4.00), (date(2020, 4, 17), 3.35),
             (date(2022, 4, 8), 3.75), (date(2022, 5, 4), 4.15), (date(2022, 6, 8), 4.65), (date(2022, 8, 5), 5.15),
             (date(2022, 9, 30), 5.65), (date(2022, 12, 7), 6.00), (date(2023, 2, 8), 6.25), (date(2025, 2, 7), 6.00),
             (date(2025, 4, 9), 5.75), (date(2025, 6, 6), 5.25), (date(2025, 12, 5), 5.00)]
_KEYS = [d for d, _ in RATE_PATH]
ROWS: List[dict] = []


def overnight(d) -> float:
    return RATE_PATH[max(0, bisect_right(_KEYS, pd.Timestamp(d).date()) - 1)][1]


# ---------------------------------------------------------------- data
def symbol_changes(hist: Path) -> Dict[str, tuple]:
    """old symbol -> (new symbol, date of change) from NSE's symbolchange.csv, if downloaded."""
    p = hist / "symbolchange.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, encoding="latin-1")
    cols = [c.lower() for c in df.columns]
    old = next((df.columns[i] for i, c in enumerate(cols) if "old" in c or "key" in c), df.columns[1])
    new = next((df.columns[i] for i, c in enumerate(cols) if "new" in c), df.columns[2])
    when = next((df.columns[i] for i, c in enumerate(cols) if "date" in c or "applicable" in c), df.columns[3])
    out = {}
    for o, n, w in zip(df[old], df[new], df[when]):
        try:
            out[str(o).strip().upper()] = (str(n).strip().upper(), pd.to_datetime(str(w).strip(), dayfirst=True))
        except (ValueError, TypeError):
            continue
    return out


def final_symbol(sym: str, changes: Dict[str, tuple]) -> str:
    seen = set()
    while sym in changes and sym not in seen:
        seen.add(sym)
        sym = changes[sym][0]
    return sym


def load_panel(hist: Path, fields=("open", "high", "low", "close", "prevclose", "value")) -> Dict[str, pd.DataFrame]:
    """Wide matrices (dates x symbols) of every field, renamed symbols joined, adjusted for corporate actions."""
    parts = []
    for p in sorted(hist.glob("equities_*.csv")):
        df = pd.read_csv(p, usecols=["date", "symbol", "series", *fields],
                         dtype={"symbol": "string", "series": "string"})
        parts.append(df)
    raw = pd.concat(parts, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["date"])
    changes = symbol_changes(hist)
    if changes:
        # rows of an old symbol BEFORE its change date belong to the company's final symbol (a symbol NSE later
        # gives to another company keeps its own later rows)
        groups = raw.groupby("symbol").indices
        sym = raw["symbol"].astype(object).to_numpy().copy()
        dts = raw["date"].to_numpy()
        for old, (_, when) in changes.items():
            pos = groups.get(old)
            if pos is None:
                continue
            pos = pos[dts[pos] < np.datetime64(when)]
            sym[pos] = final_symbol(old, changes)
        raw["symbol"] = sym
    raw["eq"] = (raw["series"] == "EQ").astype("int8")
    raw = raw.sort_values(["date", "symbol", "eq"]).drop_duplicates(["date", "symbol"], keep="last")
    panel = {f: raw.pivot(index="date", columns="symbol", values=f).astype("float32") for f in fields}
    panel["eq"] = raw.pivot(index="date", columns="symbol", values="eq").fillna(0).astype("int8")
    del raw
    return panel                                      # raw prices: adjust_prices() runs once the universe is known


# ---------------------------------------------------------------- corporate actions (Addendum 12a)
SPLIT_K = [2 / 3, 1 / 2, 2 / 5, 1 / 3, 1 / 4, 1 / 5, 1 / 10]
OFFICIAL_FROM = pd.Timestamp("2010-01-01")
_BONUS = re.compile(r"BONUS\s*(\d+)\s*:\s*(\d+)")
_FV = re.compile(r"\bFR(?:O)?M\s*R[SE]\.?\s*([\d.]+)\D*?\bTO\s*R[SE]\.?\s*([\d.]+)")


def purpose_factor(purpose: str) -> Optional[float]:
    """Price factor of one corporate action (prices before the ex-date are multiplied by it): BONUS a:b -> b/(a+b);
    face value FROM x TO y -> y/x; DEMERGER -> nan (taken from the ex-date's opening gap); anything else -> None."""
    p = " ".join(str(purpose).upper().split())
    m = _BONUS.search(p)
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        return int(m.group(2)) / (int(m.group(1)) + int(m.group(2)))
    m = _FV.search(p)
    if m and re.search(r"SPL|SUB|CONSOL|\bFV\b|FACE", p):
        x, y = float(m.group(1)), float(m.group(2))
        if x > 0 and y > 0 and x != y:
            return y / x
    if "DEMERGER" in p:
        return float("nan")
    return None


def official_events(hist: Path, changes: Dict[str, tuple]) -> pd.DataFrame:
    """symbol, ex_date, factor (nan = demerger) from NSE's Bc files, EQ rows, de-duplicated; symbols renamed like
    the prices (an old symbol before its change date belongs to the final symbol)."""
    p = hist / "corp_actions.csv"
    if not p.exists():
        return pd.DataFrame(columns=["symbol", "ex_date", "factor", "purpose"])
    ca = pd.read_csv(p, dtype=str)
    ca = ca[ca["series"] == "EQ"].drop_duplicates(["symbol", "ex_date", "purpose"])
    ca["factor"] = ca["purpose"].map(purpose_factor)
    ca = ca[ca["purpose"].map(purpose_factor).notna() | ca["purpose"].str.upper().str.contains("DEMERGER")].copy()
    ca["ex_date"] = pd.to_datetime(ca["ex_date"])

    def rename(r):
        ch = changes.get(r.symbol)
        return final_symbol(r.symbol, changes) if ch is not None and r.ex_date < ch[1] else r.symbol
    if changes and len(ca):
        ca["symbol"] = [rename(r) for r in ca.itertuples()]
    # the same split listed twice (revised wording) counts once
    return ca.drop_duplicates(["symbol", "ex_date", "factor"])[["symbol", "ex_date", "factor", "purpose"]]


def detected_events(o: pd.DataFrame, c: pd.DataFrame) -> pd.DataFrame:
    """Addendum 12a detection rule: k the nearest of SPLIT_K to g = open/previous close; an event when |g/k - 1| <= 3%
    and |close/previous close / k - 1| <= 8%."""
    prev = c.ffill().shift(1)
    g, r = (o / prev), (c / prev)
    ks = np.array(SPLIT_K)
    lg = np.nan_to_num(np.log(g.where(g > 0).to_numpy(dtype=float)), nan=0.0)   # no price: no event (g is nan)
    k = ks[np.argmin(np.abs(lg[..., None] - np.log(ks)), axis=-1)]
    with np.errstate(invalid="ignore"):
        hit = (np.abs(g.to_numpy(dtype=float) / k - 1) <= 0.03) & (np.abs(r.to_numpy(dtype=float) / k - 1) <= 0.08)
    i, j = np.nonzero(hit)
    return pd.DataFrame({"symbol": c.columns[j], "ex_date": c.index[i], "factor": k[i, j]})


def _session_index(dates: pd.DatetimeIndex, when: pd.Series) -> np.ndarray:
    return dates.searchsorted(when.to_numpy())               # an ex-date on a holiday -> the next session


def validate_detection(panel, official: pd.DataFrame, symbols: List[str]) -> bool:
    """Recall and precision of the detection rule against the official events (factor <= 2/3), 2010 on, within one
    session. Printed whatever it shows."""
    c, o = panel["close"][symbols], panel["open"][symbols].where(panel["open"][symbols] > 0)
    rows = c.index >= OFFICIAL_FROM
    det = detected_events(o, c)
    det = det[det.ex_date >= OFFICIAL_FROM]
    off = official[official.symbol.isin(symbols) & (official.ex_date >= OFFICIAL_FROM)
                   & (official.ex_date <= c.index[-1]) & (official.factor <= 2 / 3 + 1e-9)].copy()
    dates = c.index[rows]
    off["i"] = _session_index(dates, off.ex_date)
    det["i"] = _session_index(dates, det.ex_date)
    od = {}
    for s, i in zip(off.symbol, off.i):
        od.setdefault(s, []).append(i)
    dd = {}
    for s, i in zip(det.symbol, det.i):
        dd.setdefault(s, []).append(i)
    hit_off = sum(any(abs(i - x) <= 1 for x in dd.get(s, [])) for s, i in zip(off.symbol, off.i))
    hit_det = sum(any(abs(i - x) <= 1 for x in od.get(s, [])) for s, i in zip(det.symbol, det.i))
    recall = hit_off / len(off) if len(off) else float("nan")
    precision = hit_det / len(det) if len(det) else float("nan")
    small = official[official.symbol.isin(symbols) & (official.ex_date >= OFFICIAL_FROM) & (official.factor > 2 / 3)]
    ok = recall >= 0.9 and precision >= 0.9
    print(f"  detection rule on 2010-{c.index[-1]:%Y} ({len(symbols)} stocks/ETFs): official events (factor <= 2/3) "
          f"{len(off)}, found {hit_off} (recall {recall:.1%}); rule events {len(det)}, true {hit_det} "
          f"(precision {precision:.1%}) -> {'USED for 2005-2009' if ok else 'FAILS: period A becomes 2011-2015'}")
    print(f"  smaller official events (factor > 2/3, not detectable, unadjusted before 2010): {len(small)}")
    missed = off[[not any(abs(i - x) <= 1 for x in dd.get(s, [])) for s, i in zip(off.symbol, off.i)]]
    false = det[[not any(abs(i - x) <= 1 for x in od.get(s, [])) for s, i in zip(det.symbol, det.i)]]
    if len(missed):
        print("    missed e.g.: " + ", ".join(f"{r.symbol} {r.ex_date:%d %b %Y} ({r.purpose})" for r in missed.head(8).itertuples()))
    if len(false):
        print("    false e.g.: " + ", ".join(f"{r.symbol} {r.ex_date:%d %b %Y} x{r.factor:.3f}" for r in false.head(8).itertuples()))
    ROWS.append(dict(section="data", rule="detection rule validation", recall=recall, precision=precision,
                     official=len(off), detected=len(det), passes=bool(ok)))
    return ok


def adjust_prices(panel, official: pd.DataFrame, use_detection: bool) -> None:
    """Multiply prices before each ex-date by its factor: official events from 2010; detected events before 2010 when
    the rule passed; for the four live ETFs, detected events in any year the official file doesn't list one."""
    close, opn = panel["close"], panel["open"]
    dates, cols = close.index, close.columns
    factor = np.ones(close.shape)
    used = 0

    def put(sym, when, f):
        nonlocal used
        if sym not in cols:
            return
        j = cols.get_loc(sym)
        col = close.iloc[:, j]
        i = dates.searchsorted(when)
        while i < len(dates) and np.isnan(col.iat[i]):
            i += 1                                            # first session the stock traded on or after the ex-date
        if i <= 0 or i >= len(dates):
            return
        if np.isnan(f):                                       # demerger: the opening gap
            pc = col.iloc[:i].dropna()
            f = float(opn.iat[i, j] / pc.iat[-1]) if len(pc) and opn.iat[i, j] > 0 else 1.0
            if not 0.05 < f < 1.0:
                return
        factor[i, j] *= f
        used += 1

    off = official[official.ex_date >= OFFICIAL_FROM]
    for r in off.itertuples():
        put(r.symbol, r.ex_date, r.factor)
    det = detected_events(opn.where(opn > 0), close)
    listed = set(zip(off.symbol, off.ex_date))
    for r in det.itertuples():
        if r.ex_date < OFFICIAL_FROM and (use_detection or r.symbol in ETF_LIVE):
            put(r.symbol, r.ex_date, r.factor)
        elif r.ex_date >= OFFICIAL_FROM and r.symbol in ETF_LIVE and not any(
                s == r.symbol and abs((d - r.ex_date).days) <= 5 for s, d in listed):
            put(r.symbol, r.ex_date, r.factor)
    f = pd.DataFrame(factor, index=dates, columns=cols)
    cum = f.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)          # product of LATER factors
    for k in ("open", "high", "low", "close"):
        panel[k] = (panel[k] * cum).astype("float32")
    panel["factor"] = f.astype("float32")
    print(f"  {used} corporate actions applied")


def monthly_universe(panel, n: int) -> Dict[pd.Timestamp, List[str]]:
    """Each month-end: the n most traded EQ stocks (median daily value over 126 sessions, 252 sessions of history)."""
    value = panel["value"].where(panel["eq"] == 1)
    stocks = [c for c in value.columns if not NOT_STOCK.search(str(c))]
    value = value[stocks]
    med = value.rolling(126, min_periods=100).median()
    hist = panel["close"][stocks].notna().cumsum()
    dates = value.index
    ends = [dates[i] for i in range(len(dates) - 1) if dates[i].month != dates[i + 1].month]
    out = {}
    for d in ends:
        m = med.loc[d]
        ok = m[(hist.loc[d] >= 252) & m.notna() & panel["close"].loc[d, stocks].notna()]
        out[d] = list(ok.sort_values(ascending=False).index[:n])
    return out


def frames_for(panel, symbols) -> Dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        if s not in panel["close"].columns:
            continue
        df = pd.DataFrame({f: panel[f][s] for f in ("open", "high", "low", "close")}).dropna(subset=["close"])
        df["open"] = df["open"].where(df["open"] > 0, df["close"])
        df["high"] = df[["high", "open", "close"]].max(axis=1)
        df["low"] = df[["low", "open", "close"]].min(axis=1)
        out[s] = df.astype(float)
    return out


def load_index(hist: Path, keys: List[str]) -> pd.DataFrame:
    """One index's daily open/close: niftyindices history first, then NSE's daily index files (old and new)."""
    def norm(x):
        return re.sub(r"[\s\-]|s&p|cnx", "", str(x).lower()).replace("niftynifty", "nifty")
    frames = []
    for p in (hist / "index_history.csv", hist / "indices.csv", ROOT / "research" / "data" / "nse" / "indices.csv"):
        if p.exists():
            df = pd.read_csv(p, usecols=lambda c: c in ("date", "index", "open", "close"))
            df = df[df["index"].map(norm).isin(keys)]
            if len(df):
                df["date"] = pd.to_datetime(df["date"])
                frames.append(df.set_index("date")[["open", "close"]])
    manual = []                                       # niftyindices.com CSVs saved by hand (research/data/manual)
    for p in sorted((ROOT / "research" / "data" / "manual").glob("*.csv")):
        df = pd.read_csv(p, na_values=["-"], thousands=",")
        df.columns = [str(c).strip().lower() for c in df.columns]
        if {"index name", "date", "close"} <= set(df.columns) and df["index name"].map(norm).isin(keys).any():
            df = df[df["index name"].map(norm).isin(keys)]
            manual.append(pd.DataFrame({"open": df.get("open"), "close": df["close"]}).set_index(
                pd.to_datetime(df["date"], format="%d %b %Y")))
    if manual:
        frames.append(pd.concat(manual).sort_index().loc[lambda x: ~x.index.duplicated()])
    if not frames:
        return pd.DataFrame(columns=["open", "close"])
    out = frames[0]
    for f in frames[1:]:
        out = out.combine_first(f)                    # the first source wins where both have a day
    return out.sort_index()


# ---------------------------------------------------------------- helpers
def stats(eq: pd.Series) -> dict:
    a, b = eq.loc[A_START:A_END], eq.loc[B_START:]
    out = {}
    for tag, x in (("A", a), ("B", b), ("all", eq)):
        x = x.dropna()
        if len(x) > 20:
            out.update({f"cagr_{tag}": cagr(x), f"dd_{tag}": max_drawdown(x), f"sh_{tag}": sharpe(x)})
    return out


def show(section, rule, eq, extra=None):
    r = dict(section=section, rule=rule, **stats(eq), **(extra or {}))
    ROWS.append(r)
    print(f"  {rule:<58} A: {r.get('cagr_A', np.nan):6.2f}% / {r.get('dd_A', np.nan):6.1f}% / {r.get('sh_A', np.nan):5.2f}"
          f"   B: {r.get('cagr_B', np.nan):6.2f}% / {r.get('dd_B', np.nan):6.1f}% / {r.get('sh_B', np.nan):5.2f}",
          flush=True)
    return r


def verdict_strategy(name, r, bench):
    ok = r["sh_A"] > bench["sh_A"] and r["dd_A"] >= bench["dd_A"]
    print(f"  -> {name} in period A vs its benchmark: Sharpe {r['sh_A']:.2f} vs {bench['sh_A']:.2f}, worst fall "
          f"{r['dd_A']:.1f}% vs {bench['dd_A']:.1f}%: {'KEEP' if ok else 'FAILS'}")
    ROWS.append(dict(section="verdict", rule=f"{name} (live rule) in A", passes=bool(ok)))
    return ok


def verdict_variant(name, r, live):
    ok = all(r[f"sh_{p}"] > live[f"sh_{p}"] and r[f"cagr_{p}"] >= live[f"cagr_{p}"] - 0.5
             and r[f"dd_{p}"] >= live[f"dd_{p}"] - 2 for p in ("A", "B"))
    print(f"  -> {name}: {'REPLACES the live rule' if ok else 'not adopted'}")
    ROWS.append(dict(section="verdict", rule=name, passes=bool(ok)))
    return ok


# ---------------------------------------------------------------- momentum
def momentum_section(panel, hist, uni50, uni100):
    print("\nMOMENTUM (top 10 of the most-traded 50, keep while top 20, cash below the Nifty ETF 200-day average)")
    import rebalance_day                                                 # noqa: F401 - start:k / end:k review days
    import cash_parking as CP
    syms = sorted({s for u in uni100.values() for s in u})
    frames = frames_for(panel, syms + ["NIFTYBEES"])
    bench = frames.pop("NIFTYBEES")
    dates = bench.index[bench.index >= A_START]
    cache = {}

    def chooser(uni, extra_gate: Optional[Callable] = None, use_filter: bool = True):
        keys = sorted(uni)

        def choose(d, current):
            if (use_filter and not market_ok(bench, d)) or (extra_gate is not None and not extra_gate(d)):
                return []
            u = uni[keys[max(0, bisect_right(keys, d) - 1)]] if keys[0] <= d else []
            ck = (id(uni), d)
            if ck not in cache:
                cache[ck] = list(rot.momentum_scores({s: frames[s] for s in u if s in frames}, d).index)
            ranked = cache[ck]
            keep = [s for s in current if s in ranked[:20]]
            return keep + [s for s in ranked if s not in keep][:max(0, 10 - len(keep))]
        return choose

    costs = Costs(dp_per_sell=21.83)
    nb = (bench["close"].reindex(dates) / bench["close"].reindex(dates).iloc[0]) * MOM_CAP
    b = show("momentum", "benchmark: NIFTYBEES held (price, no dividends)", nb)
    live = show("momentum", "LIVE RULE (top-50 universe, last session)",
                simulate_rotation(frames, chooser(uni50), dates, MOM_CAP, 10, costs, 0.0).equity)
    keep = verdict_strategy("momentum", live, b)
    show("momentum", "neighbour: top-100 universe", simulate_rotation(frames, chooser(uni100), dates, MOM_CAP, 10,
                                                                   costs, 0.0).equity)
    show("momentum", "reference: no 200-day filter", simulate_rotation(frames, chooser(uni50, use_filter=False), dates,
                                                                     MOM_CAP, 10, costs, 0.0).equity)
    # (a) tranches
    eq = sum(simulate_rotation(frames, chooser(uni50), dates, MOM_CAP / 3, 10, costs, 0.0, ev).equity
             for ev in ("start:7", "start:14", "end:1"))
    verdict_variant("(a) 3 tranches", show("momentum", "(a) 3 tranches: 7th / 14th / last session", eq), live)
    # (b) Quality 30 check
    q = load_index(hist, ["nifty100quality30", "niftyquality30", "nsequality30", "quality30"])["close"].dropna()
    if len(q) and q.index.min() < pd.Timestamp("2010-01-01"):
        qgate = lambda d: (lambda c: len(c) < 150 or c.iloc[-1] > c.iloc[-150:].mean())(q.loc[:d])   # noqa: E731
        print(f"  Quality 30 history from {q.index.min():%b %Y} ({len(q)} days)")
        verdict_variant("(b) Quality 30 check", show("momentum", "(b) + Quality 30 below its 150-day average = cash",
                        simulate_rotation(frames, chooser(uni50, qgate), dates, MOM_CAP, 10, costs, 0.0).equity), live)
    else:
        print(f"  (b) Quality 30 check: index history only from "
              f"{q.index.min():%b %Y}" if len(q) else "  (b) Quality 30 check: no index history downloaded" + " - not testable in A")
        ROWS.append(dict(section="verdict", rule="(b) Quality 30 check", passes=None, note="no history for period A"))
    # (c) cash parking, with the live engine's one-session delay on buys funded by the parked cash
    path = lambda d: overnight(d) - 0.23                                   # noqa: E731
    CP.review_days = lambda ds: set(rot._rebalance_days(ds, "monthly"))
    live_t = CP.simulate(frames, chooser(uni50), dates, MOM_CAP, 10, costs, park=False, delay="live")["equity"]
    show("momentum", "live rule with the live engine's buy delay (no parking)", live_t)
    lt = stats(live_t)
    for label, fn in (("(c) cash parked", path), ("(c) cash parked, rate 1 point lower", lambda d: path(d) - 1)):
        r = show("momentum", label, CP.simulate(frames, chooser(uni50), dates, MOM_CAP, 10, costs, park=True,
                                                 delay="live", yield_fn=fn)["equity"])
        if "lower" in label:
            verdict_variant("(c) cash parking", r, lt)
    return keep


# ---------------------------------------------------------------- ETF trend
def etf_section(panel):
    print("\nETF TREND (NIFTYBEES, JUNIORBEES, GOLDBEES, MON100; each held above its 200-day average, monthly)")
    frames = frames_for(panel, ETF_LIVE)
    for a, f in frames.items():
        print(f"  {a}: prices from {f.index.min():%b %Y}")
    dates = frames["NIFTYBEES"].index
    dates = dates[dates >= A_START]

    def trend(d, current):
        out = []
        for a, f in frames.items():
            c = f["close"].loc[:d]
            if len(c) >= 200 and c.iloc[-1] > c.iloc[-200:].mean():
                out.append(a)
        return out

    def hold_all(d, current):
        return [a for a, f in frames.items() if len(f["close"].loc[:d]) >= 1]

    b = show("etf", "benchmark: hold every listed ETF (same slices)",
             simulate_rotation(frames, hold_all, dates, ETF_CAP, 4, ETF_COSTS, 0.0).equity)
    live = show("etf", "LIVE RULE", simulate_rotation(frames, trend, dates, ETF_CAP, 4, ETF_COSTS, 0.0).equity)
    return verdict_strategy("ETF trend", live, b)


# ---------------------------------------------------------------- intraday (daily bars)
def intraday_section(panel, hist, uni100):
    print("\nINTRADAY from daily bars (buy/sell at the open, out at the close or the stop; top-100 traded stocks)")
    syms = sorted({s for u in uni100.values() for s in u})
    o, h, l, c = (panel[f][syms].astype(float) for f in ("open", "high", "low", "close"))
    o = o.where(o > 0)
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max()
    atr = tr.rolling(14, min_periods=10).mean().shift(1)
    gap = o / pc - 1
    keys = sorted(uni100)
    member = pd.DataFrame(False, index=c.index, columns=syms)
    for i, k in enumerate(keys):
        nxt = keys[i + 1] if i + 1 < len(keys) else c.index[-1] + pd.Timedelta(days=1)
        rows = (c.index > k) & (c.index <= nxt)
        member.loc[rows, [s for s in uni100[k] if s in syms]] = True
    nifty = load_index(hist, ["nifty50", "nifty"])
    proxy = gap.where(member).median(axis=1)                            # where the index open is missing
    if len(nifty):
        idx_gap = (nifty["open"] / nifty["close"].shift(1) - 1).where(nifty["open"] > 0).reindex(c.index)
    else:
        idx_gap = pd.Series(np.nan, index=c.index)
    mkt = idx_gap.fillna(proxy)
    ic = IntradayCosts()
    cost = (ic.buy(INTRA_VALUE) + ic.sell(INTRA_VALUE)) / INTRA_VALUE + 2 * ic.slippage_pct / 100
    print(f"  round-trip cost {cost * 100:.3f}% on Rs {INTRA_VALUE:,} a trade; market gap from the Nifty 50 open where "
          f"available ({int(idx_gap.notna().sum())} days), else the median gap of the 100 stocks")

    def run(name, long_gap, short_gap, stop_atr, top, mkt_max):
        trades = []
        for d in c.index[c.index >= A_START]:
            g = gap.loc[d][member.loc[d]].dropna()
            if g.empty or pd.isna(mkt.loc[d]) or abs(mkt.loc[d]) > mkt_max:
                continue
            picks = []
            if long_gap is not None:
                picks += [(s, 1) for s in g[g <= -long_gap].index]
            if short_gap is not None:
                picks += [(s, -1) for s in g[g >= short_gap].index]
            picks = sorted(picks, key=lambda x: -abs(g[x[0]]))[:top]
            for s, side in picks:
                op, hi, lo, cl, a = o.at[d, s], h.at[d, s], l.at[d, s], c.at[d, s], atr.at[d, s]
                if not (op > 0 and cl > 0 and a > 0):
                    continue
                if side == 1:
                    stop = op - stop_atr * a
                    ex = stop if lo <= stop else cl
                    r = ex / op - 1
                else:
                    stop = op + stop_atr * a
                    ex = stop if hi >= stop else cl
                    r = op / ex - 1
                trades.append((d, r - cost))
        t = pd.DataFrame(trades, columns=["date", "ret"])
        if t.empty:
            print(f"  {name}: no trades")
            return
        day = t.groupby("date").ret.mean()
        res = {}
        for tag, lo_, hi_ in (("A", A_START, A_END), ("B", B_START, None), ("last3", c.index[-1] - pd.DateOffset(years=3), None)):
            x = day.loc[lo_:hi_] if hi_ is not None else day.loc[lo_:]
            n = int(t[(t.date >= lo_) & ((t.date <= hi_) if hi_ is not None else True)].shape[0])
            tt = x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else float("nan")
            res[tag] = (x.mean() * 100, tt, n)
        ok = all(res[k][0] > 0 and res[k][1] >= 2 and res[k][2] >= 300 for k in res)
        print(f"  {name}: " + "; ".join(f"{k} {v[0]:+.3f}%/day t {v[1]:.2f} ({v[2]} trades)" for k, v in res.items())
              + f" -> {'PASSES' if ok else 'not traded'}", flush=True)
        ROWS.append(dict(section="intraday", rule=name, passes=bool(ok),
                         **{f"{k}_{m}": v[i] for k, v in res.items() for i, m in enumerate(("pct_day", "t", "trades"))}))

    run("gap reversal: buy 3%+ gap-downs, stop 1 ATR (live paper rule)", 0.03, None, 1.0, 3, 0.01)
    run("  neighbour: 4% gaps", 0.04, None, 1.0, 3, 0.01)
    run("  neighbour: 2% gaps", 0.02, None, 1.0, 3, 0.01)
    run("gap fade both ways: 1%+ gaps, stop 0.5 ATR, top 10", 0.01, 0.01, 0.5, 10, 1.0)


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    ap.add_argument("--only", default="momentum,etf,intraday")
    a = ap.parse_args(argv)
    hist = Path(a.hist)
    only = set(a.only.split(","))
    print("Loading the bhavcopy panel ...", flush=True)
    panel = load_panel(hist)
    print(f"  {panel['close'].shape[0]} days {panel['close'].index.min():%d %b %Y} - {panel['close'].index.max():%d %b %Y}, "
          f"{panel['close'].shape[1]} symbols")
    uni50, uni100 = monthly_universe(panel, 50), monthly_universe(panel, 100)
    official = official_events(hist, symbol_changes(hist))
    tested = sorted({s for u in uni100.values() for s in u} | set(ETF_LIVE))
    use_detection = validate_detection(panel, official, [s for s in tested if s in panel["close"].columns])
    adjust_prices(panel, official, use_detection)
    if not use_detection:
        global A_START
        A_START = pd.Timestamp("2011-01-01")
    first = min(k for k, v in uni50.items() if v)
    print(f"  universe from {first:%b %Y}; e.g. {first:%b %Y}: {', '.join(uni50[first][:12])} ...")
    last = max(uni50)
    print(f"  {last:%b %Y}: {', '.join(uni50[last][:12])} ...")
    if "momentum" in only:
        momentum_section(panel, hist, uni50, uni100)
    if "etf" in only:
        etf_section(panel)
    if "intraday" in only:
        intraday_section(panel, hist, uni100)
    out = pd.DataFrame(ROWS)
    out.to_csv(ROOT / "research" / "history20_results.csv", index=False)
    print("\nWritten research/history20_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
