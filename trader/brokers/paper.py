"""Paper broker: fills orders against real prices without sending anything to the exchange.

Fill rules (the same assumptions as the research, so paper results are comparable):
- Marketable LIMIT order: fills at the last traded price at placement +/- slippage, if inside the limit.
  Otherwise it waits and fills at the limit once a later 5-minute bar trades through it.
- AMO order (placed after the close): fills at the next session's opening price +/- slippage if inside
  the limit, else at the limit if the day trades through it.
- Stop/target legs of an intraday entry: checked on every completed bar after the fill. If one bar
  touches both, the stop is assumed (worst case). Gaps through a level fill at the bar's open.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from ..models import BUY, CANCELLED, FILLED, NEW, OPEN, Order, TERMINAL
from .base import Broker, CostModel, Fill


def floor5(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)


class PaperBroker(Broker):
    name = "paper"
    live = False

    def __init__(self, market, costs: CostModel = None):
        self.market = market
        self.costs = costs or CostModel()
        self.children: Dict[str, dict] = {}       # entry tag -> active stop/target leg
        self.fresh: set = set()                    # placed, not yet checked against the price

    # ---------- orders ----------
    def place(self, order: Order, now: datetime) -> Order:
        order.status, order.broker_id, order.updated = OPEN, f"PAPER-{order.req.tag}", now
        if not order.req.amo:
            self.fresh.add(order.req.tag)
        return order

    def cancel(self, order: Order, now: datetime) -> Order:
        if order.status not in TERMINAL:
            order.status, order.updated = CANCELLED, now
        return order

    def cancel_child(self, order: Order, now: datetime) -> bool:
        self.children.pop(order.req.tag, None)
        return True

    def restore_child(self, tag: str, symbol: str, product: str, qty: int, exit_side: str,
                      stop, target, since: datetime) -> None:
        """Re-arm a stop/target leg after a restart (paper state lives in the journal)."""
        self.children[tag] = dict(symbol=symbol, product=product, qty=qty, side=exit_side,
                                  stop=stop, target=target, since=since)

    def _fill(self, order: Order, px: float, now: datetime) -> Fill:
        r = order.req
        slip = self.costs.slippage_pct(r.product, r.symbol) / 100
        px = px * (1 + slip) if r.side == BUY else px * (1 - slip)
        ch = self.costs.charges(r.product, r.side, px * r.qty, r.symbol)
        order.status, order.filled_qty, order.avg_price, order.charges, order.updated = FILLED, r.qty, px, ch, now
        if r.stop is not None or r.target is not None:
            self.children[r.tag] = dict(symbol=r.symbol, product=r.product, qty=r.qty,
                                        side="SELL" if r.side == BUY else BUY,
                                        stop=r.stop, target=r.target, since=floor5(now))
        return Fill(r.tag, r.qty, px, now, ch, "main", r.side)

    def update(self, orders: List[Order], now: datetime) -> List[Fill]:
        fills: List[Fill] = []
        for o in orders:
            if o.status not in (NEW, OPEN):
                continue
            r = o.req
            if self._expired(o, now):
                o.status, o.updated, o.message = CANCELLED, now, "expired (DAY order)"
                continue
            if r.amo:
                px = self._amo_price(o, now)
            elif r.tag in self.fresh:
                # first look right after placement: the price we would have hit
                self.fresh.discard(r.tag)
                ltp = self.market.ltp([r.symbol], now).get(r.symbol)
                px = ltp if ltp is not None and _inside(r.side, ltp, r.limit_price) else None
            else:
                px = self._resting_price(o, now)
            if px is not None:
                fills.append(self._fill(o, px, now))
        fills += self._check_children(now)
        return fills

    def _expired(self, o: Order, now: datetime) -> bool:
        """DAY orders die at the close: a normal order after its own day, an AMO after the session it was for."""
        r = o.req
        if not r.amo:
            return now.date() > r.created.date()
        day = self.market.next_session_after(r.created)
        return day is not None and now.date() > day

    def _amo_price(self, o: Order, now: datetime):
        r = o.req
        day = self.market.next_session_after(r.created)
        if day is None or now.date() < day:
            return None
        op = self.market.day_open(r.symbol, day)
        if op is None:
            return None
        if _inside(r.side, op, r.limit_price):
            return op
        return self._resting_price(o, now, since=datetime.combine(day, datetime.min.time()))

    def _resting_price(self, o: Order, now: datetime, since: datetime = None):
        r = o.req
        bars = self.market.bars(r.symbol, since or floor5(r.created) + timedelta(minutes=5), now)
        for _, b in bars.iterrows():
            if r.side == BUY and b["low"] <= r.limit_price:
                return min(b["open"], r.limit_price)
            if r.side != BUY and b["high"] >= r.limit_price:
                return max(b["open"], r.limit_price)
        return None

    def _check_children(self, now: datetime) -> List[Fill]:
        out = []
        for tag, c in list(self.children.items()):
            bars = self.market.bars(c["symbol"], c["since"], now)
            for t, b in bars.iterrows():
                hit = None
                if c["side"] == "SELL":                      # protecting a long
                    if c["stop"] is not None and b["low"] <= c["stop"]:
                        hit = ("stop", min(b["open"], c["stop"]))
                    elif c["target"] is not None and b["high"] >= c["target"]:
                        hit = ("target", max(b["open"], c["target"]))
                else:                                        # protecting a short
                    if c["stop"] is not None and b["high"] >= c["stop"]:
                        hit = ("stop", max(b["open"], c["stop"]))
                    elif c["target"] is not None and b["low"] <= c["target"]:
                        hit = ("target", min(b["open"], c["target"]))
                if hit:
                    leg, px = hit
                    slip = self.costs.slippage_pct(c["product"], c["symbol"]) / 100
                    px = px * (1 - slip) if c["side"] == "SELL" else px * (1 + slip)
                    ch = self.costs.charges(c["product"], c["side"], px * c["qty"], c["symbol"])
                    when = t.to_pydatetime() + timedelta(minutes=5) if hasattr(t, "to_pydatetime") else now
                    out.append(Fill(tag, c["qty"], px, when, ch, leg, c["side"]))
                    del self.children[tag]
                    break
            else:
                if len(bars):
                    c["since"] = bars.index[-1].to_pydatetime() + timedelta(minutes=5)
        return out


def _inside(side: str, px: float, limit: float) -> bool:
    return px <= limit if side == BUY else px >= limit
