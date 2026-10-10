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


def evaluate(hist: Path, year: int, rule_params: Optional[dict] = None) -> dict:
    """The Addendum 13 simulation on data to the last session of December `year`, for the rulebook's rule L1 as it
    stands (rule_params: its weights and valuation tilt, Addenda 16a/19a); None = Addendum 13's original L1."""
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
    if rule_params:                                              # Addendum 19a: the rulebook's own weights and tilt
        import allweather16 as W                                 # noqa: E402
        import midcap19 as M                                     # noqa: E402
        rets = M.add_mid(rets, hist)
        val = W.valuation(hist) if rule_params.get("valuation") else None
        if val is not None and val.index[-1] < end - pd.Timedelta(days=10):
            raise RuntimeError(f"the valuation data does not reach the end of December {year}")
        rule = M.rulebook_fn(rets, val.loc[:end] if val is not None else None, rule_params)
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
        res = evaluate(root / "research" / "data" / "hist", year, (rulebook(root).get("rules") or {}).get("L1"))
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
    names = {"L1": "your long-term mix (rule L1)", "F": "the safer fallback mix (rule F: Nifty 50 fund and cash)"}
    msg = (f"📅 Yearly check of your investing rules (prices up to {res['end']})\n"
           "Each year the bot re-runs its rules on all market history to make sure they still work. 'Score' below "
           "means return earned per unit of up-and-down movement (higher is better).\n"
           f"• Check 1, everything since 2006: score {f['l1_sharpe']:.2f} vs {f['bench_sharpe']:.2f} for simply holding "
           f"the Nifty 50, worst fall {f['l1_fall']:.0f}% vs {f['bench_fall']:.0f}% -> "
           f"{'PASS' if res['test1'] else 'FAIL'}\n"
           f"• Check 2, the last 5 years ({year - 4}-{year}): score {l5['l1_sharpe']:.2f} vs {l5['bench_sharpe']:.2f} -> "
           f"{'PASS' if res['test2'] else 'FAIL'}\n"
           f"Decision: {why}. Now using {names.get(rule, rule)}."
           + (f" The switch from {names.get(before, before)} happens at the next evening run, as an ordinary "
              "rebalance." if rule != before else ""))
    if notify:
        notify(msg)
    return msg
