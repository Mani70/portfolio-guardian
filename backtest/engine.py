"""Portfolio simulation for long-only swing strategies on daily bars.

Rules that keep the test honest:
- Signals use data up to day t's close; orders fill at day t+1's OPEN (no look-ahead).
- Protective stop = entry price - stop_atr x ATR(14) as of the signal day. If a day's low touches it,
  the position exits at the stop, or at the open if the stock gaps below it.
- Every fill pays slippage and Indian delivery costs (STT, exchange, SEBI, stamp duty, GST,
  brokerage, DP charge). Defaults are approximations: check your contract notes.
- At most `max_positions` at once, each sized at (current equity / max_positions), whole shares only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class Costs:
    brokerage_per_order: float = 10.0     # INDstocks algo orders: ₹10/order (dashboard, Oct 2026)
    stt_pct: float = 0.1                  # delivery STT, both sides
    exchange_pct: float = 0.00297         # NSE transaction charge
    sebi_pct: float = 0.0001
    stamp_buy_pct: float = 0.015          # buy side only
    gst_pct: float = 18.0                 # on brokerage + exchange + SEBI
    dp_per_sell: float = 16.0             # depository charge per scrip per sell day (approx.)
    slippage_pct: float = 0.05            # each side

    def buy(self, value: float) -> float:
        b = self.brokerage_per_order
        ex = value * (self.exchange_pct + self.sebi_pct) / 100
        return b + value * self.stt_pct / 100 + ex + value * self.stamp_buy_pct / 100 + (b + ex) * self.gst_pct / 100

    def sell(self, value: float) -> float:
        b = self.brokerage_per_order
        ex = value * (self.exchange_pct + self.sebi_pct) / 100
        return b + value * self.stt_pct / 100 + ex + (b + ex) * self.gst_pct / 100 + self.dp_per_sell


@dataclass
class Position:
    symbol: str
    qty: int
    entry_date: pd.Timestamp
    entry_px: float
    entry_cost: float
    stop: float
    bars: int = 0


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_px: float
    exit_date: pd.Timestamp
    exit_px: float
    qty: int
    pnl: float               # after all costs
    return_pct: float        # on capital deployed incl. entry costs
    bars: int
    reason: str              # signal | stop | time | end


@dataclass
class Result:
    equity: pd.Series
    trades: List[Trade]
    costs_paid: float
    exposure: pd.Series      # fraction of equity invested each day
    params: dict = field(default_factory=dict)


def simulate(frames: Dict[str, pd.DataFrame], capital: float = 500_000, max_positions: int = 5,
             costs: Optional[Costs] = None, stop_atr: float = 3.0, max_hold: int = 0,
             cash_yield_pct: float = 0.0, stop_pct: float = 0.0) -> Result:
    """frames: {symbol: strategy output with open/high/low/close/entry/exit/score/atr}.
    stop_pct > 0 replaces the ATR stop with a fixed percentage below entry (e.g. Minervini's 7-8%)."""
    costs = costs or Costs()
    slip = costs.slippage_pct / 100
    dates = sorted(set().union(*[f.index for f in frames.values()])) if frames else []
    rows = {s: f.to_dict("index") for s, f in frames.items()}

    cash = capital
    positions: Dict[str, Position] = {}
    last_close: Dict[str, float] = {}
    pending_entries: List[tuple] = []        # (symbol, score, atr)
    pending_exits: Dict[str, str] = {}       # symbol -> reason
    trades: List[Trade] = []
    costs_paid = 0.0
    equity_pts, exposure_pts = [], []

    def close(pos: Position, date, px: float, reason: str):
        nonlocal cash, costs_paid
        value = pos.qty * px
        c = costs.sell(value)
        cash += value - c
        costs_paid += c
        invested = pos.qty * pos.entry_px + pos.entry_cost
        pnl = value - c - invested
        trades.append(Trade(pos.symbol, pos.entry_date, pos.entry_px, date, px, pos.qty, pnl,
                            pnl / invested * 100, pos.bars, reason))
        del positions[pos.symbol]

    prev_day = None
    for d in dates:
        # idle cash earns a liquid-fund rate (calendar-day accrual)
        if prev_day is not None and cash_yield_pct and cash > 0:
            cash *= (1 + cash_yield_pct / 100) ** ((d - prev_day).days / 365.25)
        prev_day = d
        # 1) exits at the open
        for sym, reason in list(pending_exits.items()):
            r = rows[sym].get(d)
            if sym in positions and r is not None:
                close(positions[sym], d, r["open"] * (1 - slip), reason)
                del pending_exits[sym]
            elif sym not in positions:
                del pending_exits[sym]

        # 2) entries at the open, best score first
        equity_now = cash + sum(p.qty * last_close.get(s, p.entry_px) for s, p in positions.items())
        for sym, _score, atr_val in sorted(pending_entries, key=lambda x: -x[1]):
            if len(positions) >= max_positions:
                break
            r = rows[sym].get(d)
            if sym in positions or r is None or not (atr_val and atr_val > 0):
                continue
            px = r["open"] * (1 + slip)
            budget = min(equity_now / max_positions, cash)
            qty = math.floor(budget / (px * (1 + 0.003)))            # leave room for costs
            if qty < 1:
                continue
            c = costs.buy(qty * px)
            if qty * px + c > cash:
                continue
            cash -= qty * px + c
            costs_paid += c
            stop_px = px * (1 - stop_pct / 100) if stop_pct else px - stop_atr * atr_val
            positions[sym] = Position(sym, qty, d, px, c, stop_px)
        pending_entries = []

        # 3) protective stops during the day
        for sym, pos in list(positions.items()):
            r = rows[sym].get(d)
            if r is not None and r["low"] <= pos.stop:
                fill = min(r["open"], pos.stop) * (1 - slip)
                close(pos, d, fill, "stop")
                pending_exits.pop(sym, None)

        # 4) end of day: update marks, queue tomorrow's orders
        for sym, rmap in rows.items():
            r = rmap.get(d)
            if r is None:
                continue
            last_close[sym] = r["close"]
            if sym in positions:
                pos = positions[sym]
                pos.bars += 1
                if r.get("exit"):
                    pending_exits[sym] = "signal"
                elif max_hold and pos.bars >= max_hold:
                    pending_exits[sym] = "time"
            elif r.get("entry"):
                pending_entries.append((sym, float(r.get("score") or 0), r.get("atr")))

        invested = sum(p.qty * last_close.get(s, p.entry_px) for s, p in positions.items())
        equity_pts.append(cash + invested)
        exposure_pts.append(invested / (cash + invested) if cash + invested > 0 else 0)

    # close anything still open at the last close (marked, costs included)
    if dates:
        for sym, pos in list(positions.items()):
            close(pos, dates[-1], last_close.get(sym, pos.entry_px), "end")
        if equity_pts:
            equity_pts[-1] = cash
    idx = pd.DatetimeIndex(dates)
    return Result(pd.Series(equity_pts, index=idx, dtype=float), trades, costs_paid,
                  pd.Series(exposure_pts, index=idx, dtype=float),
                  dict(capital=capital, max_positions=max_positions, stop_atr=stop_atr, max_hold=max_hold))
