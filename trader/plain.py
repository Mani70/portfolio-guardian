"""Plain-language names for Telegram messages: anyone should understand them without an investment background."""
from __future__ import annotations

import re

FUNDS = {
    "NIFTYBEES": "Nifty 50 fund: India's 50 biggest companies",
    "JUNIORBEES": "Nifty Next 50 fund: the next 50 biggest companies",
    "MID150BEES": "Midcap 150 fund: 150 medium-sized companies",
    "MON100": "Nasdaq 100 fund: 100 big US companies, mostly technology",
    "GOLDBEES": "Gold fund: follows the price of gold",
    "LIQUIDCASE": "Liquid fund: a safe parking place for cash, earns about the bank overnight rate",
}
STRATEGIES = {
    "core": "your long-term portfolio",
    "gap_reversal": "practice strategy 'gap reversal' (buys stocks that open sharply lower)",
}


def fund(symbol: str) -> str:
    """'NIFTYBEES (Nifty 50 fund: India's 50 biggest companies)' - or the symbol itself if it is not a fund."""
    s = str(symbol).upper()
    return f"{s} ({FUNDS[s]})" if s in FUNDS else s


def strategy(name: str) -> str:
    return STRATEGIES.get(name, f"strategy '{name}'")


def rupees(x: float) -> str:
    """Indian grouping: 384005 -> ₹3,84,005."""
    neg, x = x < 0, abs(round(x))
    s = str(int(x))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-₹" if neg else "₹") + s


def rebalance_reason(why: str) -> str:
    """The long-term portfolio's reasons (strategies.allocation.needs_rebalance / Engine._allocate) in plain words."""
    if why == "first purchase":
        return "first purchase: building your long-term portfolio"
    if why.startswith("invest ₹"):
        return f"investing new cash that arrived in your account ({why[len('invest '):].split(' ')[0]})"
    if why == "winding down":
        return "this strategy has been retired, so its holdings are sold"
    note = ""
    m = re.search(r" \((Nifty 50 dividend yield.*|shares are .*)\)$", why)
    if m:
        note, why = f" {m.group(1)}.", why[:m.start()]
    if why.startswith("year-end rebalance"):
        return "yearly reset: at every year-end each fund is brought back to its target share" + note
    m = re.match(r"(\S+) (\d+)% vs target (\d+)%", why)
    if m:
        return (f"{m.group(1)} has drifted to {m.group(2)}% of the portfolio against its target of {m.group(3)}% "
                f"(more than 5 points away), so every fund is brought back to its target share" + note)
    return why + note
