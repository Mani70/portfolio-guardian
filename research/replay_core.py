"""Addendum 13: replay the real engine (CoreAllocation + Engine._allocate + risk checks + paper broker with real costs)
over 2005-2026 on the same sleeve prices as research/allocation20.py, and compare with the research numbers.
Pass (pre-registered): CAGR within 1 point a year of the research in A and in B.

  python research/replay_core.py [--hist research/data/hist]
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import allocation20 as A                                             # noqa: E402
import history20 as H                                                # noqa: E402
from trader import config as C                                       # noqa: E402
from trader.brokers.base import CostModel                            # noqa: E402
from trader.brokers.paper import PaperBroker                         # noqa: E402
from trader.engine import Engine                                     # noqa: E402
from trader.journal import Journal                                   # noqa: E402
from trader.market import ReplayMarket                               # noqa: E402
from trader.strategies.allocation import CoreAllocation              # noqa: E402

SYM = {"N50": "NIFTYBEES", "NN50": "JUNIORBEES", "MON100": "MON100", "GOLD": "GOLDBEES", "LIQ": "LIQUIDCASE"}


def frames_from(rets: pd.DataFrame) -> dict:
    out = {}
    for k, sym in SYM.items():
        r = rets[k]
        first = r.first_valid_index() if k != "LIQ" else r.index[0]
        lv = (1 + r.loc[first:].fillna(0)).cumprod() * 100
        out[sym] = pd.DataFrame({"open": lv, "high": lv, "low": lv, "close": lv, "volume": 1e6})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    ap.add_argument("--tilt", action="store_true", help="L1 with the valuation tilt (V1, Addendum 16a)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    rets = A.load_sleeves(Path(a.hist))
    weights = {SYM[k]: v for k, v in A.L1.items()}
    params = {"weights": weights}
    if a.tilt:
        import allweather16 as W
        import yaml
        rule = W.tilt(rets, W.valuation(Path(a.hist)), "div_yield", 20, False)
        rb = yaml.safe_load((ROOT / "trader" / "rulebook.yaml").read_text(encoding="utf-8"))
        params["valuation"] = {**rb["rules"]["L1"]["valuation"], "_dir": a.hist}   # the live rule's own settings
    else:
        rule = A.fixed_mix(rets, A.L1)
    research = A.simulate(rets, rule, pd.Timestamp("2005-12-01"))["equity"]
    cfg = C._merge(C.DEFAULTS, {
        "capital": {"swing": A.CAPITAL, "intraday": 0},
        "limits": {"max_orders_per_day": 100},
        "strategies": {"core": {"enabled": True, "class": "core_allocation", "max_order_value": 1e12,
                                "params": params}}})
    cfg["_path"] = "replay"
    daily = frames_from(rets)
    market = ReplayMarket(daily, {}, close_fill=set(daily))
    strat = CoreAllocation("core", params)
    strat.sessions = market.sessions
    tmp = Path(tempfile.mkdtemp())
    j = Journal(tmp / "replay.db", "paper")
    eng = Engine(cfg, j, PaperBroker(market, CostModel()), market, None, None, [strat], tmp, [], "NIFTYBEES",
                 quiet=True)
    eq = {}
    sessions = [d for d in market.sessions if d >= date(2005, 12, 1)]
    for i, d in enumerate(sessions):
        if i % 500 == 0:
            print(f"  {d:%d %b %Y}", flush=True)
        eng.swing_check(datetime.combine(d, time(9, 30)))
        eng.swing_plan(datetime.combine(d, time(15, 45)))
        eq[pd.Timestamp(d)] = eng.risk.planned_capital("core", "swing")
    engine = pd.Series(eq).sort_index()
    rs, es = H.stats(research), H.stats(engine)
    print("\n                 research (allocation20)        engine replay")
    ok = True
    for p in ("A", "B"):
        diff = es[f"cagr_{p}"] - rs[f"cagr_{p}"]
        ok &= abs(diff) <= 1.0
        print(f"  {p}: CAGR {rs[f'cagr_{p}']:6.2f}% / fall {rs[f'dd_{p}']:6.1f}%    "
              f"{es[f'cagr_{p}']:6.2f}% / {es[f'dd_{p}']:6.1f}%   (diff {diff:+.2f} points)")
    orders = j.load_orders()
    print(f"\n  engine: {len(orders)} orders, {len(j.trades())} closed trades (partial sells), "
          f"final value Rs {engine.iloc[-1]:,.0f}")
    print(f"  -> replay {'MATCHES' if ok else 'DOES NOT MATCH'} the research within 1 point a year "
          f"({'Addendum 16a, V1' if a.tilt else 'Addendum 13, L1'})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
