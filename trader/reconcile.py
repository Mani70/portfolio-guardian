"""Compare the live strategies' delivery (CNC) positions with the real INDstocks holdings.

Run before the market opens (the 09:05 heartbeat does it): yesterday's trades are then in the holdings as T1
quantity and there are no same-day trades to double count.

- The account holding LESS than the bot thinks (you sold some by hand, or a sell was missed) is a mismatch:
  the bot would later try to sell shares that are not there. `python -m trader.run reconcile --fix` lowers
  the bot's record to what the account really holds.
- The account holding MORE is normal (your own shares of the same ETF) and is not reported.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from .models import CNC, Position

Mismatch = Tuple[str, int, int]          # symbol, bot qty, account qty


def account_qty(holdings: Iterable) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Holdings rows (guardian.broker.Holding) -> qty by security_id and by symbol."""
    by_sid, by_sym = {}, {}
    for h in holdings:
        q = int(round(float(h.qty)))
        if h.security_id:
            by_sid[str(h.security_id)] = by_sid.get(str(h.security_id), 0) + q
        by_sym[h.symbol.upper()] = by_sym.get(h.symbol.upper(), 0) + q
    return by_sid, by_sym


def check(positions: List[Position], holdings: Iterable) -> List[Mismatch]:
    by_sid, by_sym = account_qty(holdings)
    bot: Dict[str, Tuple[int, str]] = {}
    for p in positions:
        if p.product != CNC or p.qty <= 0:
            continue
        q, sid = bot.get(p.symbol, (0, p.security_id))
        bot[p.symbol] = (q + p.qty, sid or p.security_id)
    out = []
    for sym, (q, sid) in sorted(bot.items()):
        have = by_sid.get(str(sid)) if sid and str(sid) in by_sid else by_sym.get(sym.upper(), 0)
        if have < q:
            out.append((sym, q, have))
    return out


def fix(journal, mismatches: List[Mismatch], now: datetime) -> List[str]:
    """Lower the bot's positions to what the account holds (the biggest position gives way first)."""
    notes = []
    for sym, bot_q, have in mismatches:
        excess = bot_q - have
        for p in sorted([p for p in journal.positions(product=CNC) if p.symbol == sym], key=lambda p: -p.qty):
            if excess <= 0:
                break
            cut = min(excess, p.qty)
            excess -= cut
            if cut == p.qty:
                journal.delete_position(p)
            else:
                p.entry_charges *= (p.qty - cut) / p.qty
                p.qty -= cut
                journal.save_position(p)
            msg = f"reconcile: {p.strategy} {sym} lowered by {cut} to match the account ({have} held)"
            journal.event("warning", msg)
            notes.append(msg)
    return notes
