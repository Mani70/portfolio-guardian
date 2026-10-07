"""Month-end momentum paper trading: forward-tests the strategy that passed the backtest.

On the last trading day of each month (after the close) it:
  1. updates daily prices for the universe in backtest.yaml (cached; a few calls),
  2. ranks stocks with the same rules as backtest `momentum_rotation`
     (volatility-adjusted 6/12-month momentum, trend-template filter, Nifty 200-day market filter),
  3. rebalances an imaginary portfolio at that day's closing prices, charging delivery costs,
  4. sends you the picks, the changes (BUY / SELL / HOLD) and paper performance vs the Nifty ETF and FD.

It never places orders. State: paper/momentum_portfolio.json. Log: paper/momentum_log.csv.

Usage:
  python -m guardian.momentum              # does nothing unless today is the month's last weekday
  python -m guardian.momentum --preview    # show today's ranking without touching the paper portfolio
  python -m guardian.momentum --force      # rebalance now (use once to start tracking)
  python -m guardian.momentum --dry-run    # print instead of sending to Telegram
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
PAPER_DIR = ROOT / "paper"
log = logging.getLogger("guardian.momentum")


def is_last_weekday_of_month(d: date) -> bool:
    """True if no weekday is left in d's month after d. (Exchange holidays aren't modelled: if the
    last weekday is a holiday, the run uses the previous session's prices, which is still month-end.)"""
    if d.weekday() >= 5:
        return False
    nxt = d + timedelta(days=1)
    while nxt.month == d.month:
        if nxt.weekday() < 5:
            return False
        nxt += timedelta(days=1)
    return True


def load_backtest_config() -> dict:
    p = ROOT / "backtest.yaml"
    if not p.exists():
        p = ROOT / "backtest.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def new_state(capital: float, d: pd.Timestamp, bench_px: Optional[float]) -> dict:
    return {"started": d.strftime("%Y-%m-%d"), "capital": capital, "cash": capital, "holdings": {},
            "bench_start_px": bench_px, "last_rebalance": None, "last_value_date": d.strftime("%Y-%m-%d"),
            "realized_pnl": 0.0, "costs_paid": 0.0, "rebalances": 0}


def choose_targets(scores: pd.Series, current: List[str], market_up: bool, slots: int, exit_rank: int) -> List[str]:
    """Same rule as the backtest: keep holdings still in the top `exit_rank`, fill free slots by rank."""
    if not market_up:
        return []
    ranked = list(scores.index)
    keep = [s for s in current if s in ranked[:exit_rank]]
    fill = [s for s in ranked if s not in keep][:max(0, slots - len(keep))]
    return keep + fill


def rebalance(state: dict, targets: List[str], prices: Dict[str, float], d: pd.Timestamp,
              slots: int, costs, cash_yield_pct: float) -> List[Tuple[str, str, int, float, float]]:
    """Apply paper trades at `prices` (closing prices, adjusted for slippage).
    Returns [(action, symbol, qty, fill price, pnl)]."""
    actions = []
    slip = getattr(costs, "slippage_pct", 0.0) / 100
    last = pd.Timestamp(state["last_value_date"])
    if cash_yield_pct and state["cash"] > 0 and d > last:
        state["cash"] *= (1 + cash_yield_pct / 100) ** ((d - last).days / 365.25)
    state["last_value_date"] = d.strftime("%Y-%m-%d")
    h = state["holdings"]
    for sym in [s for s in h if s not in targets]:
        px = prices.get(sym)
        if px is None:                       # no price today: keep it and retry next month
            continue
        px *= 1 - slip
        pos = h.pop(sym)
        value = pos["qty"] * px
        c = costs.sell(value)
        pnl = value - c - pos["cost_basis"]
        state["cash"] += value - c
        state["costs_paid"] += c
        state["realized_pnl"] += pnl
        actions.append(("SELL", sym, pos["qty"], px, pnl))
    equity = state["cash"] + sum(p["qty"] * prices.get(s, p["entry_px"]) for s, p in h.items())
    for sym in targets:
        if sym in h:
            actions.append(("HOLD", sym, h[sym]["qty"], prices.get(sym, h[sym]["entry_px"]), 0.0))
            continue
        px = prices.get(sym)
        if px is None or len(h) >= slots:
            continue
        px *= 1 + slip
        budget = min(equity / slots, state["cash"])
        qty = math.floor(budget / (px * 1.003))
        if qty < 1:
            continue
        c = costs.buy(qty * px)
        if qty * px + c > state["cash"]:
            continue
        state["cash"] -= qty * px + c
        state["costs_paid"] += c
        h[sym] = {"qty": qty, "entry_px": px, "entry_date": d.strftime("%Y-%m-%d"), "cost_basis": qty * px + c}
        actions.append(("BUY", sym, qty, px, 0.0))
    state["last_rebalance"] = d.strftime("%Y-%m-%d")
    state["rebalances"] += 1
    return actions


