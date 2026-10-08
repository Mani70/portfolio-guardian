"""The trading engine: turns strategy signals into orders, tracks fills, positions and P&L.

One Engine = one mode (paper or live) with its own journal and broker. The same code runs live,
in paper, and in historical replay (trader.replay), so what was tested is what trades.

Swing (delivery, CNC):
    swing_plan(now)   after the close: strategies decide; orders go in as AMO limit orders for the next
                      session's opening auction (limit = close +/- amo_band, so a big gap is not chased).
    swing_check(now)  ~09:30: record fills; cancel what didn't fill and, once the cancel is confirmed,
                      re-price exits (every morning until done) and entries (once, within max_chase).
Intraday (MIS):
    intraday_start(day, now) before the open; intraday_step(now) after every 5-minute bar;
    positions carry a stop (a GTT leg when live) and are squared off at `square_off`.

Order-safety rules: an order is journaled before it is sent; placement is never retried; an exit is
never sent while another exit (or an unconfirmed cancel, or an unresolved lost reply) is working for the
same position, and never for more than the position holds; a stop leg must be confirmed cancelled
before the engine sends its own exit.
"""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import corporate
from .corporate import describe as describe_ratio
from .brokers.base import Fill
from .gates import check_live_drawdown, is_demoted, paper_gate
from .instruments import round_tick
from .models import (ACTIVE, BUY, CANCEL_SENT, CANCELLED, CNC, FILLED, INTRADAY, NEW, OPEN, PARTIAL, REJECTED, SELL,
                     UNKNOWN, Order, OrderRequest, Position, Signal)
from .risk import Risk, _t

log = logging.getLogger("trader")
# live entries blocked for these reasons are worth a Telegram alert (duplicates and "already holding" are not)
BAND_REJECT = re.compile(r"price band|circuit|\bdpr\b|outside (the )?(daily )?(price )?(band|range)|price range",
                         re.I)                    # INDstocks/NSE price-band rejections
EXPIRED = re.compile(r"expir", re.I)              # set by brokers/indstocks.py for EXPIRED order-book rows
BROKER_TRANSIENT = re.compile(r"try again|problem in placing|technical|temporar|server error|internal error|"
                              r"service unavailable|timed? ?out", re.I)   # INDstocks' own failures, not the order's
BY_EXCHANGE = re.compile(r"exchange|corporate action|record date|ex-?date|system|\bsurveillance\b", re.I)
ALERT_BLOCKS = ("funds", "kill switch", "order limit", "max_order_value", "capital", "no live price", "too far")


