"""Rule definitions and evaluation.

Stock rules (per symbol):
  close_below / close_above   - last COMPLETED daily or weekly candle closes beyond a level
  price_below / price_above   - live price (LTP) beyond a level
  loss_from_cost              - live price is X% or more below your average buy price
  off_52w_high                - live price is X% or more below the 52-week high

Portfolio rules:
  max_position_weight         - any single stock above X% of the stock portfolio
  max_sector_weight           - any sector above X% (sectors come from config)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .broker import Candle, Holding

IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE = time(15, 30)

STOCK_RULES = {"close_below", "close_above", "price_below", "price_above", "loss_from_cost", "off_52w_high"}
PORTFOLIO_RULES = {"max_position_weight", "max_sector_weight"}


@dataclass
class Alert:
    rule_id: str
    title: str
    detail: str
    symbol: Optional[str] = None
    severity: str = "warning"   # info | warning | critical


@dataclass
class Rule:
    id: str
    type: str
    symbol: Optional[str] = None
    level: Optional[float] = None
    pct: Optional[float] = None
    timeframe: str = "day"            # day | week (close_* rules)
    note: str = ""
    severity: str = "warning"
    scrip: Optional[str] = None       # override, e.g. NSE_1660, for stocks you don't hold
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        known = {"id", "type", "symbol", "level", "pct", "timeframe", "note", "severity", "scrip"}
        r = cls(**{k: v for k, v in d.items() if k in known},
                extra={k: v for k, v in d.items() if k not in known})
        r.validate()
        return r

    def validate(self) -> None:
        if self.type not in STOCK_RULES | PORTFOLIO_RULES:
            raise ValueError(f"Rule {self.id}: unknown type '{self.type}'")
        if self.type in STOCK_RULES and not self.symbol:
            raise ValueError(f"Rule {self.id}: '{self.type}' needs a symbol")
        if self.type in {"close_below", "close_above", "price_below", "price_above"} and self.level is None:
            raise ValueError(f"Rule {self.id}: '{self.type}' needs a level")
        if self.type in {"loss_from_cost", "off_52w_high", "max_position_weight", "max_sector_weight"} and self.pct is None:
            raise ValueError(f"Rule {self.id}: '{self.type}' needs pct")
        if self.timeframe not in {"day", "week"}:
            raise ValueError(f"Rule {self.id}: timeframe must be 'day' or 'week'")
        if self.symbol:
            self.symbol = self.symbol.upper()


# ---------- completed-candle logic ----------
def _candle_date(c: Candle) -> date:
    return datetime.fromtimestamp(c.ts, IST).date()


def completed_daily(candles: Sequence[Candle], now: datetime) -> List[Candle]:
    """Drop today's candle if the session hasn't closed yet."""
    out = list(candles)
    if out and _candle_date(out[-1]) == now.date() and now.time() < MARKET_CLOSE:
        out.pop()
    return out


def completed_weekly(candles: Sequence[Candle], now: datetime) -> List[Candle]:
    """Drop the current week's candle until Friday's close.

    Note: if Friday is a market holiday the week is treated as complete from
    Saturday onwards, so a holiday-shortened week is evaluated one day late.
    """
    out = list(candles)
    if out:
        start = _candle_date(out[-1])
        friday = start + timedelta(days=(4 - start.weekday()) % 7)
        complete_at = datetime.combine(friday, MARKET_CLOSE, IST)
        if now < complete_at:
            out.pop()
    return out


# ---------- evaluation ----------
@dataclass
class MarketData:
    holdings: List[Holding]
    ltp: Dict[str, float]                       # scrip code -> live price
    daily: Dict[str, List[Candle]]              # scrip code -> ~1y of daily candles
    weekly: Dict[str, List[Candle]]             # scrip code -> ~1y of weekly candles
    scrip_of: Dict[str, str]                    # symbol -> scrip code


def _fmt(x: float) -> str:
    return f"₹{x:,.2f}"


def evaluate_stock_rule(rule: Rule, md: MarketData, now: datetime) -> Optional[Alert]:
    scrip = rule.scrip or md.scrip_of.get(rule.symbol)
    if not scrip:
        return Alert(rule.id, f"{rule.symbol}: no price data",
                     "Symbol not in holdings and no 'scrip' override set in config.",
                     rule.symbol, "info")
    price = md.ltp.get(scrip)
    holding = next((h for h in md.holdings if h.symbol == rule.symbol), None)
    t = rule.type

    if t in {"close_below", "close_above"}:
        series = md.daily.get(scrip, []) if rule.timeframe == "day" else md.weekly.get(scrip, [])
        series = completed_daily(series, now) if rule.timeframe == "day" else completed_weekly(series, now)
        if not series:
            return Alert(rule.id, f"{rule.symbol}: no candle data",
                         f"Couldn't check this rule: no completed {rule.timeframe}ly candles for {scrip}.",
                         rule.symbol, "info")
        last = series[-1]
        hit = last.c < rule.level if t == "close_below" else last.c > rule.level
        if hit:
            word = "below" if t == "close_below" else "above"
            span = "Daily" if rule.timeframe == "day" else "Weekly"
            return Alert(rule.id, f"{rule.symbol}: {span.lower()} close {word} {_fmt(rule.level)}",
                         f"{span} close {_fmt(last.c)} on {_candle_date(last):%d %b %Y} "
                         f"(level {_fmt(rule.level)}). {rule.note}".strip(), rule.symbol, rule.severity)
        return None

    if price is None:
        return Alert(rule.id, f"{rule.symbol}: no live price",
                     f"Couldn't check this rule: no quote for {scrip}.", rule.symbol, "info")

    if t in {"price_below", "price_above"}:
        hit = price < rule.level if t == "price_below" else price > rule.level
        if hit:
            word = "below" if t == "price_below" else "above"
            return Alert(rule.id, f"{rule.symbol}: price {word} {_fmt(rule.level)}",
                         f"Live price {_fmt(price)}. {rule.note}".strip(), rule.symbol, rule.severity)
        return None

    if t == "loss_from_cost":
        if not holding or holding.avg_price <= 0:
            return None
        chg = (price / holding.avg_price - 1) * 100
        if chg <= -abs(rule.pct):
            return Alert(rule.id, f"{rule.symbol}: down {abs(chg):.1f}% from your cost",
                         f"Avg cost {_fmt(holding.avg_price)}, live {_fmt(price)}, "
                         f"unrealised loss {_fmt((price - holding.avg_price) * holding.qty)}. {rule.note}".strip(),
                         rule.symbol, rule.severity)
        return None

    if t == "off_52w_high":
        series = md.daily.get(scrip, [])
        if not series:
            return None
        high = max(c.h for c in series)
        chg = (price / high - 1) * 100
        if chg <= -abs(rule.pct):
            return Alert(rule.id, f"{rule.symbol}: {abs(chg):.1f}% below 52-week high",
                         f"52w high {_fmt(high)}, live {_fmt(price)}. {rule.note}".strip(),
                         rule.symbol, rule.severity)
        return None
    return None


def portfolio_weights(md: MarketData) -> Dict[str, float]:
    values = {}
    for h in md.holdings:
        p = md.ltp.get(md.scrip_of.get(h.symbol, ""), h.avg_price)
        values[h.symbol] = h.qty * p
    total = sum(values.values()) or 1.0
    return {s: v / total * 100 for s, v in values.items()}


def evaluate_portfolio_rule(rule: Rule, md: MarketData, sectors: Dict[str, str]) -> List[Alert]:
    weights = portfolio_weights(md)
    alerts = []
    if rule.type == "max_position_weight":
        for sym, w in sorted(weights.items(), key=lambda kv: -kv[1]):
            if w > rule.pct:
                alerts.append(Alert(f"{rule.id}:{sym}", f"{sym} is {w:.1f}% of your stocks",
                                    f"Limit {rule.pct:.0f}%. {rule.note}".strip(), sym, rule.severity))
    elif rule.type == "max_sector_weight":
        by_sector: Dict[str, float] = {}
        for sym, w in weights.items():
            sec = sectors.get(sym, "Unclassified")
            by_sector[sec] = by_sector.get(sec, 0) + w
        for sec, w in sorted(by_sector.items(), key=lambda kv: -kv[1]):
            if sec != "Unclassified" and w > rule.pct:
                members = ", ".join(s for s in weights if sectors.get(s) == sec)
                alerts.append(Alert(f"{rule.id}:{sec}", f"{sec} is {w:.1f}% of your stocks",
                                    f"Limit {rule.pct:.0f}%. Holdings: {members}. {rule.note}".strip(),
                                    None, rule.severity))
    return alerts


def evaluate_all(rules: List[Rule], md: MarketData, sectors: Dict[str, str], now: datetime) -> List[Alert]:
    alerts: List[Alert] = []
    for r in rules:
        if r.type in STOCK_RULES:
            a = evaluate_stock_rule(r, md, now)
            if a:
                alerts.append(a)
        else:
            alerts.extend(evaluate_portfolio_rule(r, md, sectors))
    return alerts
