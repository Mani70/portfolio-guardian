"""Promotion gates: a strategy may send real orders only after its PAPER record proves itself.

A record passes when, net of all charges:
  - it has at least `min_trades` closed trades spread over at least `min_days` calendar days,
  - the average trade made money, and
  - the t-statistic of DAILY net P&L is at least `min_t` (trades on the same day are not independent).
A live strategy that loses more than `live_max_drawdown_pct` of its capital is sent back to paper.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Tuple

import pandas as pd


def stats(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return dict(trades=0, days=0, net=0.0, mean=0.0, t=float("nan"), win=float("nan"))
    t = trades.copy()
    t["day"] = t["exit_time"].str[:10]
    daily = t.groupby("day")["net"].sum()
    span = (pd.Timestamp(t["exit_time"].max()) - pd.Timestamp(t["entry_time"].min())).days
    sd = daily.std(ddof=1) if len(daily) > 1 else float("nan")
    tstat = daily.mean() / sd * math.sqrt(len(daily)) if sd and not math.isnan(sd) else float("nan")
    return dict(trades=len(t), days=span, net=float(t["net"].sum()), mean=float(t["net"].mean()),
                t=float(tstat), win=float((t["net"] > 0).mean() * 100))


def paper_gate(journal_path: Path, strategy: str, engine: str, cfg: dict) -> Tuple[bool, str, dict]:
    """Per-strategy `gate:` in trader.yaml overrides the engine default. Monthly allocation strategies use a
    light gate (min_t: null, require_profit: false): their paper phase checks that orders and fills behave
    as the 10-year research assumed, because a few trades can't prove an edge either way."""
    from .journal import Journal
    g = dict(cfg["gates"][engine])
    g.update((cfg["strategies"].get(strategy) or {}).get("gate") or {})
    j = Journal(journal_path, "paper")
    try:
        s = stats(j.trades(strategy))
        if g.get("count") == "orders":            # low-frequency strategies: judge filled orders, not closed trades
            filled = [o for o in j.load_orders(statuses=["FILLED"]) if o.req.strategy == strategy]
            s["trades"] = len(filled)
            s["days"] = (max(o.req.created for o in filled) - min(o.req.created for o in filled)).days if filled else 0
    finally:
        j.close()
    if s["trades"] < g["min_trades"]:
        what = "filled paper orders" if g.get("count") == "orders" else "paper trades"
        return False, f"{s['trades']} {what} (needs {g['min_trades']})", s
    if s["days"] < g["min_days"]:
        return False, f"paper record spans {s['days']} days (needs {g['min_days']})", s
    if g.get("require_profit", True) and s["mean"] <= 0:
        return False, f"paper trades lose on average (₹{s['mean']:,.0f})", s
    if g.get("min_t") is not None and not (s["t"] >= g["min_t"]):
        return False, f"paper t-stat {s['t']:.2f} (needs {g['min_t']})", s
    return True, "passed", s


def demoted_path(root: Path) -> Path:
    return Path(root) / "trader" / "state" / "demoted.json"


def is_demoted(root: Path, strategy: str) -> bool:
    p = demoted_path(root)
    return p.exists() and strategy in json.loads(p.read_text(encoding="utf-8") or "{}")


def check_live_drawdown(journal, strategy: str, capital: float, max_dd_pct: float, root: Path) -> Tuple[bool, float]:
    """True if the strategy's live P&L drawdown is beyond the limit; records the demotion."""
    t = journal.trades(strategy, mode="live")
    if t.empty or capital <= 0:
        return False, 0.0
    curve = t.sort_values("exit_time")["net"].cumsum()
    dd = float((curve - curve.cummax().clip(lower=0)).min())
    pct = -dd / capital * 100
    if pct >= max_dd_pct:
        p = demoted_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cur[strategy] = {"drawdown_pct": round(pct, 2)}
        p.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        return True, pct
    return False, pct
