"""Historical replay: run the real engine (strategies, risk checks, paper broker, journal) day by day,
bar by bar, over cached history. This is the end-to-end test that the live code does what the
research tested.

  python -m trader.replay --start 2025-01-01 --end 2026-10-01
  python -m trader.replay --strategy momentum_rotation --start 2015-01-01
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from backtest.data import adjust_splits

from . import config as C
from .brokers.base import CostModel
from .brokers.paper import PaperBroker
from .engine import Engine
from .journal import Journal
from .market import ReplayMarket
from .strategies import build

ROOT = C.ROOT


def _file(symbol: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)


def load_cache(symbols: List[str], intraday: bool, root: Path = ROOT):
    daily, intra = {}, {}
    for s in symbols:
        p = root / "cache" / "history" / f"{_file(s)}.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
            daily[s] = adjust_splits(df)[0]
        if intraday:
            q = root / "cache" / "intraday_5m" / f"{_file(s)}.csv"
            if q.exists():
                intra[s] = pd.read_csv(q, parse_dates=["date"]).set_index("date").sort_index()
    return daily, intra


def bar_times(d: date):
    t = datetime.combine(d, time(9, 15))
    for k in range(1, 75):
        yield t + timedelta(minutes=5 * k, seconds=15)


def replay(cfg: dict, start: date, end: date, journal_path: Optional[Path] = None, root: Path = ROOT,
           market: Optional[ReplayMarket] = None, progress: bool = False) -> Journal:
    strategies = build(cfg)
    universe = C.universe(cfg, root, nse=False)       # the research list, not today's downloaded one
    bench = cfg.get("benchmark", "NIFTYBEES")
    need_intra = any(s.engine == "intraday" for s in strategies)
    symbols = set(universe) | {bench}
    for s in strategies:
        symbols |= set(s.symbols(universe))
    for sc in (cfg.get("strategies") or {}).values():          # liquid ETFs that strategies park idle cash in
        pc = (sc or {}).get("park")
        sym = pc if isinstance(pc, str) else (pc or {}).get("symbol")
        if sym:
            symbols.add(str(sym).upper())
    if market is None:
        daily, intra = load_cache(sorted(symbols), need_intra, root)
        etfs = {s for s in daily if s.endswith("BEES") or s in ("MON100", "CPSEETF", "MAFANG")}
        market = ReplayMarket(daily, intra if need_intra else {}, close_fill=etfs)
    for s in strategies:
        s.sessions = market.sessions
    jp = journal_path or Path(tempfile.mkdtemp()) / "replay.db"
    journal = Journal(jp, "paper")
    broker = PaperBroker(market, CostModel())
    eng = Engine(cfg, journal, broker, market, None, None, strategies, root, universe, bench, quiet=True)
    sessions = [d for d in market.sessions if start <= d <= end]
    for i, d in enumerate(sessions):
        if progress and i % 250 == 0:
            print(f"  {d:%d %b %Y}")
        if eng.swing_strategies():
            eng.swing_check(datetime.combine(d, time(9, 30)))
        if eng.intraday_strategies():
            eng.intraday_start(d, datetime.combine(d, time(9, 10)))
            for t in bar_times(d):
                eng.intraday_step(t)
                if eng._squared and t.time() >= time(15, 15):
                    break
            eng.intraday_end(datetime.combine(d, time(15, 30)))
        if eng.swing_strategies():
            eng.swing_plan(datetime.combine(d, time(15, 45)))
    return journal


def report(journal: Journal, capital: float) -> pd.DataFrame:
    from .gates import stats
    t = journal.trades()
    rows = []
    for name, g in (t.groupby("strategy") if len(t) else []):
        s = stats(g)
        rows.append(dict(strategy=name, trades=s["trades"], net=round(s["net"]), mean=round(s["mean"], 1),
                         win_pct=round(s["win"], 1), t_daily=round(s["t"], 2)))
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay the trading engine over cached history (paper fills)")
    ap.add_argument("--config")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=None)
    ap.add_argument("--strategy", action="append", help="only these strategies (repeatable)")
    ap.add_argument("--out", help="keep the replay journal here (SQLite)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    cfg = C.load(Path(args.config) if args.config else None)
    if args.strategy:
        for name, sc in cfg["strategies"].items():
            sc["enabled"] = name in args.strategy
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    j = replay(cfg, start, end, Path(args.out) if args.out else None, progress=True)
    print(report(j, sum(cfg["capital"].values())).to_string(index=False))
    print(f"\nJournal: {j.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
