"""Bonus issues and share splits on what the bot holds.

On the ex-date the share price drops by the ratio (a 1:1 bonus halves it) and the extra shares reach the demat
account a day or two later. Left alone, the bot would keep the old share count at the old price: a fake loss in
its reports and limits, and a sale that leaves the new shares behind.

Detection: the daily history is put on today's price basis (split boundaries that INDstocks left in it are
back-adjusted; for real money also any opening gap within 4.5% of a standard ratio after the position was
opened, because the ex-date's own move can push the close away from the exact ratio). If the position's own
price no longer fits its opening day in that history but does after a standard ratio, that ratio is the
adjustment. The same history gives the same answer every run, so nothing is adjusted twice.

Real money is adjusted only when the account confirms it: its share count must have become the last count
taken while nothing was pending times the ratio (a bonus multiplies every share, yours included; a crash or a
buy in the app does not). Until then the position's sells are held (one Telegram message a day); if the shares
have not arrived 3 sessions later (SEBI: tradable by T+2), the move is treated as a real fall. Consolidations are never applied
automatically. `trader.run split --stock X --ratio N` records one by hand. Paper uses only exact boundaries.
Total cost stays the same (for tax, bonus shares have their own cost and date).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, Optional

import pandas as pd

from backtest.data import SPLIT_RATIOS, adjust_splits

from .models import Position

STANDARD = SPLIT_RATIOS + tuple(1 / r for r in SPLIT_RATIOS)
NAMES = {1 / 2: "1:1 bonus or 2-for-1 split", 1 / 3: "2:1 bonus or 3-for-1 split", 2 / 3: "1:2 bonus",
         1 / 4: "3:1 bonus or 4-for-1 split", 3 / 4: "1:3 bonus", 1 / 5: "4:1 bonus or 5-for-1 split",
         2 / 5: "3:2 bonus", 3 / 5: "2:3 bonus", 1 / 10: "10-for-1 split"}
SLACK = 0.03                       # a fill can sit a little outside the day's candle (slippage, rounding)
GAP_TOL = 0.045                    # opening gap vs ratio; neighbouring ratios are >= 10% apart
SNAP_TOL = 0.08                    # a found boundary this close to a standard ratio is that ratio
WAIT_SESSIONS = 3                  # bonus shares are tradable by T+2 (SEBI); one more, then it is a real fall


def standard(f: float, tol: float = 0.015) -> Optional[float]:
    """The standard bonus/split price ratio within `tol` of f, or None."""
    if not f or f != f:
        return None
    return next((r for r in STANDARD if abs(f / r - 1) <= tol), None)


def describe(r: float) -> str:
    for k, v in NAMES.items():
        if abs(r / k - 1) < 1e-6:
            return v
    return f"share consolidation ({1 / r:g} old shares -> 1)" if r > 1 else f"split (price x{r:g})"


@dataclass
class Adjustment:
    position: Position
    factor: float                  # new price / old price (0.5 for a 1:1 bonus)
    when: date                     # ex-date if the history shows it, else the position's opening day
    new_qty: int

    @property
    def new_avg(self) -> float:
        return self.position.avg_price * self.position.qty / self.new_qty


def _inside(px: float, lo: float, hi: float) -> bool:
    return lo * (1 - SLACK) <= px <= hi * (1 + SLACK)


def find_one(p: Position, df: Optional[pd.DataFrame], rejected: Iterable[str] = (),
             loose: bool = False) -> Optional[Adjustment]:
    """The adjustment the position needs to be on today's price basis, or None.
    rejected: ex-dates (ISO) already judged to be real price falls. loose: also use near-ratio opening gaps."""
    if df is None or len(df) < 2 or p.qty <= 0 or p.avg_price <= 0:
        return None
    rejected = set(rejected)
    opened = pd.Timestamp(p.entry_time.date())
    if opened > df.index[-1]:
        return None                                    # bought today: no candle for that day yet
    adj, found = adjust_splits(df)
    cols = ["open", "high", "low", "close"]
    bounds = []
    for d, f in found:
        if d <= opened:
            continue
        if d.date().isoformat() in rejected:           # a judged-real fall is put back as a price move
            adj.loc[adj.index < d, cols] /= f
            continue
        r = standard(f, tol=SNAP_TOL)
        if r is not None and r != f:                   # a big ex-date move: the ratio is the standard one,
            adj.loc[adj.index < d, cols] *= r / f      # the rest is that day's own price change
        bounds.append(d)
    if loose:
        idx = adj.index
        for k in range(len(idx) - 1, 0, -1):           # latest first, so adjustments compound correctly
            d = idx[k]
            if d <= opened:
                break
            r = standard(float(adj["open"].iloc[k]) / float(adj["close"].iloc[k - 1]), tol=GAP_TOL)
            if r is None or r >= 1 or d.date().isoformat() in rejected:
                continue
            adj.loc[idx < d, cols] *= r
            bounds.append(d)
    row = adj.loc[:opened]                             # the opening day (or the last session before a weekend handover)
    if row.empty:
        return None
    lo, hi = float(row["low"].iloc[-1]), float(row["high"].iloc[-1])
    if _inside(p.avg_price, lo, hi):
        return None                                    # the position is on today's price basis
    for r in STANDARD:
        if _inside(p.avg_price * r, lo, hi):
            when = max(bounds).date() if bounds else row.index[-1].date()
            new_qty = int(p.qty / r + 1e-6)            # fractions of a share are paid out in cash
            if when.isoformat() in rejected or new_qty < 1:
                return None
            return Adjustment(p, r, when, new_qty)
    return None


def account_qty(holdings: Iterable) -> Dict[str, int]:
    """Account shares keyed by security id and by "sym:SYMBOL" (either may be what matches a position)."""
    out: Dict[str, int] = {}
    for h in holdings:
        q = int(round(float(h.qty)))
        if getattr(h, "security_id", ""):
            out[str(h.security_id)] = out.get(str(h.security_id), 0) + q
        out["sym:" + h.symbol.upper()] = out.get("sym:" + h.symbol.upper(), 0) + q
    return out


def held(acct: Dict[str, int], p: Position) -> int:
    if p.security_id and str(p.security_id) in acct:
        return acct[str(p.security_id)]
    return acct.get("sym:" + p.symbol.upper(), 0)


def confirmed(acct: Dict[str, int], p: Position, a: Adjustment) -> bool:
    """A bonus/split multiplies EVERY share in the account: the count must now be the last count taken with
    nothing pending, times the ratio (one share either way for rounding). Buying or selling shares of the stock
    yourself while the bot waits stops it from confirming (then use `trader.run split`)."""
    base = (p.meta or {}).get("acct_qty")
    if base is None or a.factor >= 1:
        return False
    return abs(held(acct, p) - int(int(base) / a.factor + 1e-6)) <= 1


def apply(journal, a: Adjustment, now: datetime) -> str:
    p = a.position
    old_q, old_px, new_avg = p.qty, p.avg_price, a.new_avg
    p.qty, p.avg_price = a.new_qty, new_avg
    meta = dict(p.meta or {})
    if meta.get("owner_cost") is not None:
        meta["owner_cost"] = float(meta["owner_cost"]) * old_q / a.new_qty
        journal.put(f"owner_cost:{p.strategy}:{p.symbol}", meta["owner_cost"])
    if meta.get("acct_qty") is not None:
        meta["acct_qty"] = int(meta["acct_qty"]) + a.new_qty - old_q
    meta.setdefault("corporate_actions", []).append(
        {"date": a.when.isoformat(), "factor": a.factor, "old_qty": old_q, "new_qty": a.new_qty,
         "applied": now.isoformat()})
    p.meta = meta
    journal.save_position(p)
    msg = (f"{p.symbol}: {describe(a.factor)} (ex-date {a.when:%d %b}): {p.strategy} now holds {a.new_qty} shares at "
           f"₹{new_avg:,.2f} (was {old_q} at ₹{old_px:,.2f}); total cost unchanged.")
    journal.event("warning", f"corporate action: {msg}")
    return msg


def pending_text(a: Adjustment) -> str:
    p = a.position
    return (f"{p.symbol}: the price moved x{a.factor:g} around {a.when:%d %b}, which matches a {describe(a.factor)}. "
            f"{p.strategy} holds {p.qty} shares; with a bonus/split the account should soon show {a.new_qty}. Until "
            f"they arrive the bot sends no sell for {p.symbol} and then updates its record (it waits up to "
            f"{WAIT_SESSIONS} sessions). If no bonus or split was announced, this is a real fall: check the news.")
