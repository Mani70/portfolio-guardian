"""Hand the owner's existing stocks to a live strategy (by default momentum_rotation).

  python -m trader.run adopt              # shows what would be handed over, asks for YES
  python -m trader.run adopt --yes

Every delivery holding in the account that the bot does not already own (and that isn't pledged or already
sold) becomes a live position of the strategy at TODAY'S PRICE - the strategy's record starts now, so your
past gains or losses never count as its results (your own cost is kept alongside, for tax records). From the strategy's next evening run its normal rules apply:
holdings it would not choose are sold (after-market orders for the next open, listed on Telegram the evening
before - `python -m trader.run cancel` before 09:00 stops them), the rest are kept, and freed money is used
for its own picks. Research: research/FINDINGS.md, "managing an existing stock portfolio".
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from .models import CNC, Position

Plan = List[Tuple[str, int, float, str]]          # symbol, qty, average cost, security_id


def plan(holdings: Iterable, bot_positions: List[Position], skip: Iterable[str] = ()) -> Plan:
    """Holdings minus what the bot itself holds (the owner may hold more of the same ETF than the bot)."""
    bot: Dict[str, int] = {}
    for p in bot_positions:
        if p.product == CNC and p.qty > 0:
            bot[p.symbol.upper()] = bot.get(p.symbol.upper(), 0) + p.qty
    skip = {s.upper() for s in skip}
    out = []
    for h in holdings:
        sym = h.symbol.upper()
        qty = int(round(float(h.qty) - float(getattr(h, "used_qty", 0) or 0))) - bot.get(sym, 0)
        if qty >= 1 and sym not in skip:
            out.append((sym, qty, float(h.avg_price or 0.0), str(h.security_id or "")))
    return sorted(out)


def adopt(journal, strategy: str, items: Plan, prices: Dict[str, float], now: datetime) -> List[str]:
    """items: from plan(); prices: today's price per symbol (symbols without one are skipped)."""
    notes = []
    for sym, qty, cost, sid in items:
        px = prices.get(sym)
        if not px:
            notes.append(f"{sym}: no live price, NOT handed over")
            continue
        if any(p.symbol == sym for p in journal.positions(product=CNC, strategy=strategy)):
            notes.append(f"{sym}: already managed by {strategy}, skipped")
            continue
        journal.save_position(Position(strategy, sym, CNC, qty, px, now, f"adopted:{sym}:{now:%y%m%d}", sid,
                                       meta={"adopted": True, "owner_cost": cost}))
        journal.put(f"owner_cost:{strategy}:{sym}", cost)
        journal.event("warning", f"adopt: {strategy} took over {qty} {sym} at ₹{px:,.2f} (your cost ₹{cost:,.2f})")
        notes.append(f"{sym}: {qty} handed to {strategy} at ₹{px:,.2f} (your cost ₹{cost:,.2f})")
    return notes


def release(journal, strategy: str, symbols: Iterable[str]) -> List[str]:
    """Give handed-over holdings back to the owner: the bot forgets them (no order is sent). Refused while a
    sell for that stock is working - cancel it first with `trader.run cancel`."""
    want = {s.strip().upper() for s in symbols if s.strip()}
    notes = []
    for p in journal.positions(product=CNC, strategy=strategy):
        if not (p.meta or {}).get("adopted") or ("ALL" not in want and p.symbol not in want):
            continue
        if journal.active_orders(strategy, p.symbol, "exit", CNC):
            notes.append(f"{p.symbol}: a sell is working - run `trader.run cancel` first; not released")
            continue
        journal.delete_position(p)
        journal.event("warning", f"release: {p.qty} {p.symbol} given back to the owner by {strategy}")
        notes.append(f"{p.symbol}: {p.qty} given back to you; {strategy} no longer manages it")
    return notes or ["Nothing to release."]
