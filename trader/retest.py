"""Yearly re-test of the autopilot's rule (research/PREREGISTRATION.md Addendum 14).

  python -m trader.run retest            # January (scheduled: Saturdays in January, once a year)
  python -m trader.run retest --force    # any time, e.g. to see where the tests stand (still applies the rule)

Updates the research data (NSE bhavcopy, corporate actions, niftyindices total-return, 1D-rate and valuation histories)
up to the last session of December, re-runs the Addendum 13 simulation (L1 with its Addendum 16a valuation tilt),
and applies the pre-registered rule:
  test 1 (Jan 2006 to the end): L1's Sharpe above the Nifty 50 TRI's AND its worst fall shallower;
  test 2 (the last 5 calendar years): L1's Sharpe above the Nifty 50 TRI's.
  F when test 1 fails, or test 2 fails in two consecutive re-tests; back to L1 when both pass.
The choice goes to trader/state/autopilot.json; the next evening run trades the new weights through the bands.
A re-test that cannot get its data changes nothing and says so.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from .autopilot import load_state, now_iso_date, save_state

log = logging.getLogger("trader.retest")


def decide(state: dict, test1: bool, test2: bool) -> Tuple[str, int, str]:
    """(rule, consecutive test-2 failures, why) under Addendum 14."""
    streak = 0 if test2 else int(state.get("test2_fail_streak", 0)) + 1
    current = state.get("active") or "L1"
    if not test1:
        return "F", streak, "test 1 failed (full history): fallback F"
    if streak >= 2:
        return "F", streak, "test 2 failed two years running: fallback F"
    if test2:
        return "L1", streak, "both tests passed: L1" + (" again" if current != "L1" else "")
    return current, streak, f"test 2 failed once (needs two in a row): {current} kept"


def last_december_session(rets: pd.DataFrame, year: int) -> Optional[pd.Timestamp]:
    idx = rets.index[(rets.index.year == year) & (rets.index.month == 12)]
    return idx[-1] if len(idx) else None


def evaluate(hist: Path, year: int, tilt: bool = False) -> dict:
    """The Addendum 13 simulation, unchanged, on data to the last session of December `year`. tilt: L1 with the
    valuation tilt (Addendum 16a: research/allweather16.py's V1, Nifty 50 dividend yield from hist/pepb.csv)."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "research"))
    import allocation20 as A                                     # noqa: E402 - research code, loaded on demand
    from backtest.metrics import max_drawdown, sharpe
    rets = A.load_sleeves(hist)
    end = last_december_session(rets, year)
    if end is None or (pd.Timestamp(f"{year}-12-31") - end).days > 7:
        raise RuntimeError(f"the data does not reach the end of December {year} (last: {rets.index[-1]:%d %b %Y})")
    rets = rets.loc[:end]
    start = pd.Timestamp("2005-12-01")
    bench = A.simulate(rets, A.hold_one("N50"), start)["equity"]
    if tilt:
        import allweather16 as W                                 # noqa: E402
        val = W.valuation(hist)
        if val.index[-1] < end - pd.Timedelta(days=10):
            raise RuntimeError(f"the valuation data does not reach the end of December {year}")
        rule = W.tilt(rets, val.loc[:end], "div_yield", 20, False)
    else:
        rule = A.fixed_mix(rets, A.L1)
    l1 = A.simulate(rets, rule, start)["equity"]
    out = {"end": f"{end:%Y-%m-%d}"}
    for tag, lo in (("full", pd.Timestamp("2006-01-01")), ("last5", pd.Timestamp(f"{year - 4}-01-01"))):
        b, x = bench.loc[lo:], l1.loc[lo:]
        out[tag] = {"l1_sharpe": round(sharpe(x), 3), "bench_sharpe": round(sharpe(b), 3),
                    "l1_fall": round(max_drawdown(x), 1), "bench_fall": round(max_drawdown(b), 1)}
    f, l5 = out["full"], out["last5"]
    out["test1"] = bool(f["l1_sharpe"] > f["bench_sharpe"] and f["l1_fall"] > f["bench_fall"])
    out["test2"] = bool(l5["l1_sharpe"] > l5["bench_sharpe"])
    return out


def update_data(root: Path) -> None:
    """The research downloads (resumable; the first run on a server fetches 20 years and takes a few hours)."""
    py, script = sys.executable, str(root / "research" / "download_history.py")
    for args in (["--stop-at", "", "--skip-index-history"], ["--extras"], ["--indices-only", "--start", "2005-01-01"]):
        r = subprocess.run([py, script, *args], cwd=root)
        if r.returncode != 0:
            raise RuntimeError(f"download_history.py {' '.join(args)} failed (exit {r.returncode})")


def run(root: Path, notify=None, update: bool = True, force: bool = False, today: Optional[date] = None) -> str:
    today = today or date.today()
    state = load_state(root)
    year = today.year - 1                                        # the re-test covers the year just ended
    last = (state.get("last_retest") or {}).get("year")
    if not force and (today.month != 1 or last == year):
        return f"re-test: nothing to do ({'not January' if today.month != 1 else f'{year} already re-tested'})"
    try:
        if update:
            update_data(root)
        from .autopilot import rulebook
        tilt = bool(((rulebook(root).get("rules") or {}).get("L1") or {}).get("valuation"))
        res = evaluate(root / "research" / "data" / "hist", year, tilt=tilt)
    except Exception as e:                                       # noqa: BLE001 - no data, no change
        msg = f"Autopilot re-test for {year} could not run ({str(e)[:200]}). Nothing changed: rule " \
              f"{state.get('active') or 'L1'} stays."
        log.warning(msg)
        if notify:
            notify(msg)
        return msg
    before = state.get("active") or "L1"
    rule, streak, why = decide(state, res["test1"], res["test2"])
    f, l5 = res["full"], res["last5"]
    state.update(active=rule, test2_fail_streak=streak,
                 last_retest={"year": year, "date": now_iso_date(), "decision": why, **res})
    state.setdefault("history", []).append({"year": year, "rule": rule, "test1": res["test1"], "test2": res["test2"]})
    save_state(root, state)
    msg = (f"Autopilot re-test for {year} (data to {res['end']}):\n"
           f"test 1, 2006-{year}: Sharpe L1 {f['l1_sharpe']:.2f} vs Nifty 50 {f['bench_sharpe']:.2f}, worst fall "
           f"{f['l1_fall']:.1f}% vs {f['bench_fall']:.1f}% -> {'pass' if res['test1'] else 'FAIL'}\n"
           f"test 2, {year - 4}-{year}: Sharpe {l5['l1_sharpe']:.2f} vs {l5['bench_sharpe']:.2f} -> "
           f"{'pass' if res['test2'] else 'FAIL'}\n"
           f"Decision: {why}." + (f" The core moves from {before} to {rule} at the next evening run (an ordinary "
                                  "rebalance)." if rule != before else ""))
    if notify:
        notify(msg)
    return msg