def portfolio_value(state: dict, prices: Dict[str, float]) -> float:
    return state["cash"] + sum(p["qty"] * prices.get(s, p["entry_px"]) for s, p in state["holdings"].items())


def compose_message(d: pd.Timestamp, market_up: bool, bench: str, bench_px: Optional[float],
                    bench_sma: Optional[float], scores: pd.Series, actions, state: Optional[dict],
                    prices: Dict[str, float], fd_rate: float, excluded: List[str], preview: bool,
                    slots: int) -> str:
    lines = [f"Momentum {'preview' if preview else 'month-end review'}, {d:%d %b %Y} (paper trading, no orders)"]
    if bench_px and bench_sma:
        lines.append(f"Market filter: {bench} {bench_px:,.2f} vs 200-day avg {bench_sma:,.2f} -> "
                     f"{'INVEST' if market_up else 'STAY IN CASH'}")
    lines.append("")
    lines.append(f"Top {slots} by momentum (score = strength vs the other eligible stocks):")
    for i, (sym, sc) in enumerate(scores.head(slots).items(), 1):
        lines.append(f"  {i:>2}. {sym:<12} score {sc:+.2f}")
    if len(scores) < slots:
        lines.append(f"  (only {len(scores)} stocks pass the trend template)")
    if actions:
        lines.append("")
        lines.append("Paper trades at today's close:" if not preview else "Would trade:")
        for act, sym, qty, px, pnl in sorted(actions, key=lambda a: {"SELL": 0, "BUY": 1, "HOLD": 2}[a[0]]):
            extra = f", P&L ₹{pnl:,.0f}" if act == "SELL" else ""
            lines.append(f"  {act:<4} {sym:<12} {qty:>5} @ ₹{px:,.2f}{extra}")
    if state is not None and not preview:
        v = portfolio_value(state, prices)
        start = pd.Timestamp(state["started"])
        days = max((d - start).days, 0)
        ret = (v / state["capital"] - 1) * 100
        fd = ((1 + fd_rate / 100) ** (days / 365.25) - 1) * 100
        lines.append("")
        lines.append(f"Paper portfolio since {start:%d %b %Y}: ₹{v:,.0f} ({ret:+.1f}%)")
        if state.get("bench_start_px") and bench_px:
            lines.append(f"  {bench} buy-and-hold: {(bench_px / state['bench_start_px'] - 1) * 100:+.1f}%   "
                         f"FD {fd_rate}%: {fd:+.1f}%")
        lines.append(f"  Costs paid ₹{state['costs_paid']:,.0f}; holdings {len(state['holdings'])}, "
                     f"cash ₹{state['cash']:,.0f}")
    if excluded:
        lines.append("")
        lines.append(f"Skipped (unexplained >35% one-day move in the last year): {', '.join(excluded)}")
    lines.append("")
    lines.append("Track this for 3-6 months before risking money. Real fills would be at the next open.")
    return "\n".join(lines)


