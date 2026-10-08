"""Risk checks every order must pass, and position sizing. Exits are always allowed (they reduce risk);
entries face every check below."""
from __future__ import annotations

import math
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import BUY, CNC, INTRADAY, OrderRequest, Position


TOPUP_CLASSES = {"core_allocation"}              # strategies that may buy more of an ETF they already hold


def _t(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


class Risk:
    def __init__(self, cfg: dict, journal, root: Path):
        self.cfg = cfg
        self.j = journal
        self.root = Path(root)
        self.lim = cfg["limits"]
        self.icfg = cfg["intraday"]
        self.marks: Dict[str, float] = {}         # latest prices, set by the engine, for mark-to-market
        # live, `capital.live_from_account`: money the account really has for the live swing strategies, and
        # what those strategies would use at full size. Set by the engine before each swing run; None = off.
        self.account_equity: Optional[float] = None
        self.live_plan: Optional[float] = None
        # paper experiments (lab): tranches of one strategy may hold the same stock, each as its own position
        self.shared_symbols = bool(cfg.get("_shared_symbols"))

    # ---------- switches ----------
    def kill_switch(self) -> bool:
        return (self.root / self.lim["kill_switch_file"]).exists()

    def capital(self, engine: str) -> float:
        return float(self.cfg["capital"].get(engine, 0))

    def account_scale(self, engine: str = "swing") -> float:
        """1.0 normally; below 1 when the account holds less than the live swing plan."""
        if engine != "swing" or self.account_equity is None or not self.live_plan:
            return 1.0
        return max(0.0, min(1.0, self.account_equity / self.live_plan))

    def strategy_capital(self, name: str, engine: str) -> float:
        """The planned capital, scaled down to the money in the account when that is less (live, swing)."""
        return self.planned_capital(name, engine) * self.account_scale(engine)

    def planned_capital(self, name: str, engine: str) -> float:
        """Base allocation x share, plus the strategy's realized profit/loss if `compound` is on
        (profits are reinvested, losses shrink the next positions)."""
        share = float((self.cfg["strategies"].get(name) or {}).get("capital_share", 1.0))
        base = self.capital(engine) * share
        if self.cfg.get("compound", True):
            base += self.j.realized_total(name)
            for p in self.j.positions(strategy=name):
                m = self.marks.get(p.symbol)
                if m is not None:
                    base += (m - p.avg_price) * p.qty
        return max(0.0, base)

    def held_value(self, positions: List[Position], strategy: str, product: str = CNC) -> float:
        return sum(abs(p.qty) * self.marks.get(p.symbol, p.avg_price)
                   for p in positions if p.product == product and p.strategy == strategy)

    # ---------- sizing ----------
    def note_deployed(self, name: str) -> None:
        """Remember the largest capital this live strategy has been sized with (survives restarts). Only from a
        known account figure, at cost (no unrealized gains), and reset when its configured plan changes."""
        if self.account_equity is None and self.cfg["capital"].get("live_from_account", True):
            return                                         # funds unknown this run: say nothing about the peak
        share = float((self.cfg["strategies"].get(name) or {}).get("capital_share", 1.0))
        base = self.capital("swing") * share
        at_cost = (base + (self.j.realized_total(name) if self.cfg.get("compound", True) else 0.0)) * self.account_scale()
        rec = self.j.get(f"{name}:deployed_peak") or {}
        if not isinstance(rec, dict) or rec.get("base") != base:
            rec = {"base": base, "peak": 0.0}
        if at_cost > rec["peak"]:
            rec["peak"] = at_cost
            self.j.put(f"{name}:deployed_peak", rec)
        elif not self.j.get(f"{name}:deployed_peak"):
            self.j.put(f"{name}:deployed_peak", rec)

    def drawdown_base(self, name: str, engine: str) -> float:
        """What the live drawdown limit is measured against: the most capital the strategy has actually been
        sized with. Moving cash out of the account (which shrinks today's sizing) can't trigger a demotion,
        and a strategy run on a small account isn't judged against a plan it never had the money for."""
        rec = self.j.get(f"{name}:deployed_peak") or {}
        peak = float(rec.get("peak", 0)) if isinstance(rec, dict) else 0.0
        return peak if peak > 0 else self.planned_capital(name, engine)

    def size_swing(self, strategy: str, price: float, slots: int, held_value: float, scaled: bool = True) -> int:
        cap = self.strategy_capital(strategy, "swing") if scaled else self.planned_capital(strategy, "swing")
        per = min(cap / max(1, slots), self.lim["max_order_value"])
        room = cap - held_value
        budget = max(0.0, min(per, room))
        return int(math.floor(budget / (price * 1.003))) if price > 0 else 0

    def size_intraday(self, strategy: str, entry: float, stop: Optional[float]) -> int:
        cap = self.strategy_capital(strategy, "intraday")
        by_value = cap * float(self.icfg["max_position_pct"]) / 100
        by_value = min(by_value, self.lim["max_order_value"])
        qty = math.floor(by_value / entry) if entry > 0 else 0
        if stop is not None and abs(entry - stop) > 0:
            risk_rs = cap * float(self.icfg["risk_per_trade_pct"]) / 100
            qty = min(qty, math.floor(risk_rs / abs(entry - stop)))
        return max(0, int(qty))

    # ---------- checks ----------
    def check(self, req: OrderRequest, now: datetime, ltp: Optional[float],
              positions: List[Position], funds: Optional[float] = None) -> Tuple[bool, str]:
        if req.qty < 1:
            return False, "quantity below 1"
        if self.j.order_exists(req.tag):
            return False, "duplicate order tag (already sent)"
        if req.kind == "exit":
            # exits are always allowed (they reduce risk), but never two at once for the same position
            if self.j.active_orders(req.strategy, req.symbol, "exit", req.product):
                return False, "an exit order for this position is already working"
            held = sum(abs(p.qty) for p in positions if p.strategy == req.strategy and p.symbol == req.symbol
                       and p.product == req.product)
            if req.qty > held:
                return False, f"exit of {req.qty} exceeds the {held} held"
            return True, "exit"
        # ----- entries only below -----
        if self.j.orders_today(now.date()) >= int(self.lim["max_orders_per_day"]):
            return False, "daily order limit reached"
        if self.kill_switch():
            return False, f"kill switch file '{self.lim['kill_switch_file']}' present"
        value = req.qty * req.limit_price
        sc = self.cfg["strategies"].get(req.strategy) or {}
        cap_value = float(sc.get("max_order_value") or self.lim["max_order_value"])   # a strategy's own cap wins
        park = sc.get("park")
        park_sym = (park if isinstance(park, str) else (park or {}).get("symbol", "")) or ""
        if park_sym and req.symbol == park_sym.upper():
            # parking idle cash in a liquid ETF: one order for the whole idle amount, never more than the
            # strategy's own capital (or `park.max_order_value` if set)
            explicit = (park if isinstance(park, dict) else {}).get("max_order_value")
            cap_value = max(cap_value, float(explicit) if explicit else
                            self.strategy_capital(req.strategy, "swing") * 1.05)
        if value > cap_value * 1.001:
            return False, f"order value ₹{value:,.0f} above max_order_value"
        if ltp is None and not req.amo:
            return False, "no live price"
        band = float(self.lim["price_band_pct"]) / 100 + 0.005
        ref = ltp if ltp is not None else req.ref_price
        if ref and abs(req.limit_price / ref - 1) > (0.03 if req.amo else band):
            return False, f"limit {req.limit_price} too far from price {ref}"
        mine = req.strategy if self.shared_symbols else None
        topup = sc.get("class", req.strategy) in TOPUP_CLASSES      # target-weight strategies add to what they hold
        if any(p.symbol == req.symbol and p.product == req.product and (mine is None or p.strategy == mine)
               and not (topup and p.strategy == req.strategy) for p in positions):
            return False, "already holding this symbol"
        if self.j.active_orders(mine, req.symbol, "entry", req.product):
            return False, "an entry order for this symbol is already working"
        if req.kind == "entry" and not req.signal_id.startswith("reprice") and \
                self.j.entry_sent_today(req.strategy, req.symbol, req.product, now.date()):
            return False, "already entered this symbol today (restart-safe de-duplication)"
        if req.product == INTRADAY:
            ok, why = self._intraday(req, now, positions)
            if not ok:
                return ok, why
        else:
            held = self.held_value(positions, req.strategy)
            pending = sum(o.req.qty * o.req.limit_price for o in
                          self.j.active_orders(req.strategy, kind="entry", product=CNC))
            cap = self.strategy_capital(req.strategy, "swing")
            if held + pending + value > cap * 1.02:
                return False, f"swing capital for {req.strategy} fully used (₹{held + pending:,.0f} of ₹{cap:,.0f})"
        if funds is not None and funds < value * 1.01:
            return False, f"available funds ₹{funds:,.0f} below order value ₹{value:,.0f}"
        return True, "ok"

    def _intraday(self, req: OrderRequest, now: datetime, positions: List[Position]) -> Tuple[bool, str]:
        t = now.time()
        if t < _t(self.icfg["first_entry"]) or t > _t(self.icfg["last_entry"]):
            return False, "outside intraday entry window"
        intra = [p for p in positions if p.product == INTRADAY]
        working = self.j.active_orders(kind="entry", product=INTRADAY)
        if len(intra) + len(working) >= int(self.icfg["max_positions"]):
            return False, "max intraday positions open (including working entry orders)"
        if self.j.entries_today(now.date(), INTRADAY) >= int(self.icfg["max_entries_per_day"]):
            return False, "max intraday entries for today"
        unreal = sum((self.marks.get(p.symbol, p.avg_price) - p.avg_price) * p.qty for p in intra)
        loss = -(self.j.realized_today(now.date(), INTRADAY) + unreal)
        limit = self.capital("intraday") * float(self.icfg["daily_loss_limit_pct"]) / 100
        if loss >= limit:
            return False, f"daily intraday loss limit hit (₹{loss:,.0f} incl. open positions)"
        exposure = sum(abs(p.qty) * self.marks.get(p.symbol, p.avg_price) for p in intra) + \
            sum(o.req.qty * o.req.limit_price for o in working)
        if exposure + req.qty * req.limit_price > self.capital("intraday") * 1.02:
            return False, "intraday capital fully used"
        if req.stop is None:
            return False, "intraday entries need a stop"
        if (req.side == BUY and req.stop >= req.limit_price) or (req.side != BUY and req.stop <= req.limit_price):
            return False, "stop on the wrong side of the entry"
        return True, "ok"
