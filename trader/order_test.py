"""Live order-path check, run from the whitelisted server:

  python -m trader.run test-order [--symbol NIFTYBEES] [--pct 3]

Places ONE delivery LIMIT BUY of 1 unit priced `pct`% below the last price (so it should not fill), confirms
INDstocks accepted it and shows it in the order book, cancels it, and confirms the cancel. This proves the
parts paper trading cannot: static-IP whitelist, token, order format, algo id, order-book matching and cancel.
Nothing executes, so no brokerage or tax is charged; ~1 unit's value is blocked for a few seconds.
Outside market hours the same check runs as an after-market order (AMO), the kind the swing strategies use:
it cannot execute before the next open and is cancelled within seconds.

Once the order may exist at INDstocks, every way out of this function either confirms the cancel or tells the
user exactly which order to cancel by hand.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .instruments import round_tick
from .models import BUY, CANCEL_SENT, CANCELLED, CNC, FILLED, REJECTED, UNKNOWN, Order, OrderRequest

MIN_PCT, MAX_PCT = 1.0, 10.0


def _row(book: List[dict], o: Order) -> Optional[dict]:
    for r in book:
        if (o.broker_id and str(r.get("id")) == o.broker_id) or r.get("remarks") == o.req.tag[:100]:
            return r
    return None


def order_path_test(broker, symbol: str, security_id: str, ltp: float, tick: float, now: datetime,
                    pct: float = 3.0, wait: Callable[[float], None] = time.sleep, polls: int = 10,
                    amo: bool = False) -> Tuple[bool, List[str]]:
    out: List[str] = []
    if not (MIN_PCT <= pct <= MAX_PCT):
        return False, [f"✗ --pct must be between {MIN_PCT:g} and {MAX_PCT:g} (got {pct:g}); nothing sent."]
    limit = round_tick(ltp * (1 - pct / 100), tick, "down")
    if not (0 < limit < ltp):
        return False, [f"✗ Computed limit ₹{limit} is not below the last price ₹{ltp}; nothing sent."]
    req = OrderRequest(tag=f"pathtest-{now:%Y%m%d-%H%M%S}", strategy="order_test", symbol=symbol,
                       security_id=str(security_id), side=BUY, qty=1, product=CNC, limit_price=limit,
                       signal_id="order_test", kind="entry", created=now, ref_price=ltp, amo=amo)
    o = Order(req)
    what = f"BUY 1 {symbol} @ ₹{limit}"

    def by_hand(reason: str) -> Tuple[bool, List[str]]:
        ident = f"order id {o.broker_id}" if o.broker_id else f"remarks '{req.tag}'"
        out.append(f"✗ {reason}")
        out.append(f"  → Open the INDstocks app → Orders. If an open {what} ({ident}) is there, CANCEL IT BY HAND. "
                   f"If it shows as executed, you hold 1 {symbol}: keep it or sell it.")
        return False, out

    try:
        broker.place(o, now, tick)                         # never retried (see IndStocksBroker.place)
        if o.status == REJECTED:
            out.append(f"✗ INDstocks REJECTED the order: {o.message}")
            low = o.message.lower()
            if re.search(r"\bip\b|whitelist|static", low):
                out.append("  → The static IP is not accepted: check the Primary IP on the Access Tokens page "
                           "matches this server's public IP (curl -s https://api.ipify.org).")
            elif re.search(r"\b(funds?|margin|rms)\b", low):
                out.append("  → Not enough funds for 1 unit: add funds and run again.")
            elif re.search(r"range|band|circuit", low):
                out.append("  → The price is outside the allowed band: run again with a smaller --pct.")
            elif amo:
                out.append("  → INDstocks may not take after-market orders at this hour: try again after 16:00 "
                           "or during market hours.")
            return False, out

        mark = "?" if o.status == UNKNOWN else "✓"
        out.append(f"{mark} Sent{' as an after-market order' if amo else ''}: {what} "
                   f"(last ₹{ltp}, {pct:g}% below) → {o.status}" + (f", order id {o.broker_id}" if o.broker_id else ""))

        row, err = None, None                          # 1. it must really be in the order book
        for _ in range(polls):
            try:
                row = _row(broker._order_book(), o)
            except Exception as e:                     # noqa: BLE001 - one failed poll is not fatal
                row, err = None, e
            if row:
                break
            wait(1)
        if not row:
            return by_hand("The order does not show up in the order book"
                           + (f" (the order book could not be read: {str(err)[:120]})." if err else "."))
        o.broker_id = o.broker_id or str(row.get("id") or "")
        broker.update([o], now)
        out.append(f"✓ In the order book: {o.broker_id}, INDstocks status {row.get('status')}")
        if o.status == REJECTED:
            out.append(f"✗ Accepted, then rejected by the exchange/RMS: {o.message}")
            return False, out
        if o.filled_qty or o.status == FILLED:
            out.append(f"! It EXECUTED (1 {symbol} @ ₹{o.avg_price or limit}). You now hold 1 unit: keep it or sell it. "
                       "Placement works; the cancel was not tested - run the test again.")
            return False, out
        if o.status == CANCELLED:
            out.append(f"? INDstocks cancelled/expired it by itself ({o.message or 'no reason given'}). Placement works; "
                       "the cancel was not tested - run the test again. Nothing is left open.")
            return False, out

        broker.cancel(o, now)                          # 2. cancel and confirm
        if o.status != CANCEL_SENT:
            return by_hand(f"The cancel request failed: {o.message or 'no reason given'}.")
        for _ in range(polls):
            wait(1)
            try:
                broker.update([o], now)
            except Exception:                          # noqa: BLE001
                continue
            if o.status in (CANCELLED, FILLED) or o.filled_qty:
                break
        if o.filled_qty or o.status == FILLED:
            out.append(f"! It EXECUTED before the cancel landed (1 {symbol} @ ₹{o.avg_price or limit}). "
                       "You now hold 1 unit: keep it or sell it. Run the test again to check the cancel.")
            return False, out
        if o.status == CANCELLED:
            out.append("✓ Cancelled and confirmed. The live order path works end to end.")
            return True, out
        return by_hand("The cancel was sent but the order book has not confirmed it yet.")
    except KeyboardInterrupt:
        by_hand("Interrupted after the order was sent.")
        print("\n".join(out))
        raise
    except Exception as e:                             # noqa: BLE001 - never leave without instructions
        return by_hand(f"Lost contact with INDstocks after the order was sent ({str(e)[:150]}).")
