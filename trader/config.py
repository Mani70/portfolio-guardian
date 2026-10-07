"""trader.yaml loading with safe defaults. Anything missing falls back to DEFAULTS below."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "mode": "paper",                      # paper | live
    "journal": "trader/state/journal.db",
    "compound": True,                     # reinvest each strategy's realized profit (losses shrink it too)
    # swing: paper capital and the most live may use. live_from_account: live strategies size from the money
    # actually in the account (free cash + what they hold) when that is less. reserve: rupees never touched.
    "capital": {"swing": 300_000, "intraday": 100_000, "live_from_account": True, "reserve": 0},
    "limits": {
        "max_order_value": 150_000,       # hard cap on any single order, ₹
        "max_orders_per_day": 30,
        "price_band_pct": 0.5,            # marketable limit price = LTP +/- this
        "order_rate_per_sec": 2,          # far below SEBI's 10/s retail threshold
        "kill_switch_file": "STOP",       # create this file in the project folder to block new orders
    },
    "swing": {"execute_at": "09:20", "fill_timeout_min": 10},
    "intraday": {
        "first_entry": "09:20", "last_entry": "14:30", "square_off": "15:15",
        "max_positions": 3, "max_entries_per_day": 6,
        "risk_per_trade_pct": 0.5,        # of intraday capital, distance entry->stop
        "max_position_pct": 40,           # of intraday capital per position (no leverage)
        "daily_loss_limit_pct": 1.5,      # stop new entries for the day after this loss
        "poll_seconds": 20,
    },
    "gates": {                             # a strategy's PAPER record must pass these before live orders
        "swing": {"min_trades": 20, "min_days": 90, "min_t": 1.5},
        "intraday": {"min_trades": 60, "min_days": 40, "min_t": 2.0},
        "live_max_drawdown_pct": 8,        # live strategy loses this % of its capital -> back to paper
    },
    "strategies": {},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: Path = None) -> dict:
    path = Path(path) if path else ROOT / "trader.yaml"
    if not path.exists():
        path = ROOT / "trader.example.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = _merge(DEFAULTS, raw or {})
    if cfg["mode"] not in ("paper", "live"):
        raise ValueError(f"mode must be 'paper' or 'live', not {cfg['mode']!r}")
    cfg["_path"] = str(path)
    return cfg


def universe(cfg: dict, root: Path = ROOT, nse: bool = True) -> list:
    """Symbols the scanners look at: trader.yaml `universe`, else (live trading, `nse`) the Nifty 50 list last
    downloaded from NSE (trader/universe_update.py), else the backtest universe. Replays pass nse=False."""
    if cfg.get("universe"):
        return [s.upper() for s in cfg["universe"]]
    if nse:
        from .universe_update import load
        got = load(root)
        if got:
            return [s.upper() for s in got["symbols"]]
    p = ROOT / "backtest.yaml"
    if not p.exists():
        p = ROOT / "backtest.example.yaml"
    bt = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [s.upper() for s in bt.get("universe", [])]


def lab(cfg: dict, root: Path = ROOT) -> dict | None:
    """Settings for the paper-only experiments in trader/experiments.yaml (journal mode "lab"), or None.
    Same limits and costs as trader.yaml, its own notional capital, every strategy forced to paper, and tranches of
    one strategy may hold the same stock. Swing strategies only (the intraday job never sees the lab). Never raises:
    a broken file only switches the experiments off."""
    p = Path(root) / "trader" / "experiments.yaml"
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
        from .strategies import REGISTRY
        strategies = {n: dict(s, live=False, override_gate=False)
                      for n, s in ((raw or {}).get("strategies") or {}).items()
                      if s and s.get("enabled") and getattr(REGISTRY.get(s.get("class", n)), "engine", "") == "swing"}
        if not strategies:
            return None
        out = copy.deepcopy(cfg)
        out["mode"] = "paper"
        out["strategies"] = strategies
        out["capital"] = dict(out["capital"], swing=float(raw.get("capital", out["capital"]["swing"])), reserve=0)
        out["limits"] = dict(out["limits"], max_orders_per_day=max(100, int(out["limits"]["max_orders_per_day"])))
        out["_shared_symbols"] = True
        out["_path"] = str(p)
        return out
    except Exception:                                   # noqa: BLE001 - experiments must never stop the real jobs
        import logging
        logging.getLogger("trader").exception("trader/experiments.yaml unusable: experiments off")
        return None
