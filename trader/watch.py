"""Market-hours watch over every stock in the account (yours and the bot's): Telegram within ~5 minutes of
a sharp company-specific fall or of a price trading through one of your exit levels. Alerts only.

Why alerts and not automatic selling: on 12 years of Nifty 50 data (research/FINDINGS.md), a stock-specific
fall of 5-8% was followed on average by a small RECOVERY against the Nifty (+1.4% over 20 days); falls of 8%+
fell a further 1-3% the next day on average, then showed no reliable direction. Selling automatically on the
shock would mostly lock in the loss. The cases where selling matters (fraud, regulator action, a broken
business) can't be told apart by price alone - that needs a person to read the news.

A fall that matches a bonus/split ratio (half, a third, ...) is reported as a likely ex-date, not a crash.

  python -m trader.run watch        # 09:16-15:30, every 5 minutes (scheduled by cron)
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .corporate import describe, standard

log = logging.getLogger("trader.watch")

LEVELS = (0.05, 0.08, 0.12)          # company-specific fall vs the Nifty that triggers an alert
EX_DIVIDEND = (" (On a stock's ex-dividend date the price also drops by the dividend paid out; that part is not "
               "a loss.)")
HISTORY = {
    0.05: "History (Nifty 50, 2015-26): after company-specific falls of 5-8%, stocks on average RECOVERED "
          "slightly vs the Nifty over the next 20 days (+1.4%). No automatic sell.",
    0.08: "History: after falls of 8-12%, the next day averaged a further -1.2%, then no reliable direction. "
          "Check the news before acting.",
    0.12: "History: after falls of 12%+, the next day averaged a further -2.7%, then no reliable direction. "
          "Check the news now: results, regulator, fraud or a broken business are when selling matters.",
}


def evaluate(prices: Dict[str, float], prev: Dict[str, float], nifty_change: float,
             levels: Iterable[Tuple[str, float, str]], sent: Set[Tuple[str, str]]) -> List[str]:
    """Pure logic: alerts for this check. `sent` holds (symbol, kind) already alerted today; updated in place."""
    out = []
    for sym, px in sorted(prices.items()):
        pc = prev.get(sym)
        if not pc or not px:
            continue
        if sym.upper().startswith("LIQUID"):
            continue                                   # parked cash (liquid ETF): flat by design, not a fall
        chg = px / pc - 1
        rel = chg - nifty_change
        hit = [lv for lv in LEVELS if rel <= -lv + 1e-9]
        if hit:
            lv = max(hit)
            key = (sym, f"fall{lv}")
            if key not in sent:
                sent.update((sym, f"fall{x}") for x in hit)          # don't repeat the smaller levels later
                ratio = split_ratio(px / pc)
                if ratio:
                    out.append(f"⚠️ {sym} is down {chg:+.1%} today (Nifty {nifty_change:+.1%}), ₹{px:,.2f}. That is "
                               f"close to the ratio of a {describe(ratio)}: ONLY if {sym} announced one with today "
                               "as ex-date is this not a loss (the extra shares arrive in a day or two; the bot holds "
                               f"its sells of {sym} until then). Otherwise this is a real crash:\n{HISTORY[lv]}")
                else:
                    out.append(f"⚠️ {sym} is down {chg:+.1%} today (Nifty {nifty_change:+.1%}), ₹{px:,.2f}.\n"
                               f"{HISTORY[lv]}" + (EX_DIVIDEND if lv == LEVELS[0] else ""))
    for sym, level, note in levels:
        px = prices.get(sym)
        if px and px < level and (sym, f"level{level}") not in sent:
            sent.add((sym, f"level{level}"))
            pc = prev.get(sym)
            hint = (f" Today's drop matches a {describe(split_ratio(px / pc))} - if that is what happened, your "
                    "level needs the same adjustment in config.yaml.") if pc and split_ratio(px / pc) else ""
            out.append(f"🔻 {sym} is trading at ₹{px:,.2f}, below your exit level ₹{level:,.2f}. {note}".strip() + hint)
    return out


def split_ratio(r: float) -> float | None:
    """A price ratio (today / yesterday) that matches a standard bonus/split ratio within 1.5%, else None."""
    f = standard(r, tol=0.015)
    return f if f is not None and f < 1 else None


def _ist_date(ts: int) -> date:
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=5, minutes=30)).date()


class Watch:
    def __init__(self, client, notifier, rules: list, root, now_fn: Callable[[], datetime],
                 sleep: Callable[[float], None] = _time.sleep):
        self.client, self.notifier, self.rules, self.root = client, notifier, rules, root
        self.now, self.sleep = now_fn, sleep
        self.sent: Set[Tuple[str, str]] = set()
        self.scrip: Dict[str, str] = {}
        self.prev: Dict[str, float] = {}
        self.nifty_code = ""

    def prepare(self) -> int:
        """Holdings -> quote codes and yesterday's closes. Returns the number of stocks watched."""
        from guardian.main import gather, instrument_index
        now = self.now()
        md = gather(self.client, self.rules, now)
        self.scrip = {h.symbol: md.scrip_of[h.symbol] for h in md.holdings if h.symbol in md.scrip_of}
        try:
            idx = instrument_index(self.client, self.root / "cache")
            self.nifty_code = f"NSE_{idx['NSE']['NIFTYBEES']}"
        except Exception as e:                                  # noqa: BLE001
            log.warning("NIFTYBEES code unavailable (%s): falls are measured without the market", e)
        codes = list(self.scrip.values()) + ([self.nifty_code] if self.nifty_code else [])
        end = int(now.timestamp() * 1000)
        start = int((now - timedelta(days=12)).timestamp() * 1000)
        candles = self.client.candles_many(codes, "1day", start, end)
        today = now.date()
        back = {c: s for s, c in self.scrip.items()}
        for code, rows in candles.items():
            done = [c for c in rows if _ist_date(c.ts) < today]
            if done:
                self.prev[back.get(code, "__NIFTY__")] = done[-1].c
        return len(self.scrip)

    def levels(self) -> List[Tuple[str, float, str]]:
        out = []
        for r in self.rules:
            if getattr(r, "type", "") not in ("close_below", "price_below") or not r.symbol or r.level is None:
                continue
            when = ("The rule checks the weekly close." if r.timeframe == "week" else
                    "The rule checks the daily close at 15:50.") if r.type == "close_below" else ""
            out.append((r.symbol.upper(), float(r.level), f"{when} {r.note or ''}".strip()))
        return out

    def check(self) -> List[str]:
        codes = list(self.scrip.values()) + ([self.nifty_code] if self.nifty_code else [])
        q = self.client.ltp(codes)
        prices = {s: q[c] for s, c in self.scrip.items() if c in q}
        npx, npc = q.get(self.nifty_code), self.prev.get("__NIFTY__")
        nifty = (npx / npc - 1) if npx and npc else 0.0
        alerts = evaluate(prices, self.prev, nifty, self.levels(), self.sent)
        for a in alerts:
            self.notifier.send(a)
        return alerts

    def run(self, until: time = time(15, 30)) -> None:
        n = self.prepare()
        log.info("Watching %d holdings", n)
        failures = 0
        while True:
            now = self.now()
            if now.time() >= until:
                break
            if now.time() >= time(9, 16):
                try:
                    self.check()
                    if failures >= 3:
                        self.notifier.send("✅ Price watch is working again.")
                    failures = 0
                except Exception as e:                          # noqa: BLE001 - one bad poll must not stop the day
                    failures += 1
                    log.warning("watch check failed: %s", e)
                    if failures == 3:                           # 15 minutes blind: say so once
                        self.notifier.send(f"⚠️ Price watch not working for 15 minutes ({str(e)[:160]}). "
                                           "Crash alerts are paused until it recovers.")
            nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5, seconds=30)
            self.sleep(max(1.0, (nxt - self.now()).total_seconds()))
