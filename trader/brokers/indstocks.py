"""Live orders through the INDstocks REST API (https://api-docs.indstocks.com/normal_orders/).

Safety rules built in:
- Order calls are NEVER retried. If a placement times out we look for it in the order book by its
  `remarks` tag (our order id) and adopt it if it exists; otherwise we mark it UNKNOWN and alert.
- LIMIT orders only (INDstocks converts MARKET to LIMIT anyway); prices are rounded to the tick.
- algo_id "99999" for NSE, as the docs require; a client-side rate limit keeps us far under 10 orders/s.
- Placing, modifying and cancelling orders needs a whitelisted static IP (Access Tokens page).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from guardian.broker import BASE_URL, BrokerError, _error_detail

from ..models import (BUY, CANCEL_SENT, CANCELLED, FILLED, INTRADAY, NEW, OPEN, PARTIAL, REJECTED, TERMINAL,
                      UNKNOWN, Order)
from .base import Broker, CostModel, Fill

log = logging.getLogger(__name__)

STATUS = {
    "QUEUED": OPEN, "O-PENDING": OPEN, "SL-PENDING": OPEN, "PROCESSING": OPEN, "INITIATED": OPEN,
    "PENDING": OPEN, "MODIFIED": OPEN, "CREATED": OPEN,
    "SUCCESS": FILLED, "PARTIALLY FILLED": PARTIAL,
    "CANCELLED": CANCELLED, "EXPIRED": CANCELLED, "PARTIALLY FILLED - CANCELLED": CANCELLED,
    "PARTIALLY FILLED - EXPIRED": CANCELLED,
    "ABORTED": REJECTED, "FAILED": REJECTED,
}


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class IndStocksBroker(Broker):
    name = "indstocks"
    live = True

    def __init__(self, client, costs: CostModel = None, rate_per_sec: float = 2.0, stop_band_pct: float = 2.0):
        self.client = client
        self.costs = costs or CostModel()
        self.min_gap = 1.0 / max(0.1, rate_per_sec)
        self.stop_band = stop_band_pct / 100
        self._last = 0.0
        self.leg_seen: Dict[str, int] = {}        # parent tag -> stop/target quantity already reported

    # ---------- http ----------
    def _send(self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        wait = self.min_gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in (0, 1):
            token = self.client._ensure_token()
            try:
                resp = self.client._session.request(method, f"{BASE_URL}{path}", json=body, params=params,
                                                    headers={"Authorization": token, "Content-Type": "application/json"},
                                                    timeout=self.client.timeout)
            finally:
                self._last = time.monotonic()
            # a rejected token is refused before anything is processed (no order is created), so one retry with
            # the token another job saved, or a fresh login, is safe
            if attempt == 0 and resp.status_code in (401, 403) and self.client._is_token_error(resp):
                try:
                    if self.client.recover_token(token):
                        continue
                except BrokerError:
                    pass
            break
        try:
            data = resp.json()
        except ValueError:
            raise BrokerError(f"{path}: HTTP {resp.status_code}, non-JSON response: {resp.text[:200]}", resp.status_code)
        if resp.status_code >= 400 or data.get("success") is False or data.get("status") == "error":
            raise BrokerError(f"{path}: HTTP {resp.status_code} {data.get('error_type', '')}: {_error_detail(data)}",
                              resp.status_code, data.get("error_type", ""))
        return data

    # ---------- placement ----------
    def _body(self, order: Order) -> dict:
        r = order.req
        body = {"txn_type": r.side, "exchange": "NSE", "segment": "EQUITY", "product": r.product,
                "order_type": "LIMIT", "validity": "DAY", "security_id": str(r.security_id), "qty": int(r.qty),
                "limit_price": r.limit_price, "algo_id": "99999", "remarks": r.tag[:100]}
        if r.amo:
            body["is_amo"] = True
        return body

    def _smart_body(self, order: Order, tick: float) -> dict:
        from ..instruments import round_tick
        r = order.req
        body = self._body(order)
        body.pop("is_amo", None)
        long = r.side == BUY
        if r.stop is not None:
            trig = round_tick(r.stop, tick, "down" if long else "up")
            lim = round_tick(trig * (1 - self.stop_band) if long else trig * (1 + self.stop_band), tick,
                             "down" if long else "up")
            body.update(sl_trigger_price=trig, sl_limit_price=lim)
        if r.target is not None:
            trig = round_tick(r.target, tick, "up" if long else "down")
            lim = round_tick(trig * (1 + 0.001) if long else trig * (1 - 0.001), tick, "up" if long else "down")
            body.update(tgt_trigger_price=trig, tgt_limit_price=lim)
        return body

    def place(self, order: Order, now: datetime, tick: float = 0.05) -> Order:
        r = order.req
        smart = r.product == INTRADAY and (r.stop is not None or r.target is not None) and not r.amo
        path = "/smart/order" if smart else "/order"
        body = self._smart_body(order, tick) if smart else self._body(order)
        order.updated = now
        try:
            data = self._send("POST", path, body).get("data") or {}
        except BrokerError as e:
            if e.status and 400 <= e.status < 500 and e.status not in (408, 499):   # timeouts are ambiguous
                order.status, order.message = REJECTED, str(e)[:300]
                return order
            return self._reconcile_after_error(order, str(e))
        except requests.RequestException as e:
            return self._reconcile_after_error(order, f"network error: {e}")
        if smart:
            row = (data.get("order_data") or [{}])[0]
            order.broker_id = str(row.get("order_id") or "")
            order.child_id = str((row.get("child_order_details") or {}).get("order_id") or "")
        else:
            order.broker_id = str(data.get("order_id") or "")
        order.status = OPEN if order.broker_id else UNKNOWN
        if not order.broker_id:
            order.message = f"no order_id in response: {str(data)[:200]}"
        return order

    def _match(self, order: Order, book: List[dict]) -> bool:
        """Find our order in the book by its `remarks` tag: the main order is the EQ- row on our side,
        the stop/target leg (smart orders) the GTT- row on the other side."""
        r = order.req
        rows = [g for g in book if g.get("remarks") == r.tag[:100]]
        main = [g for g in rows if str(g.get("txn_type", "")).upper() == r.side and str(g.get("id", "")).startswith("EQ-")] \
            or [g for g in rows if str(g.get("txn_type", "")).upper() == r.side]
        if not main:
            return False
        order.broker_id = str(main[0]["id"])
        order.status = STATUS.get(str(main[0].get("status", "")).upper(), OPEN)
        child = [g for g in rows if str(g.get("txn_type", "")).upper() != r.side and str(g.get("id", "")).startswith("GTT-")]
        if child and not order.child_id:
            order.child_id = str(child[0]["id"])
        return True

    def _reconcile_after_error(self, order: Order, why: str) -> Order:
        """The order may or may not have reached INDstocks. Look for our tag before doing anything else."""
        log.error("Order %s: %s; checking the order book before anything else", order.req.tag, why)
        try:
            if self._match(order, self._order_book()):
                order.message = f"adopted after error ({why[:120]})"
                return order
        except Exception as e:                       # noqa: BLE001 - any failure here means "unknown"
            why += f"; order book check failed: {e}"
        order.status, order.message = UNKNOWN, f"{why[:250]} - NOT resent; looked up again on every sync."
        return order

    # ---------- cancel ----------
    def cancel(self, order: Order, now: datetime) -> Order:
        """Request a cancel. The order becomes CANCEL_SENT; the next update() reads its final state
        (it may have filled in the meantime) from the order book."""
        if order.status in TERMINAL or not order.broker_id:
            return order
        path = "/smart/order/cancel" if order.child_id else "/order/cancel"
        try:
            self._send("POST", path, {"order_id": order.broker_id, "segment": "EQUITY"})
            order.status, order.updated = CANCEL_SENT, now
        except (BrokerError, requests.RequestException) as e:
            order.message = f"cancel failed: {e}"[:300]
        return order

    def cancel_child(self, order: Order, now: datetime) -> bool:
        """Cancel a stop/target leg. True only if INDstocks confirmed it; False means the leg may have
        triggered, so the caller must not send its own exit until the next sync."""
        if not order.child_id:
            return True
        try:
            self._send("POST", "/smart/order/cancel", {"order_id": order.child_id, "segment": "EQUITY"})
            return True
        except (BrokerError, requests.RequestException) as e:
            log.error("Could not cancel stop/target leg %s: %s", order.child_id, e)
            return False

    # ---------- status ----------
    def _order_book(self) -> List[dict]:
        return self._send("GET", "/order-book").get("data") or []

    def update(self, orders: List[Order], now: datetime) -> List[Fill]:
        watch = [o for o in orders if o.broker_id or o.status in (NEW, UNKNOWN)]
        watch = [o for o in watch if o.status not in TERMINAL or o.child_id]
        if not watch:
            return []
        book = self._order_book()
        by_id = {str(r.get("id")): r for r in book}
        fills: List[Fill] = []
        for o in watch:
            r = o.req
            if not o.broker_id:                       # reply was lost: keep looking for it by tag
                if not self._match(o, book):
                    continue
                o.message = "found in the order book after a lost reply"
            row = by_id.get(o.broker_id)
            if row is not None and o.status not in TERMINAL:
                traded = int(_num(row.get("traded_qty")))
                px = _num(row.get("traded_price"))
                if traded > o.filled_qty and px > 0:
                    delta = traded - o.filled_qty
                    delta_px = (traded * px - o.filled_qty * o.avg_price) / delta
                    ch = self.costs.charges(r.product, r.side, delta_px * delta, r.symbol)
                    fills.append(Fill(r.tag, delta, delta_px, now, ch, "main", r.side))
                    o.filled_qty, o.avg_price, o.charges = traded, px, o.charges + ch
                new_status = STATUS.get(str(row.get("status", "")).upper(), o.status)
                if o.status == CANCEL_SENT and new_status == OPEN:
                    new_status = CANCEL_SENT                    # still waiting for the cancel to land
                o.status = new_status
                if o.status == CANCELLED and o.filled_qty:
                    o.status = FILLED if o.filled_qty >= r.qty else PARTIAL
                    if o.filled_qty < r.qty:
                        o.message = f"cancelled after {o.filled_qty} of {r.qty} filled"
                        o.status = CANCELLED
                o.message = str(row.get("extra_info") or o.message)[:300]
                if "EXPIRED" in str(row.get("status", "")).upper():
                    o.message = f"expired: {row.get('extra_info') or 'not filled by the close'}"[:300]
                o.updated = now
            # executed stop/target legs come back as new orders carrying our tag in `remarks`;
            # if INDstocks only updates the GTT row itself, use that row. Count the leg quantity per
            # parent so the same execution is never reported twice.
            if o.child_id:
                legs = [g for g in book if g.get("remarks") == r.tag[:100]
                        and str(g.get("id")) != o.broker_id
                        and str(g.get("txn_type", "")).upper() != r.side]
                real = [g for g in legs if str(g.get("id")) != o.child_id]
                gtt = [g for g in legs if str(g.get("id")) == o.child_id]
                real_qty = sum(int(_num(g.get("traded_qty"))) for g in real)
                gtt_qty = sum(int(_num(g.get("traded_qty"))) for g in gtt)
                total = max(real_qty, gtt_qty)
                seen = self.leg_seen.get(r.tag, 0)
                if total > seen:
                    src = real if real_qty >= gtt_qty else gtt
                    q = sum(int(_num(g.get("traded_qty"))) for g in src)
                    px = sum(int(_num(g.get("traded_qty"))) * _num(g.get("traded_price")) for g in src) / q if q else 0
                    if px > 0:
                        side = "SELL" if r.side == BUY else BUY
                        which = "stop"
                        if r.stop is not None and r.target is not None:
                            which = "stop" if abs(px - r.stop) <= abs(px - r.target) else "target"
                        elif r.target is not None:
                            which = "target"
                        ch = self.costs.charges(r.product, side, px * (total - seen), r.symbol)
                        fills.append(Fill(r.tag, total - seen, px, now, ch, which, side))
                        self.leg_seen[r.tag] = total
        return fills

    def net_positions(self, product: str) -> Dict[str, int]:
        """security_id -> net quantity held at the broker (for reconciliation)."""
        rows = self._send("GET", "/portfolio/positions",
                          params={"segment": "equity", "product": product.lower()}).get("data") or []
        return {str(r.get("security_id")): int(_num(r.get("net_qty"))) for r in rows if _num(r.get("net_qty"))}

    def holdings(self):
        """The account's delivery holdings (guardian.broker.Holding rows)."""
        return self.client.holdings()

    def available_funds(self, product: str) -> Optional[float]:
        try:
            d = self._send("GET", "/funds").get("data") or {}
        except (BrokerError, requests.RequestException) as e:
            log.error("Funds check failed: %s", e)
            return None
        bal = d.get("detailed_avl_balance") or {}
        return _num(bal.get("eq_mis" if product == INTRADAY else "eq_cnc"))
