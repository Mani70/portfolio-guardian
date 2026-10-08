"""Autopilot (research/PREREGISTRATION.md Addendum 14): the bot runs the rulebook without manual steps.

trader.yaml needs only:
    mode: live
    autopilot: {enabled: true, reserve: 10000}     # rupees never invested; paper_capital: the paper control's size

Then:
- the strategies come from trader/rulebook.yaml (the active rule as one long-term core, plus paper-only ones);
  trader.yaml's own `strategies:` are ignored;
- the live core manages ALL free cash in the INDstocks account above the reserve: new deposits are invested within a
  day or two (the evening cash sweep), withdrawals shrink it;
- holdings of retired strategies are handed to the core at cost (handover), so they are trimmed or topped up, not sold
  and bought back;
- the active rule changes only through the yearly re-test (trader/retest.py), which writes trader/state/autopilot.json.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

log = logging.getLogger("trader.autopilot")

LIVE_GATE = True                     # Addendum 12: the 20-year test replaces paper months for the rulebook's rules


def rulebook(root: Path) -> dict:
    return yaml.safe_load((Path(root) / "trader" / "rulebook.yaml").read_text(encoding="utf-8")) or {}


def state_path(root: Path) -> Path:
    return Path(root) / "trader" / "state" / "autopilot.json"


def load_state(root: Path) -> dict:
    p = state_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        log.warning("autopilot state unreadable (%s): using the rulebook's active rule", p)
        return {}


def save_state(root: Path, state: dict) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)


def active_rule(root: Path, rb: Optional[dict] = None) -> str:
    rb = rb if rb is not None else rulebook(root)
    chosen = load_state(root).get("active")
    return chosen if chosen in (rb.get("rules") or {}) else rb["active"]


def apply(cfg: dict, root: Path) -> dict:
    """trader.yaml settings -> the settings the engines run with. Unchanged unless autopilot.enabled."""
    ap = cfg.get("autopilot") or {}
    if not ap.get("enabled"):
        return cfg
    rb = rulebook(root)
    rule = active_rule(root, rb)
    core = rb.get("core_name", "core")
    out = copy.deepcopy(cfg)
    strategies = {core: {"enabled": True, "class": "core_allocation", "live": True, "override_gate": LIVE_GATE,
                         "capital_share": 1.0, "max_drawdown_pct": 100, "max_order_value": 1e12,
                         "params": dict(rb["rules"][rule])}}
    for name, sc in (rb.get("paper_only") or {}).items():
        strategies[name] = dict(sc or {}, enabled=True, live=False)
    out["strategies"] = strategies
    out["capital"] = dict(out["capital"], all_cash=True, live_from_account=True,
                          reserve=float(ap.get("reserve", out["capital"].get("reserve", 0)) or 0),
                          swing=float(ap.get("paper_capital", out["capital"].get("swing", 300_000))))
    out["_autopilot"] = {"rule": rule, "core": core, "retired": list(rb.get("retired") or [])}
    return out


def transfer_positions(j, src: str, dst: str) -> List[str]:
    """Move every position of strategy `src` to `dst` in the journal, keeping cost, entry time and charges (nothing
    is bought or sold). A symbol `dst` already holds is merged at the combined average price. Returns notes."""
    from .corporate import ca_ref
    notes = []
    have = {p.symbol: p for p in j.positions(strategy=dst)}
    for p in j.positions(strategy=src):
        q = have.get(p.symbol)
        j.delete_position(p)
        if q is not None and q.product == p.product:
            n = q.qty + p.qty
            ref = max(ca_ref(q), ca_ref(p), key=lambda x: x[1])    # the later lot keeps the bonus/split check right
            q.avg_price = (q.avg_price * abs(q.qty) + p.avg_price * abs(p.qty)) / abs(n)
            q.qty, q.entry_charges = n, q.entry_charges + p.entry_charges
            q.meta = {**(q.meta or {}), "ca_px": ref[0], "ca_day": ref[1].isoformat()}
            j.save_position(q)
        else:
            p.strategy = dst
            j.save_position(p)
        notes.append(f"{p.symbol}: {abs(p.qty)} units moved from {src} to {dst} (cost ₹{p.avg_price:,.2f} kept)")
    return notes


def handover(cfg: dict, journal_path: Path) -> List[str]:
    """Hand the retired strategies' holdings (live and paper books) to the core. A strategy with orders still
    working is left for a later run. Returns what was moved (empty: nothing to do)."""
    ap = cfg.get("_autopilot")
    if not ap or not ap.get("retired"):
        return []
    from .journal import Journal
    notes = []
    for book in ("live", "paper"):
        j = Journal(journal_path, book)
        try:
            for old in ap["retired"]:
                if not j.positions(strategy=old):
                    continue
                if j.active_orders(old):
                    log.info("autopilot: %s (%s) still has orders working; handover next run", old, book)
                    continue
                moved = transfer_positions(j, old, ap["core"])
                j.event("info", f"autopilot handover {old} -> {ap['core']}: " + "; ".join(moved))
                if book == "live":
                    notes += moved
        finally:
            j.close()
    if notes:
        notes.insert(0, f"Autopilot: the retired strategies' holdings now belong to {ap['core']} (rule {ap['rule']}); "
                        "it trims or tops them up to its mix:")
    return notes


def describe(cfg: dict, root: Path) -> str:
    """One line for the morning health message and `status`."""
    ap = cfg.get("_autopilot")
    if not ap:
        return ""
    last = load_state(root).get("last_retest") or {}
    line = f"autopilot: rule {ap['rule']}" + (f" (re-tested {last.get('date')}: {last.get('decision')})" if last
                                              else " (not re-tested yet)")
    vp = ((cfg.get("strategies") or {}).get(ap["core"]) or {}).get("params", {}).get("valuation")
    if vp:
        from datetime import date
        from .valuation import regime
        line += "; " + regime(vp, date.today())["why"]
    return line


def now_iso_date() -> str:
    return datetime.now().date().isoformat()