class Engine:
    def __init__(self, cfg: dict, journal, broker, market, instruments, notifier, strategies: list,
                 root: Path, universe: List[str], bench: str = "NIFTYBEES", quiet: bool = False,
                 frozen: bool = False):
        self.cfg = cfg
        self.j = journal
        self.broker = broker
        self.market = market
        self.inst = instruments
        self.notifier = notifier
        self.root = Path(root)
        self.universe = universe
        self.bench = bench
        self.quiet = quiet
        self.mode = journal.mode
        self.risk = Risk(cfg, journal, root)
        self.strategies = []
        self.skipped: Dict[str, str] = {}
        self._funds_left: Dict[str, float] = {}       # live: money left for entries in this run, per product
        self.last_block, self.last_block_funds = "", False
        self.sizing_failed = False
        self._funds_alert_day = None                  # "can't read funds" is said once a day, not every 5 minutes
        self.exit_only = []                           # live strategies that may only close what they hold
        self.frozen = frozen                          # live engine kept only to track/cancel orders (mode: paper)
        held_by = {p.strategy for p in journal.positions()} if self.broker.live else set()
        for s in ([] if frozen else strategies):
            ok, why = self._allowed(s)
            if ok:
                self.strategies.append(s)
            else:
                self.skipped[s.name] = why
                # demoted or not past its paper check: what it already holds is still sold by its rules.
                # `live: false` is the owner's off switch: then nothing is sent for it at all.
                if s.name in held_by and (self.cfg["strategies"].get(s.name) or {}).get("live"):
                    self.exit_only.append(s)
        from .holidays import load as load_holidays
        holidays = load_holidays(self.root, cfg)
        if hasattr(market, "set_holidays"):
            market.set_holidays(holidays)               # paper after-market orders fill on the real next session
        for s in self.strategies + self.exit_only:
            s.journal = journal
            s.root = self.root                          # files a strategy may read (trader/index_data.py)
            if hasattr(s, "holidays"):
                s.holidays = holidays
        self._dd_check = set()
        self._squared = False
        self._park_warned = ""

    # ---------- plumbing ----------
    def _say(self, text: str, force: bool = False) -> None:
        log.info("[%s] %s", self.mode, text.replace("\n", " | "))
        if self.mode == "lab":
            return                                      # paper experiments: log only (failures: run._run_each)
        if self.notifier and (force or not self.quiet):
            self.notifier.send(f"[{self.mode.upper()}] {text}")

    def _allowed(self, strat):
        if not self.broker.live:
            return True, "paper"
        sc = self.cfg["strategies"].get(strat.name) or {}
        if not sc.get("live"):
            return False, "live: false in trader.yaml"
        if is_demoted(self.root, strat.name):
            return False, "demoted to paper after a live drawdown (delete trader/state/demoted.json to re-arm)"
        jp = self.root / self.cfg["journal"]
        ok, why, _ = paper_gate(jp, strat.name, strat.engine, self.cfg)
        if ok:
            return True, "paper record passed the gates"
        if sc.get("override_gate"):
            log.warning("%s: gate not passed (%s) but override_gate is set", strat.name, why)
            return True, f"OVERRIDE ({why})"
        return False, f"paper record not proven yet: {why}"

    def _tag(self, sig: Signal, now: datetime, n: int = 0) -> str:
        return f"{sig.strategy[:24]}:{sig.symbol}:{sig.side[0]}{sig.kind[0]}:{now:%y%m%d%H%M%S}" + (f":{n}" if n else "")

    def _tick(self, symbol: str) -> float:
        return self.inst.tick(symbol) if self.inst else 0.05

    def _sid(self, symbol: str) -> str:
        return (self.inst.security_id(symbol) if self.inst else None) or ""

    def submit(self, sig: Signal, qty: int, limit: float, now: datetime, amo: bool = False,
               positions: Optional[List[Position]] = None, n: int = 0, reprice_of: str = "",
               wait_for_sells: bool = False) -> Optional[Order]:
        """`wait_for_sells` (swing plan with sells going out the same evening): if the money isn't there yet,
        don't buy a smaller position - skip and retry the next evening, when the sale proceeds have arrived."""
        tick = self._tick(sig.symbol)
        limit = round_tick(limit, tick, "up" if sig.side == BUY else "down")
        req = OrderRequest(tag=self._tag(sig, now, n), strategy=sig.strategy, symbol=sig.symbol,
                           security_id=self._sid(sig.symbol), side=sig.side, qty=int(qty), product=sig.product,
                           limit_price=limit, signal_id=f"reprice:{reprice_of}" if reprice_of else sig.id,
                           kind=sig.kind, created=now, stop=sig.stop, target=sig.target, ref_price=sig.ref_price,
                           amo=amo)
        self.last_block, self.last_block_funds = "", False
        if self.frozen:
            self.last_block = "live trading is switched off (mode: paper); nothing new is sent"
            self.j.record_signal(sig, False, self.last_block)
            self._say(f"Not sent: {sig.side} {sig.symbol}: {self.last_block}", force=True)
            return None
        if self.broker.live and not req.security_id:
            self.last_block = "unknown security_id"
            self.j.record_signal(sig, False, self.last_block)
            self._say(f"Order NOT sent: {sig.side} {sig.symbol} ({sig.strategy}): no INDstocks security id", force=True)
            return None
        ltp = None if amo else self.market.ltp([sig.symbol], now).get(sig.symbol)
        funds, planned, waiting = None, req.qty, False
        if self.broker.live and sig.kind == "entry":
            # real money: never more than the account holds (less the reserve). The broker's figure may not yet
            # include orders sent earlier in this run, so keep our own running total and use the lower of the two.
            fetched = self.broker.available_funds(sig.product)
            if fetched is None and self._funds_left.get(sig.product) is None:
                self.last_block, self.last_block_funds = "could not read the account's funds from INDstocks", True
                self.j.record_signal(sig, False, self.last_block)
                if self._funds_alert_day != now.date():
                    self._funds_alert_day = now.date()
                    self._say(f"Order NOT sent: {sig.side} {sig.symbol} ({sig.strategy}): {self.last_block}; "
                              "new buys are retried at the next runs.", force=True)
                return None
            if fetched is not None:
                fetched -= float(self.cfg["capital"].get("reserve", 0) or 0)
            left = self._funds_left.get(sig.product)
            funds = fetched if left is None else (left if fetched is None else min(fetched, left))
            funds = max(0.0, funds)
            if funds < req.qty * req.limit_price * 1.01:
                if wait_for_sells:
                    waiting, req.qty = True, 0
                else:
                    req.qty = max(0, int(funds / (req.limit_price * 1.01)))     # buy what the money allows
        positions = positions if positions is not None else self.j.positions()
        ok, why = self.risk.check(req, now, ltp, positions, funds)
        if waiting:
            why = (f"waiting for the money from this evening's sells (₹{funds:,.0f} free now); "
                   "it is tried again at the next evening runs")
        elif req.qty < 1 and funds is not None and planned >= 1:
            reserve = float(self.cfg["capital"].get("reserve", 0) or 0)
            why = (f"available funds ₹{funds:,.0f}" + (f" (after keeping the ₹{reserve:,.0f} reserve)" if reserve else "")
                   + f" below the price of one unit (₹{req.limit_price:,.2f})")
        self.last_block = "" if ok else why
        self.last_block_funds = not ok and (waiting or why.startswith("available funds"))
        if not reprice_of:
            self.j.record_signal(sig, ok, why)
        if not ok:
            log.info("[%s] blocked %s %s %s: %s", self.mode, sig.strategy, sig.side, sig.symbol, why)
            self.j.event("info", f"blocked {req.tag}: {why}")
            if self.broker.live and waiting:
                self._say(f"BUY {sig.symbol} ({sig.strategy}) waits: {why}.", force=True)
            elif self.broker.live and (self.last_block_funds or any(k in why for k in ALERT_BLOCKS)):
                self._say(f"Order NOT sent: {sig.side} {sig.symbol} ({sig.strategy}): {why}", force=True)
            return None
        order = Order(req=req)
        self.j.save_order(order)                       # written BEFORE sending: a crash can't lose it
        order = self.broker.place(order, now, tick=tick) if self.broker.live else self.broker.place(order, now)
        self.j.save_order(order)
        if order.status in (REJECTED, UNKNOWN):
            self._say(f"Order problem {req.tag}: {order.status} {order.message}", force=True)
        if funds is not None and order.status != REJECTED:           # an UNKNOWN order may be holding money
            self._funds_left[req.product] = funds - req.qty * req.limit_price * 1.01
            if req.qty < planned and order.status != UNKNOWN:
                self._say(f"{sig.symbol}: buying {req.qty} instead of the planned {planned} - the account has "
                          f"₹{funds:,.0f} free. The rest is not bought for this signal; add funds to trade the "
                          "full plan next time.", force=True)
        return order

    # ---------- fills & positions ----------
    def _watch_orders(self) -> List[Order]:
        active = self.j.load_orders(statuses=ACTIVE)
        if self.broker.live:
            tags = {o.req.tag for o in active}
            open_tags = {p.entry_tag for p in self.j.positions()}
            active += [o for o in self.j.load_orders(statuses=[FILLED, PARTIAL, CANCELLED])
                       if o.child_id and o.req.tag in open_tags and o.req.tag not in tags]
        return active

    def sync(self, now: datetime) -> List[Fill]:
        orders = self._watch_orders()
        if not orders and not getattr(self.broker, "children", None):
            return []
        by_tag = {o.req.tag: o for o in orders}
        before = {o.req.tag: o.status for o in orders}
        fills = self.broker.update(orders, now)
        for o in orders:
            self.j.save_order(o)
            if (self.broker.live and o.status == CANCELLED and o.req.kind == "exit" and o.req.product == CNC
                    and before.get(o.req.tag) in (NEW, OPEN, PARTIAL) and o.req.qty - o.filled_qty >= 1):
                # ended without our cancel (ours pass through CANCEL_SENT)
                if EXPIRED.search(o.message or "") or BY_EXCHANGE.search(o.message or ""):
                    self.j.put(f"ext_cancel:{o.req.tag}", now.isoformat())      # unfilled / exchange: re-sent
                    self.j.event("warning", f"exit {o.req.tag} ended unfilled ({o.message}); re-sent at the next "
                                            "morning check")
                else:
                    self._say(f"The bot's sell of {o.req.symbol} ({o.req.strategy}) was cancelled outside the bot "
                              f"({o.message or 'no reason given'}) - by you in the app, or by the exchange. It is NOT "
                              f"sent again. To keep the stock: `trader.run adopt --release {o.req.symbol}`; otherwise "
                              "the strategy sells it at its next decision.", force=True)
            if self.broker.live and o.status == REJECTED and before.get(o.req.tag) != REJECTED:
                reopened = o.req.kind == "entry" and self._reopen_after_broker_error(o, now)
                self._say(f"Order REJECTED after it was accepted: {o.req.side} {o.req.qty} {o.req.symbol} "
                          f"({o.req.strategy}): {o.message or 'no reason given'}"
                          + (" - the sell is tried again at the morning check." if o.req.kind == "exit"
                             else " - an INDstocks-side error: the strategy decides again at this evening's plan "
                                  "(16:10) and re-places it for the next open." if reopened
                             else " - this buy is not retried until the strategy's next decision."),
                          force=True)
        for f in fills:
            o = by_tag.get(f.tag) or self.j.order(f.tag)
            if o is None:
                self.j.event("error", f"fill for unknown order {f.tag}")
                continue
            self._apply_fill(f, o)
        self._check_drawdowns()
        return fills

    def _reopen_after_broker_error(self, o: Order, now: datetime) -> bool:
        """A swing buy that INDstocks accepted and then rejected for its OWN problem ("We experienced problem in
        placing your trade. Please try again...") is not a decision against the buy: the strategy's last decision
        is reopened so the evening plan decides again (with that evening's prices) and re-places it. At most 3
        times a week per strategy, so a lasting problem doesn't repeat every day."""
        r = o.req
        if r.product != CNC or not BROKER_TRANSIENT.search(o.message or "") or (now - r.created).days > 3:
            return False
        key = f"{r.strategy}:reopened"
        recent = [t for t in (self.j.get(key) or []) if now - datetime.fromisoformat(t) < timedelta(days=7)]
        if len(recent) >= 3 or self.j.get(f"{r.strategy}:last_period") is None:
            return False
        self.j.put(key, recent + [now.isoformat()])
        self.j.put(f"{r.strategy}:last_period", None)
        self.j.event("warning", f"{r.strategy}: decision reopened after INDstocks rejected {r.tag} ({o.message})")
        return True

    def _check_drawdowns(self) -> None:
        """Live: once per sync, for each strategy that closed something, the drawdown limit."""
        names, self._dd_check = self._dd_check, set()
        for name in sorted(names):
            if not any(s.name == name for s in self.strategies):
                continue                                # already exit-only
            strat = next(s for s in self.strategies if s.name == name)
            limit = (self.cfg["strategies"].get(name) or {}).get("max_drawdown_pct",
                                                                 self.cfg["gates"]["live_max_drawdown_pct"])
            hit, pct = check_live_drawdown(self.j, name, self.risk.drawdown_base(name, strat.engine),
                                           float(limit), self.root)
            if hit:
                self.strategies = [s for s in self.strategies if s.name != name]
                self.exit_only.append(strat)
                self._say(f"{name}: live drawdown {pct:.1f}% of its capital. Sent back to paper: no new live "
                          f"entries; what it holds is still sold by its rules (exits, stops, square-off).", force=True)

    def settle(self, now: datetime, tries: int = 4) -> None:
        """Sync until no cancel is pending (live: give INDstocks a few seconds to confirm)."""
        for _ in range(tries):
            self.sync(now)
            if not self.broker.live or not self.j.load_orders(statuses=[CANCEL_SENT]):
                return
            _time.sleep(2)

    def _find_position(self, strategy: str, symbol: str, product: str) -> Optional[Position]:
        for p in self.j.positions(product=product, strategy=strategy):
            if p.symbol == symbol:
                return p
        return None

    def _apply_fill(self, f: Fill, o: Order) -> None:
        r = o.req
        pos = self._find_position(r.strategy, r.symbol, r.product)
        if f.leg == "main" and r.kind == "entry":
            signed = f.qty if r.side == BUY else -f.qty
            if pos is None:
                pos = Position(r.strategy, r.symbol, r.product, signed, f.price, f.time, r.tag, r.security_id,
                               r.stop, r.target, f.charges, o.child_id, {})
            else:
                new_qty = pos.qty + signed
                pos.avg_price = (pos.avg_price * abs(pos.qty) + f.price * f.qty) / abs(new_qty)
                pos.qty, pos.entry_charges = new_qty, pos.entry_charges + f.charges
                # a topped-up position's average cost matches no single day: the bonus/split check
                # (corporate.ca_ref) compares the last fill with its own day's candle instead
                pos.meta = {**(pos.meta or {}), "ca_px": f.price, "ca_day": f.time.date().isoformat()}
            self.j.save_position(pos)
            self._say(f"{'Bought' if r.side == BUY else 'Sold short'} {f.qty} {r.symbol} @ ₹{f.price:,.2f} "
                      f"({r.strategy}{', stop ₹%.2f' % r.stop if r.stop else ''})")
            return
        if pos is None or (f.leg != "main" and pos.entry_tag != f.tag):
            self.j.event("warning", f"exit fill {f.tag}/{f.leg} x{f.qty} but no matching open position for {r.symbol}")
            self._say(f"Unmatched exit fill for {r.symbol} ({f.leg}, {f.qty} @ ₹{f.price:,.2f}). "
                      f"Check the broker's positions.", force=self.broker.live)
            return
        q = min(f.qty, abs(pos.qty))
        reason = f.leg if f.leg != "main" else "exit"
        if (pos.meta or {}).get("owner_cost") is not None:      # handed-over stock: keep the owner's cost for tax
            self.j.put(f"owner_cost:{r.strategy}:{r.symbol}", pos.meta["owner_cost"])
        net = self.j.record_trade(pos, q, f.price, f.time, f.charges, r.tag if f.leg == "main" else f"{r.tag}/{f.leg}",
                                  reason)
        remaining = abs(pos.qty) - q
        if remaining <= 0:
            self.j.delete_position(pos)
        else:
            pos.entry_charges *= remaining / abs(pos.qty)
            pos.qty = remaining if pos.qty > 0 else -remaining
            self.j.save_position(pos)
        self._say(f"Closed {q} {r.symbol} @ ₹{f.price:,.2f} ({reason}), net ₹{net:,.0f} [{r.strategy}]")
        if self.broker.live:
            self._dd_check.add(r.strategy)

    # ---------- bonus issues and splits ----------
    def corporate_actions(self, now: datetime, frames=None) -> bool:
        """Adjust held delivery positions for bonus issues and splits (trader/corporate.py). True if any changed.
        Live: also keeps each position's last confirmed account share count, and holds its sells while new
        shares are awaited."""
        positions = [p for p in self.j.positions(product=CNC) if p.qty > 0]
        self._last_close = {}
        if not positions:
            return False
        live = self.broker.live
        try:
            if frames is None:
                frames = self.market.daily(sorted({p.symbol for p in positions}), now)
            self._last_close = {k: float(v["close"].iloc[-1]) for k, v in frames.items() if len(v)}
        except Exception as e:                                       # noqa: BLE001 - never block the run
            log.warning("[%s] bonus/split check failed: %s", self.mode, e)
            return False
        acct = None
        if live:
            try:
                acct = corporate.account_qty(self.broker.holdings())
            except Exception as e:                                   # noqa: BLE001 - waits still time out
                log.warning("[%s] holdings unavailable for the bonus/split check: %s", self.mode, e)
        changed = False
        for p in positions:
            wk = f"ca_wait:{p.strategy}:{p.symbol}"
            try:
                a = corporate.find_one(p, frames.get(p.symbol), self._ca_rejected(p), loose=live)
            except Exception as e:                                   # noqa: BLE001
                log.warning("[%s] bonus/split check of %s failed: %s", self.mode, p.symbol, e)
                continue
            if a is None:
                if self.j.get(wk):
                    self.j.put(wk, None)
                if acct is not None:                    # remember the account's count while nothing is pending
                    have = corporate.held(acct, p)
                    mine = sum(q.qty for q in positions if q.symbol == p.symbol)
                    if have >= mine and (p.meta or {}).get("acct_qty") != have:
                        p.meta = {**(p.meta or {}), "acct_qty": have}
                        self.j.update_position_meta(p)
                continue
            busy = bool(self.j.active_orders(p.strategy, p.symbol, product=CNC))   # an order for the old count
            if a.factor > 1:
                key = f"ca_note:{p.strategy}:{p.symbol}:{a.when.isoformat()}"
                if not self.j.get(key):
                    self.j.put(key, now.isoformat())
                    self._say(f"{p.symbol}: the price moved x{a.factor:g} ({describe_ratio(a.factor)}). Not applied "
                              f"automatically. If the shares were consolidated, run: .venv/bin/python -m trader.run "
                              f"split --stock {p.symbol} --ratio {1 / a.factor:g}", force=True)
                continue
            if live and (busy or acct is None or not corporate.confirmed(acct, p, a)):
                self._ca_wait(p, a, now)                # holds its sells; applied at a later run
                continue
            if busy:
                continue
            if self.j.get(wk):
                self.j.put(wk, None)
            self._say(corporate.apply(self.j, a, now), force=live)
            changed = True
        return changed

    def _ca_rejected(self, p: Position) -> List[str]:
        return list(self.j.get(f"ca_rejected:{p.strategy}:{p.symbol}") or [])

    def _ca_wait(self, p: Position, a, now: datetime) -> None:
        """New shares not in the account yet: say so once, hold sells; after WAIT_SESSIONS call it a real fall."""
        wk = f"ca_wait:{p.strategy}:{p.symbol}"
        w = self.j.get(wk)
        if not w or w.get("when") != a.when.isoformat():
            self.j.put(wk, {"when": a.when.isoformat(), "since": now.date().isoformat(), "entry": p.entry_tag})
            self._say(corporate.pending_text(a), force=True)
            return
        try:
            since = date.fromisoformat(str(w.get("since")))
        except ValueError:
            since = now.date()
        waited = sum(1 for k in range(1, (now.date() - since).days + 1)
                     if (since + timedelta(days=k)).weekday() < 5)
        if waited >= corporate.WAIT_SESSIONS:
            self.j.put(f"ca_rejected:{p.strategy}:{p.symbol}", self._ca_rejected(p) + [a.when.isoformat()])
            self.j.put(wk, None)
            self._say(f"{p.symbol}: no new shares arrived {waited} sessions after the price move of {a.when:%d %b}, "
                      "so the bot treats it as a real price fall and stops waiting (its sells go out again). If a "
                      f"bonus or split did happen, run: .venv/bin/python -m trader.run split --stock {p.symbol} "
                      f"--ratio {1 / a.factor:g}", force=True)

    def _ca_hold(self, p: Position, now: datetime, ref: Optional[float] = None, price_check: bool = True) -> Optional[str]:
        """Live: why a sell of this position must wait for a bonus/split, or None. price_check (mornings): today's
        price against the last close (or `ref`, the price the sell was decided at, if the daily data failed)."""
        if not self.broker.live:
            return None
        w = self.j.get(f"ca_wait:{p.strategy}:{p.symbol}")
        if w and w.get("entry", p.entry_tag) == p.entry_tag:
            return "its bonus/split shares are not in the account yet"
        pc = (getattr(self, "_last_close", None) or {}).get(p.symbol) or ref
        if price_check and pc:
            try:
                ltp = self.market.ltp([p.symbol], now).get(p.symbol)
            except Exception as e:                                   # noqa: BLE001
                log.warning("ltp for the ex-date check of %s failed: %s", p.symbol, e)
                ltp = None
            r = corporate.standard(ltp / pc, tol=corporate.GAP_TOL) if ltp else None
            if r is not None and r < 1 and now.date().isoformat() not in self._ca_rejected(p):
                return (f"its price is x{ltp / pc:.2f} of the last close, like a {describe_ratio(r)} going ex today; "
                        "the bot waits for tomorrow's data")
        return None

    def _defer_exit(self, p: Position, now: datetime, why: str, ref: Optional[float] = None) -> None:
        """ref: the price the sell was decided at (the ex-date check's fallback if daily data is missing)."""
        key = f"deferred_exit:{p.strategy}:{p.symbol}"
        d = self.j.get(key)
        first = not d or d.get("entry") != p.entry_tag
        if first:
            self.j.put(key, {"entry": p.entry_tag, "since": now.isoformat(), "ref": ref})
        said = f"deferred_said:{p.strategy}:{p.symbol}:{now.date().isoformat()}"
        if first or not self.j.get(said):
            self.j.put(said, True)
            self._say(f"Sell of {p.symbol} ({p.strategy}) held: {why}. It goes out by itself at a morning check "
                      "once that is resolved.", force=True)

    def _managed(self) -> set:
        """Strategies whose sells the engine may still send (live: true, possibly exit-only)."""
        return {s.name for s in self.strategies + self.exit_only}

    def _stopped_after(self, t: datetime) -> bool:
        """True if `trader.run cancel` was run after t (the owner stopped what was pending)."""
        c = self.j.get("cancel_all_at")
        return bool(c) and t.isoformat() < c

    def _send_deferred_exits(self, now: datetime, band: float) -> None:
        for p in [p for p in self.j.positions(product=CNC) if p.qty > 0]:
            key = f"deferred_exit:{p.strategy}:{p.symbol}"
            d = self.j.get(key)
            if not d:
                continue
            if (d.get("entry") != p.entry_tag or self.j.active_orders(p.strategy, p.symbol, "exit", CNC)
                    or p.strategy not in self._managed()
                    or self._stopped_after(datetime.fromisoformat(d.get("since") or now.isoformat()))):
                self.j.put(key, None)           # a different position, a sell already working, switched off, cancelled
                continue
            hold = self._ca_hold(p, now, ref=d.get("ref"))
            if hold:
                said = f"deferred_said:{p.strategy}:{p.symbol}:{now.date().isoformat()}"
                if not self.j.get(said):
                    self.j.put(said, True)
                    self._say(f"Sell of {p.symbol} ({p.strategy}) still held: {hold}.", force=True)
                continue
            ltp = self.market.ltp([p.symbol], now).get(p.symbol)
            if ltp is None:
                continue
            self.j.put(key, None)
            sig = Signal(p.strategy, p.symbol, SELL, "exit", 1.0, ltp, now, CNC, reason="held sell, now sent")
            self.submit(sig, abs(p.qty), ltp * (1 - band), now, n=3, reprice_of=p.entry_tag)

    # ---------- swing ----------
    def _park_cfg(self, strat) -> Optional[tuple]:
        """(symbol, minimum idle rupees) if the strategy parks idle cash in a liquid ETF (`park:` in its settings;
        research/FINDINGS.md Addendum 10), else None."""
        pc = (self.cfg["strategies"].get(strat.name) or {}).get("park")
        if not pc:
            return None
        if isinstance(pc, str):
            pc = {"symbol": pc}
        sym = str(pc.get("symbol", "")).upper()
        if not sym:
            return None
        if self.broker.live and not self._sid(sym):
            if self._park_warned != sym:
                self._park_warned = sym
                self._say(f"{strat.name}: cash parking is set to {sym}, which INDstocks doesn't list; idle cash "
                          "stays idle.", force=True)
            return None
        return sym, float(pc.get("min", 25_000))

    def swing_strategies(self):
        return [s for s in self.strategies if s.engine == "swing"]

    def size_from_account(self) -> Optional[str]:
        """Live, `capital.live_from_account` (default on): the live swing strategies use the money actually in
        the account - free cash (less what an after-market limit and charges may block) plus what they already
        hold - up to their planned capital. Returns a one-line note when that is less than the plan."""
        self.risk.account_equity = self.risk.live_plan = None
        self.sizing_failed = False
        capcfg = self.cfg["capital"]
        if not (self.broker.live and capcfg.get("live_from_account", True) and self.swing_strategies()):
            return None
        free = self.broker.available_funds(CNC)
        if free is None:
            self.sizing_failed = True                    # can't size from the account: no new buys this run
            return None
        band = float(self.cfg["swing"].get("amo_band_pct", 2.0)) / 100
        usable = max(0.0, free - float(capcfg.get("reserve", 0) or 0)) / (1 + band + 0.01)
        names = {s.name for s in self.swing_strategies()}
        held = sum(abs(p.qty) * self.risk.marks.get(p.symbol, p.avg_price)
                   for p in self.j.positions(product=CNC) if p.strategy in names)
        pending = sum((o.req.qty - o.filled_qty) * o.req.limit_price
                      for o in self.j.active_orders(kind="entry", product=CNC)
                      if o.req.strategy in names and o.status in (NEW, OPEN, PARTIAL))
        self.risk.account_equity = usable + held + pending
        self.risk.live_plan = sum(self.risk.planned_capital(s.name, "swing") for s in self.swing_strategies())
        if self.risk.account_scale() < 0.95 and not capcfg.get("all_cash"):   # all_cash: the account IS the plan
            return (f"Account: ₹{free:,.0f} free + ₹{held + pending:,.0f} invested; live strategies are sized at "
                    f"{self.risk.account_scale():.0%} of their ₹{self.risk.live_plan:,.0f} plan.")
        return None

    def swing_plan(self, now: datetime) -> List[Order]:
        strats = self.swing_strategies() + [s for s in self.exit_only if s.engine == "swing"]
        if not strats:
            return []
        self._funds_left = {}
        self.sync(now)
        positions = self.j.positions(product=CNC)
        held_syms = {p.symbol for p in positions}
        wanted = set(self.universe) | held_syms | {self.bench}
        for strat in strats:
            wanted |= set(strat.symbols(self.universe))
            park = self._park_cfg(strat)
            if park:
                wanted.add(park[0])
        frames = self.market.daily(sorted(wanted), now)
        bench_df = frames.get(self.bench)
        if bench_df is None or bench_df.empty:
            self._say(f"Swing plan skipped: no {self.bench} data", force=True)
            return []
        d = bench_df.index[-1].date()
        if d != now.date():
            log.info("No completed session today (%s); last session %s", now.date(), d)
            return []
        if self.corporate_actions(now, frames):          # a bonus/split changed a share count: re-read
            positions = self.j.positions(product=CNC)
        self.risk.marks = {k: float(v["close"].iloc[-1]) for k, v in frames.items() if len(v)}
        sizing = self.size_from_account()               # again, with today's prices for what is held
        band = float(self.cfg["swing"].get("amo_band_pct", 2.0)) / 100
        placed: List[Order] = []
        lines = []
        for strat in strats:
            if hasattr(strat, "targets"):                # target-weight strategies (strategies/allocation.py)
                got, said = self._allocate(strat, frames, d, now, band, positions)
                placed += got
                lines += said
                continue
            park = self._park_cfg(strat)
            park_pos = next((p for p in positions if park and p.strategy == strat.name and p.symbol == park[0]), None)
            mine = {p.symbol: p for p in positions if p.strategy == strat.name and p is not park_pos}
            mine_syms = set(strat.symbols(self.universe)) | set(mine)
            own = {k: v for k, v in frames.items() if k in mine_syms}
            strat.universe_syms = set(strat.symbols(self.universe))   # held stocks outside it are only sold
            exit_only = strat in self.exit_only
            wkey = f"{strat.name}:waiting_buys"
            plan = self.j.get(wkey) if park else None
            if plan and strat.due_period(d) == plan.get("period"):
                # the buys of a review that waited for the parked money: the same buys at today's prices, no new
                # ranking and no new sells (a re-run could chain one-day delays to the end of the catch-up window)
                sigs = [Signal(strat.name, b["symbol"], BUY, "entry", float(b["strength"]),
                               float(frames[b["symbol"]]["close"].iloc[-1]), now, CNC,
                               reason=f"{b['reason']} (waited for the money)")
                        for b in plan["buys"] if b["symbol"] not in mine and len(frames.get(b["symbol"], ()))]
                strat._pending_period = plan["period"]
            else:
                if plan:
                    self.j.put(wkey, None)
                sigs = strat.signals(own, bench_df, d, mine, now)
            exits = [s for s in sigs if s.kind == "exit" and s.symbol in mine]
            entries = sorted([s for s in sigs if s.kind == "entry" and s.symbol not in mine
                              and s.strength >= strat.min_strength], key=lambda s: -s.strength)
            leaving = {s.symbol for s in exits}
            broker_trouble = False
            if self.broker.live:
                self.risk.note_deployed(strat.name)      # base for the live drawdown limit (see Risk.drawdown_base)
            for s in exits:
                p = mine[s.symbol]
                hold = self._ca_hold(p, now, price_check=False)      # 16:10: today's candle is in the data
                if hold:
                    self._defer_exit(p, now, hold, ref=s.ref_price)
                    lines.append(f"SELL {s.symbol} held ({hold})")
                    continue
                o = self.submit(s, abs(p.qty), s.ref_price * (1 - band), now, amo=True, positions=positions)
                if o:
                    placed.append(o)
                    broker_trouble |= o.status in (REJECTED, UNKNOWN)
                    lines.append(f"SELL {abs(p.qty)} {s.symbol} ({s.reason})")
            held_value = self.risk.held_value([p for p in mine.values() if p.symbol not in leaving], strat.name)
            open_slots = strat.slots() - (len(mine) - len(leaving))
            kept = [p for p in positions if p.symbol not in leaving]
            if exit_only:
                entries = []
            parked_buys = park_pos is not None and bool(entries[:max(0, open_slots)]) and not self.sizing_failed
            if park_pos is not None and (parked_buys or exit_only):
                # buys to make while the cash is parked (or a demoted strategy winding down): the ETF goes with
                # tonight's sells; the buys wait for the money and the review stays open until they are in
                kept = [p for p in kept if p is not park_pos]
                if not self.j.active_orders(strat.name, park_pos.symbol, "exit", CNC):
                    o = self._unpark(park_pos, frames, now, band, positions)
                    if o:
                        placed.append(o)
                        lines.append(f"SELL {abs(park_pos.qty)} {park_pos.symbol} (parked cash"
                                     + (", for the buys)" if parked_buys else ")"))
                    if parked_buys and (o is None or o.status in (REJECTED, UNKNOWN)):
                        broker_trouble = True            # the review stays open: tried again the next evening
            selling = any(o.req.side == SELL and o.status in (NEW, OPEN, PARTIAL) for o in placed) or \
                any(o.status in (NEW, OPEN, PARTIAL) for o in self.j.active_orders(kind="exit", product=CNC))
            if self.sizing_failed and entries[:max(0, open_slots)]:
                self._say(f"{strat.name}: could not read the account's funds from INDstocks, so no new buys "
                          f"tonight ({', '.join(e.symbol for e in entries[:max(0, open_slots)])}); "
                          "tried again at the next evening runs.", force=True)
                broker_trouble = True
                entries = []
            waited = []
            for s in entries[:max(0, open_slots)]:
                qty = self.risk.size_swing(strat.name, s.ref_price, strat.slots(), held_value)
                if qty < 1:
                    full = self.risk.size_swing(strat.name, s.ref_price, strat.slots(), held_value, scaled=False)
                    if full >= 1 and self.risk.account_scale() < 1:   # the plan affords it; the account doesn't
                        why = f"available funds too low for one unit of {s.symbol} (~₹{s.ref_price:,.2f})"
                        self.j.record_signal(s, False, why)
                        self._say(f"Order NOT sent: BUY {s.symbol} ({strat.name}): {why}. Add funds; it is "
                                  "tried again at the next evening runs.", force=True)
                        broker_trouble = True
                    else:
                        self.j.record_signal(s, False, "size below 1 share")
                    continue
                # while the strategy's own cash is in the ETF a buy never shrinks to the free money: it waits
                o = self.submit(s, qty, s.ref_price * (1 + band), now, amo=True, positions=kept,
                                wait_for_sells=selling or parked_buys)
                if o is None and self.last_block_funds:
                    broker_trouble = True                # money not there (yet): try again the next evening
                    waited.append(s)
                if o:
                    placed.append(o)
                    broker_trouble |= o.status in (REJECTED, UNKNOWN)
                    held_value += o.req.qty * s.ref_price
                    lines.append(f"BUY {o.req.qty} {s.symbol} ~₹{s.ref_price:,.2f} ({s.reason})")
            if park:
                if waited and getattr(strat, "_pending_period", None):
                    self.j.put(wkey, {"period": strat._pending_period, "buys": [
                        {"symbol": w.symbol, "strength": w.strength, "reason": w.reason} for w in waited]})
                elif not broker_trouble:
                    self.j.put(wkey, None)
            if hasattr(strat, "commit"):
                # a broker rejection leaves the month open, so the next evening (first week) tries again
                strat.commit(ok=not broker_trouble)
        for strat in self.swing_strategies():
            o = self._park_idle(strat, frames, d, now, band)
            if o:
                placed.append(o)
                lines.append(f"BUY {o.req.qty} {o.req.symbol} ~₹{o.req.ref_price:,.2f} ({o.req.strategy}: idle cash "
                             "parked in a liquid ETF)")
        if lines:
            nxt = self.market.next_session_after(now)
            when = f"{nxt:%d %b}" if nxt else "next"
            self._say(f"Swing orders for the {when} open (limit ±{band * 100:.0f}% of today's close):\n"
                      + "\n".join(lines) + (f"\n{sizing}" if sizing else "")
                      + "\nTo stop them: run `python -m trader.run cancel` before 09:00.")
        return placed

    def _allocate(self, strat, frames, d: date, now: datetime, band: float, positions) -> Tuple[List[Order], List[str]]:
        """A target-weight strategy's evening. On its review (month-end, or a review left open), when the mix has
        drifted (strat.needs_rebalance): sell what is over target (part of a position) and buy what is under (topping
        up). Buys that need the money from tonight's sells wait for it: the review stays open and runs again the next
        evening, when the sales have settled. On other evenings, idle cash above the sweep threshold (new money) buys
        what is under target - buys only. A strategy winding down (demoted / not allowed live) sells everything."""
        placed: List[Order] = []
        lines: List[str] = []
        exit_only = strat in self.exit_only
        period = strat.due_period(d)
        mine = {p.symbol: p for p in positions if p.strategy == strat.name and p.product == CNC}
        if exit_only and not mine:
            return placed, lines
        if self.broker.live:
            self.risk.note_deployed(strat.name)
        marks = self.risk.marks
        vals = {s: abs(p.qty) * marks.get(s, p.avg_price) for s, p in mine.items()}
        total = self.risk.strategy_capital(strat.name, "swing")
        if total <= 0:
            return placed, lines
        target = {} if exit_only else strat.targets(frames, d)
        if not exit_only and not target:
            if period is not None:
                self._say(f"{strat.name}: no prices today for its ETFs; review tried again tomorrow", force=True)
            return placed, lines
        current = {s: v / total for s, v in vals.items()}
        if period is None and not exit_only:            # not a review evening: only new money is invested
            idle = total - sum(vals.values())
            sweep = max(float(strat.params.get("sweep_min", 10_000)), float(strat.params.get("sweep_pct", 0.02)) * total)
            if idle < sweep or self.j.active_orders(strat.name, product=CNC) or self.sizing_failed:
                return placed, lines
            self._buy_to_target(strat, frames, target, vals, total, current, f"invest ₹{idle:,.0f} new cash", now,
                                band, positions, False, placed, lines)
            return placed, lines
        why = "winding down" if exit_only else strat.needs_rebalance(current, target, d, str(period).endswith("-12"))
        strat._pending_period = period
        if why is None:
            strat.commit(ok=True)
            return placed, lines
        if self.j.active_orders(strat.name, product=CNC):
            strat.commit(ok=False)                      # earlier orders still working: try again tomorrow
            return placed, lines
        min_trade = float(strat.params.get("min_trade", 2000))
        trouble = selling = False
        for s, p in mine.items():                       # sells first: what is over target
            px = marks.get(s)
            if px is None or px <= 0:
                trouble = True
                continue
            excess = vals[s] - target.get(s, 0.0) * total
            if s in target and excess < min_trade:
                continue
            qty = abs(p.qty) if s not in target else min(abs(p.qty), int(excess // px))
            if qty < 1:
                continue
            hold = self._ca_hold(p, now, price_check=False)
            if hold:
                lines.append(f"SELL {s} held ({hold})")
                trouble = True
                continue
            sig = Signal(strat.name, s, SELL, "exit", 1.0, px, now, CNC,
                         reason=f"{current.get(s, 0):.0%} -> {target.get(s, 0):.0%}: {why}")
            o = self.submit(sig, qty, px * (1 - band), now, amo=True, positions=positions)
            if o is None or o.status in (REJECTED, UNKNOWN):
                trouble = True
            if o:
                placed.append(o)
                selling = selling or o.status in (NEW, OPEN, PARTIAL, FILLED)
                lines.append(f"SELL {qty} {s} ({sig.reason})")
        trouble |= self._buy_to_target(strat, frames, target, vals, total, current, why, now, band, positions,
                                       selling, placed, lines)
        strat.commit(ok=not trouble)
        return placed, lines

    def _buy_to_target(self, strat, frames, target, vals, total, current, why, now, band, positions, selling,
                       placed, lines) -> bool:
        """Buy each ETF that is under its target weight up to it. True if a buy could not go out in full (waiting
        for money): the caller keeps the review open."""
        min_trade = float(strat.params.get("min_trade", 2000))
        trouble = False
        for s, w in sorted(target.items(), key=lambda kv: -kv[1]):
            px = float(frames[s]["close"].iloc[-1])
            short = w * total - vals.get(s, 0.0)
            if short < min_trade:
                continue
            qty = int(short / (px * (1 + band) * 1.003))
            if qty < 1:
                continue
            sig = Signal(strat.name, s, BUY, "entry", 1.0, px, now, CNC,
                         reason=f"{current.get(s, 0):.0%} -> {w:.0%}: {why}")
            o = self.submit(sig, qty, px * (1 + band), now, amo=True, positions=positions, wait_for_sells=selling)
            if o is None or o.status in (REJECTED, UNKNOWN) or o.req.qty < qty:
                trouble = True                          # waits for the sale money / funds: again tomorrow
            if o:
                placed.append(o)
                lines.append(f"BUY {o.req.qty} {s} ~₹{px:,.2f} ({sig.reason})")
        return trouble

    def _unpark(self, park_pos: Position, frames, now: datetime, band: float, positions) -> Optional[Order]:
        pdf = frames.get(park_pos.symbol)
        px = float(pdf["close"].iloc[-1]) if pdf is not None and len(pdf) else park_pos.avg_price
        sig = Signal(park_pos.strategy, park_pos.symbol, SELL, "exit", 1.0, px, now, CNC, reason="parked cash sold")
        return self.submit(sig, abs(park_pos.qty), px * (1 - band), now, amo=True, positions=positions)

    def _park_idle(self, strat, frames, d: date, now: datetime, band: float) -> Optional[Order]:
        """Evening, after the reviews: a strategy with `park:` puts idle cash of at least `min` rupees into the
        liquid ETF - only when its review is not waiting for money and none is due in the next 2 sessions (the
        ETF would be sold again before its units reach the account), it has no order working, the owner has not
        cancelled the bot's orders since its last one, and it is not already parked (no top-ups)."""
        park = self._park_cfg(strat)
        if not park:
            return None
        sym, min_value = park
        df = frames.get(sym)
        if df is None or df.empty or df.index[-1].date() != d:
            log.info("[%s] %s: no price for %s today; cash not parked", self.mode, strat.name, sym)
            return None
        if strat.due_period(d) is not None:
            return None                                 # its review is open (buys waiting for money)
        t = now
        for _ in range(2):
            nxt = self.market.next_session_after(t)
            if nxt is None:
                break
            if strat.due_period(nxt) is not None:
                return None                             # a review is coming: keep the cash
            t = datetime.combine(nxt, time(16, 0))
        if self.j.active_orders(strat.name, product=CNC):
            return None                                 # orders working: park once they have settled
        stop = self.j.get("cancel_all_at")
        if stop:
            last = max((o.req.created for o in self.j.load_orders() if o.req.strategy == strat.name), default=None)
            if last is None or datetime.fromisoformat(stop) > last:
                return None                             # `trader.run cancel` since the last order: wait for a review
        own = self.j.positions(product=CNC, strategy=strat.name)
        if any(p.symbol == sym for p in own):
            return None
        idle = self.risk.strategy_capital(strat.name, "swing") - self.risk.held_value(own, strat.name)
        if idle < min_value:
            return None
        px = float(df["close"].iloc[-1])
        qty = int(idle * 0.99 / (px * (1 + band) * 1.003))
        if qty < 1:
            return None
        sig = Signal(strat.name, sym, BUY, "entry", 1.0, px, now, CNC, reason=f"park ₹{idle:,.0f} idle cash")
        return self.submit(sig, qty, px * (1 + band), now, amo=True)

    def swing_check(self, now: datetime) -> None:
        if not self.swing_strategies() and not self.j.positions(product=CNC) and \
                not self.j.active_orders(product=CNC):
            return
        self._funds_left = {}
        self.sync(now)
        self.size_from_account()                        # after sync: sold positions are gone, their cash is in
        chase = float(self.cfg["swing"].get("max_chase_pct", 3.0)) / 100
        band = float(self.cfg["limits"]["price_band_pct"]) / 100
        stale = [o for o in self.j.load_orders(statuses=[OPEN, PARTIAL])
                 if o.req.product == CNC and o.req.created.date() < now.date()]
        for o in stale:
            self.broker.cancel(o, now)                  # yesterday's AMO not filled at the open, or an expired DAY order
            self.j.save_order(o)
            if o.status in (OPEN, PARTIAL) and (o.message or "").startswith("cancel failed"):
                key = f"cancel_failed:{o.req.tag}:{now.date().isoformat()}"
                if self.broker.live and not self.j.get(key):
                    self.j.put(key, True)
                    self._say(f"Could not cancel yesterday's {o.req.side} of {o.req.symbol} ({o.message[:120]}). Check it "
                              f"in the INDstocks app; while it shows as open here the bot sends no new "
                              f"{'sell' if o.req.side == SELL else 'buy'} for {o.req.symbol}. If the app shows it as "
                              "cancelled or expired, run after 15:35: .venv/bin/python -m trader.run cancel --forget",
                              force=True)
        if stale:
            self.settle(now)
        self.corporate_actions(now)                     # after the cancels: a bonus/split may change what is sold
        for o0 in stale:
            o = self.j.order(o0.req.tag)
            r = o.req
            if o.status == CANCEL_SENT:
                self._say(f"Cancel of {r.symbol} not confirmed yet; will look again at the next run.", force=True)
                continue
            left = r.qty - o.filled_qty
            if o.status == FILLED or left < 1:
                continue
            if r.kind == "exit":                        # what the position holds now (a bonus may have added shares)
                pos = self._find_position(r.strategy, r.symbol, r.product)
                if pos is None:
                    continue
                left = abs(pos.qty)
                hold = self._ca_hold(pos, now, ref=r.ref_price)
                if hold:
                    self._defer_exit(pos, now, hold, ref=r.ref_price)
                    continue
            if r.kind == "entry" and not r.amo:
                continue                                # entries get one re-price only
            ltp = self.market.ltp([r.symbol], now).get(r.symbol)
            if ltp is None:
                if self.broker.live:
                    self._say(f"No live price for {r.symbol}: its unfilled {r.side} was not re-priced today.", force=True)
                continue
            if r.kind == "entry":
                drift = ltp / r.ref_price - 1
                if (drift > chase) if r.side == BUY else (drift < -chase):
                    self._say(f"Skipped {r.symbol}: trading {drift:+.1%} from the planned price, beyond the "
                              f"{chase * 100:.0f}% chase limit")
                    continue
            sig = Signal(r.strategy, r.symbol, r.side, r.kind, 0.0, r.ref_price, now, CNC, reason="re-priced")
            limit = ltp * (1 + band) if r.side == BUY else ltp * (1 - band)
            self.submit(sig, left, limit, now, n=1, reprice_of=r.tag)
        self._retry_rejected_exits(now, band)
        self._send_deferred_exits(now, band)
        self.sync(now)

    def _retry_rejected_exits(self, now: datetime, band: float) -> None:
        """Live: a delivery sell that was rejected, or that ended unfilled without us cancelling it (expired at
        the close, cancelled by the exchange), is sent again once a day while the position is still held and
        nothing else is selling it. After 3 rejections in a week it stops and asks for a sale by hand. A sell
        rejected for being outside the day's price band (e.g. a stock stuck at its lower limit) is re-sent AT
        the last price, which queues it at the limit instead of being rejected again."""
        if not self.broker.live:
            return
        done = {(x.req.strategy, x.req.symbol) for x in self.j.load_orders(day=now.date()) if x.req.kind == "exit"}
        recent = now - timedelta(days=7)

        managed = self._managed()

        def failed(x) -> bool:
            r = x.req
            flag = self.j.get(f"ext_cancel:{r.tag}") if x.status == CANCELLED else None
            since = max(r.created, datetime.fromisoformat(flag)) if flag else r.created
            return (r.product == CNC and r.kind == "exit" and recent <= r.created and r.created.date() < now.date()
                    and r.strategy in managed and not self._stopped_after(since)
                    and (x.status == REJECTED or bool(flag)))

        cands = sorted([o for o in self.j.load_orders(statuses=[REJECTED, CANCELLED]) if failed(o)],
                       key=lambda o: o.req.created, reverse=True)
        for o in cands:
            r = o.req
            key = (r.strategy, r.symbol)
            if key in done:
                continue
            done.add(key)                               # the latest failed order per stock decides
            pos = self._find_position(r.strategy, r.symbol, CNC)
            if pos is None or self.j.active_orders(r.strategy, r.symbol, "exit", CNC):
                continue
            if r.created < pos.entry_time:
                continue                                # that sell was for an earlier position, since closed
            n_rejected = sum(1 for x in self.j.load_orders(statuses=[REJECTED])
                             if x.req.strategy == r.strategy and x.req.symbol == r.symbol and x.req.kind == "exit"
                             and x.req.product == CNC and x.req.created >= max(recent, pos.entry_time))
            if n_rejected >= 3:
                flag = f"{r.strategy}:{r.symbol}:gave_up_selling"
                if not self.j.get(flag):
                    self.j.put(flag, now.isoformat())
                    self._say(f"{r.symbol}: the sell was rejected {n_rejected} times; not retrying. Sell it by hand in "
                              f"the INDstocks app, then run `trader.run reconcile --fix`.", force=True)
                continue
            hold = self._ca_hold(pos, now, ref=r.ref_price)
            if hold:
                self._defer_exit(pos, now, hold, ref=r.ref_price)
                continue
            ltp = self.market.ltp([r.symbol], now).get(r.symbol)
            if ltp is None:
                self._say(f"No live price for {r.symbol}: its unfinished sell was not retried today.", force=True)
                continue
            # rejected this week for being outside the day's price band: sell AT the last price (queues at the limit)
            at_band = any(x.status == REJECTED and BAND_REJECT.search(x.message or "") for x in cands
                          if (x.req.strategy, x.req.symbol) == key and x.req.created >= pos.entry_time)
            why = "retry after rejection" if o.status == REJECTED else "retry: last sell ended unfilled"
            sig = Signal(r.strategy, r.symbol, SELL, "exit", 1.0, ltp, now, CNC, reason=why)
            self.submit(sig, abs(pos.qty), ltp if at_band else ltp * (1 - band), now, n=2, reprice_of=r.tag)

    # ---------- intraday ----------
    def intraday_strategies(self):
        return [s for s in self.strategies if s.engine == "intraday"]

    def _watchlist(self):
        strats = self.intraday_strategies()
        return sorted(set().union(*[set(s.symbols(self.universe)) for s in strats])) if strats else []

    def intraday_start(self, day: date, now: datetime) -> None:
        self._squared = False
        held = self.j.positions(product=INTRADAY)
        need = sorted(set(self._watchlist()) | {p.symbol for p in held})
        daily = self.market.daily(need, now) if need else {}
        # an intraday position from an earlier day was closed by the broker's auto square-off (MIS cannot be
        # carried): book it at that day's close and tell the user; never send an order for it
        for p in held:
            if p.entry_time.date() < day:
                d = daily.get(p.symbol)
                prior = d.loc[:str(p.entry_time.date())] if d is not None else None
                px = float(prior["close"].iloc[-1]) if prior is not None and len(prior) else p.avg_price
                self.j.record_trade(p, abs(p.qty), px, datetime.combine(p.entry_time.date(), time(15, 30)), 0.0,
                                    "carried-over", "broker square-off (assumed)")
                self.j.delete_position(p)
                self._say(f"{p.symbol}: intraday position from {p.entry_time:%d %b} was still open in the journal. "
                          f"Booked at that day's close ₹{px:,.2f}; check the actual square-off price at INDstocks.",
                          force=True)
        for s in self.intraday_strategies():
            s.prepare(day, daily, s.symbols(self.universe))
        if hasattr(self.broker, "restore_child"):
            for p in self.j.positions(product=INTRADAY):
                if p.stop is not None or p.target is not None:
                    self.broker.restore_child(p.entry_tag, p.symbol, p.product, abs(p.qty),
                                              SELL if p.qty > 0 else BUY, p.stop, p.target, now)

    def intraday_step(self, now: datetime) -> None:
        self._funds_left = {}
        if not self.intraday_strategies() and not self.j.positions(product=INTRADAY):
            return
        self.sync(now)
        need = sorted(set(self._watchlist()) | {p.symbol for p in self.j.positions(product=INTRADAY)})
        bars = self.market.today_bars(need, now)
        self.risk.marks.update({s: float(b["close"].iloc[-1]) for s, b in bars.items() if b is not None and len(b)})
        if now.time() >= _t(self.cfg["intraday"]["square_off"]):
            self.square_off(now)
            return
        positions = self.j.positions()
        band = float(self.cfg["limits"]["price_band_pct"]) / 100
        for strat in self.intraday_strategies():
            mine = {p.symbol: p for p in positions if p.strategy == strat.name and p.product == INTRADAY}
            for sig in strat.on_bar(now, bars, mine):
                if self.j.signal_seen(sig.id):
                    continue
                if sig.kind == "exit":
                    p = mine.get(sig.symbol)
                    if p:
                        self._exit_intraday(p, now, sig.reason or "strategy exit")
                    continue
                if sig.strength < strat.min_strength:
                    self.j.record_signal(sig, False, f"strength {sig.strength:.3g} below {strat.min_strength:.3g}")
                    continue
                ltp = self.market.ltp([sig.symbol], now).get(sig.symbol, sig.ref_price)
                qty = self.risk.size_intraday(strat.name, ltp, sig.stop)
                limit = ltp * (1 + band) if sig.side == BUY else ltp * (1 - band)
                if self.submit(sig, qty, limit, now, positions=positions):
                    positions = self.j.positions()
        self.sync(now)

    def _exit_intraday(self, p: Position, now: datetime, reason: str) -> None:
        self.sync(now)                                  # a stop may have filled meanwhile
        p = self._find_position(p.strategy, p.symbol, p.product)
        if p is None:
            return
        working = self.j.active_orders(p.strategy, p.symbol, "exit", INTRADAY)
        if working:
            # one exit at a time: re-price only after the old one is confirmed cancelled
            for o in working:
                if o.status in (OPEN, PARTIAL) and (now - (o.updated or o.req.created)).total_seconds() > 60:
                    self.broker.cancel(o, now)
                    self.j.save_order(o)
            return
        entry = self.j.order(p.entry_tag)
        has_leg = entry is not None and (entry.child_id or entry.req.tag in getattr(self.broker, "children", {}))
        if has_leg:
            if not self.broker.cancel_child(entry, now):
                self.j.event("warning", f"stop leg of {p.symbol} not cancelled; waiting for it before exiting")
                return                                  # the leg may be executing: look again next bar
            if entry.child_id:
                entry.child_id = ""
                self.j.save_order(entry)
            self.sync(now)
            p = self._find_position(p.strategy, p.symbol, p.product)
            if p is None:
                return
        side = SELL if p.qty > 0 else BUY
        ltp = self.market.ltp([p.symbol], now).get(p.symbol, p.avg_price)
        band = float(self.cfg["limits"]["price_band_pct"]) / 100
        sig = Signal(p.strategy, p.symbol, side, "exit", 0.0, ltp, now, INTRADAY, reason=reason)
        self.submit(sig, abs(p.qty), ltp * (1 - band) if side == SELL else ltp * (1 + band), now)
        self.sync(now)

    def square_off(self, now: datetime) -> None:
        for o in self.j.load_orders(statuses=[OPEN, PARTIAL]):
            if o.req.product == INTRADAY and o.req.kind == "entry":
                self.broker.cancel(o, now)
                self.j.save_order(o)
        for p in self.j.positions(product=INTRADAY):
            self._exit_intraday(p, now, "square-off")
        self._squared = not self.j.positions(product=INTRADAY)

    def intraday_end(self, now: datetime) -> None:
        if not self.intraday_strategies() and not self.j.positions(product=INTRADAY):
            return
        self.settle(now)
        left = self.j.positions(product=INTRADAY)
        if left and not self.broker.live:
            for p in left:                              # paper: close at the last price seen
                px = self.risk.marks.get(p.symbol, p.avg_price)
                self.j.record_trade(p, abs(p.qty), px, now, 0.0, "eod", "end of day")
                self.j.delete_position(p)
            left = []
        pnl = self.j.realized_today(now.date(), INTRADAY)
        t = self.j.trades()
        n = int((t["exit_time"].str[:10] == now.date().isoformat()).sum()) if len(t) else 0
        if n or left:
            self._say(f"Intraday {now:%d %b}: {n} trades, net ₹{pnl:,.0f}"
                      + (f". STILL OPEN: {', '.join(p.symbol for p in left)} - check the broker!" if left else ""),
                      force=bool(left))
        if self.broker.live and hasattr(self.broker, "net_positions"):
            try:
                at_broker = self.broker.net_positions(INTRADAY)
                if at_broker:
                    self._say(f"Broker still shows intraday positions: {at_broker}. Check INDstocks.", force=True)
            except Exception as e:                       # noqa: BLE001
                log.error("Position reconciliation failed: %s", e)