def append_log(d: pd.Timestamp, actions, value: float, bench_px: Optional[float], market_up: bool) -> None:
    PAPER_DIR.mkdir(exist_ok=True)
    p = PAPER_DIR / "momentum_log.csv"
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "action", "symbol", "qty", "price", "pnl", "portfolio_value", "bench_px", "market_up"])
        for act, sym, qty, px, pnl in actions or [("NONE", "", 0, 0, 0)]:
            w.writerow([d.strftime("%Y-%m-%d"), act, sym, qty, round(px, 2), round(pnl, 2), round(value, 2),
                        bench_px, market_up])


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Month-end momentum paper trading")
    ap.add_argument("--preview", action="store_true", help="show the ranking; don't change the paper portfolio")
    ap.add_argument("--force", action="store_true", help="rebalance even if today isn't month-end")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending Telegram")
    ap.add_argument("--reset", action="store_true", help="discard the paper portfolio and start again")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .rules import IST
    today = datetime.now(IST)
    if not (args.preview or args.force or is_last_weekday_of_month(today.date())):
        log.info("Not the month's last weekday; nothing to do (use --preview or --force).")
        return 0
    if not (args.preview or args.force) and (today.hour, today.minute) < (15, 35):
        log.info("Month-end, but the session hasn't closed yet; run after 15:35.")
        return 0

    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    from backtest.data import PriceStore, adjust_splits, resolve_codes, suspicious_jumps, update_history
    from backtest.engine import Costs
    from backtest.rotation import market_ok, momentum_scores
    from .broker import IndStocksClient
    from .main import instrument_index
    from .notifier import Notifier

    cfg = load_backtest_config()
    params = (cfg.get("strategies") or {}).get("momentum_rotation") or {}
    slots, exit_rank = int(params.get("slots", 10)), int(params.get("exit_rank", 20))
    market_filter = bool(params.get("market_filter", True))
    bench = str(cfg.get("benchmark", "NIFTYBEES")).upper()
    universe = [s.upper() for s in cfg.get("universe", [])]
    capital = float(cfg.get("capital", 500_000))
    costs = Costs(**(cfg.get("costs") or {}))
    cash_yield, fd_rate = float(cfg.get("cash_yield", 6.0)), float(cfg.get("fd_rate", 6.25))

    notifier = Notifier(dry_run=args.dry_run)
    try:
        client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
        codes, missing = resolve_codes(universe + [bench], instrument_index(client, ROOT / "cache"), "NSE")
        frames = update_history(client, codes, 1.5, PriceStore(ROOT))
    except Exception as e:
        log.exception("Momentum run failed")
        notifier.send(f"Momentum paper tracker could not run: {e}")
        return 2

    bench_df = frames.get(bench)
    if bench_df is None or bench_df.empty:
        notifier.send(f"Momentum paper tracker: no price data for benchmark {bench}.")
        return 2
    d = bench_df.index[-1]                                   # latest completed session
    stocks, excluded = {}, []
    for s in universe:
        df = frames.get(s)
        if df is None or df.empty or df.index[-1] != d:
            continue
        df = adjust_splits(df)[0]
        if suspicious_jumps(df.iloc[-300:]):              # an unexplained >35% day: don't trust the ranking
            excluded.append(s)
            continue
        stocks[s] = df
    scores = momentum_scores(stocks, d)
    market_up = market_ok(bench_df, d) if market_filter else True
    bench_px = float(bench_df["close"].iloc[-1])
    bench_sma = float(bench_df["close"].iloc[-200:].mean()) if len(bench_df) >= 200 else None
    prices = {s: float(df["close"].iloc[-1]) for s, df in stocks.items()}

    PAPER_DIR.mkdir(exist_ok=True)
    state_path = PAPER_DIR / "momentum_portfolio.json"
    state = None
    if state_path.exists() and not args.reset:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    for s, p in (state or {}).get("holdings", {}).items():   # held names that dropped out of scoring
        if s not in prices and s in frames and not frames[s].empty:
            prices[s] = float(frames[s]["close"].iloc[-1])

    if args.preview:                                         # rehearse on a copy; nothing is saved
        trial = copy.deepcopy(state) if state else new_state(capital, d, bench_px)
        targets = choose_targets(scores, list(trial["holdings"]), market_up, slots, exit_rank)
        acts = rebalance(trial, targets, prices, d, slots, costs, cash_yield)
        notifier.send(compose_message(d, market_up, bench, bench_px, bench_sma, scores, acts, state, prices,
                                      fd_rate, excluded, True, slots))
        return 0

    if state is None:
        state = new_state(capital, d, bench_px)
    if state.get("last_rebalance") == d.strftime("%Y-%m-%d"):
        log.info("Already rebalanced for %s; sending the current state only.", d.date())
        actions = []
    else:
        targets = choose_targets(scores, list(state["holdings"]), market_up, slots, exit_rank)
        actions = rebalance(state, targets, prices, d, slots, costs, cash_yield)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        append_log(d, actions, portfolio_value(state, prices), bench_px, market_up)
    notifier.send(compose_message(d, market_up, bench, bench_px, bench_sma, scores, actions, state, prices,
                                  fd_rate, excluded, False, slots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
