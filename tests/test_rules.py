from datetime import date, datetime, time
from pathlib import Path

import pytest

from guardian.broker import Candle, Holding
from guardian.rules import (IST, MarketData, Rule, completed_daily, completed_weekly,
                            evaluate_all, evaluate_stock_rule)
from guardian.state import AlertState


def c(d: date, close: float, hi=None) -> Candle:
    ts = int(datetime.combine(d, time(9, 15), IST).timestamp())
    return Candle(ts, close, hi or close, close, close, 0)


def md(holdings, ltp, daily=None, weekly=None):
    scrip_of = {h.symbol: f"NSE_{h.security_id}" for h in holdings}
    return MarketData(holdings, ltp, daily or {}, weekly or {}, scrip_of)


H = Holding("WAAREEENER", "1", 6, 2394.85)


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_daily_close_ignores_running_candle():
    series = [c(date(2026, 9, 30), 2374.4), c(date(2026, 10, 1), 2290)]
    rule = Rule.from_dict(dict(id="w", type="close_below", symbol="WAAREEENER", level=2300))
    data = md([H], {"NSE_1": 2290}, daily={"NSE_1": series})
    # 2pm on 1 Oct: today's candle is still forming -> uses 30 Sep close (2374.4) -> no alert
    assert evaluate_stock_rule(rule, data, at(2026, 10, 1, 14, 0)) is None
    # after the close: 2290 < 2300 -> alert
    a = evaluate_stock_rule(rule, data, at(2026, 10, 1, 15, 45))
    assert a and "2,290.00" in a.detail


def test_weekly_close_waits_for_friday():
    wk = [c(date(2026, 9, 21), 269), c(date(2026, 9, 28), 248)]
    itc = Holding("ITC", "2", 94, 269.05)
    rule = Rule.from_dict(dict(id="i", type="close_below", symbol="ITC", level=250, timeframe="week"))
    data = md([itc], {"NSE_2": 248}, weekly={"NSE_2": wk})
    assert evaluate_stock_rule(rule, data, at(2026, 10, 1, 16, 0)) is None   # Thursday
    assert evaluate_stock_rule(rule, data, at(2026, 10, 2, 15, 45)) is not None  # Friday after close


def test_holiday_shortened_week_counts_from_saturday():
    wk = [c(date(2026, 9, 28), 248)]
    out_fri_morning = completed_weekly(wk, at(2026, 10, 2, 10, 0))
    out_sat = completed_weekly(wk, at(2026, 10, 3, 10, 0))
    assert out_fri_morning == [] and len(out_sat) == 1


def test_price_and_loss_rules():
    data = md([H], {"NSE_1": 2000})
    below = Rule.from_dict(dict(id="p", type="price_below", symbol="WAAREEENER", level=2100))
    loss = Rule.from_dict(dict(id="l", type="loss_from_cost", symbol="WAAREEENER", pct=15))
    now = at(2026, 10, 1, 12, 0)
    assert evaluate_stock_rule(below, data, now) is not None
    a = evaluate_stock_rule(loss, data, now)
    assert a and "16.5%" in a.title   # 2000/2394.85 - 1 = -16.5%


def test_off_52w_high():
    series = [c(date(2026, 1, 5), 3000, hi=3718.8), c(date(2026, 10, 1), 2340)]
    data = md([H], {"NSE_1": 2340}, daily={"NSE_1": series})
    r = Rule.from_dict(dict(id="h", type="off_52w_high", symbol="WAAREEENER", pct=30))
    a = evaluate_stock_rule(r, data, at(2026, 10, 1, 16, 0))
    assert a and "37.1%" in a.title


def test_portfolio_weights_and_sectors():
    hs = [Holding("INFY", "1", 10, 100), Holding("TCS", "2", 10, 100), Holding("ITC", "3", 20, 100)]
    data = md(hs, {"NSE_1": 100, "NSE_2": 100, "NSE_3": 100})
    rules = [Rule.from_dict(dict(id="pos", type="max_position_weight", pct=40)),
             Rule.from_dict(dict(id="sec", type="max_sector_weight", pct=45))]
    alerts = evaluate_all(rules, data, {"INFY": "IT", "TCS": "IT", "ITC": "FMCG"}, at(2026, 10, 1, 16, 0))
    ids = {a.rule_id for a in alerts}
    assert ids == {"pos:ITC", "sec:FMCG", "sec:IT"}   # ITC 50%, IT 50%, FMCG 50%


def test_unheld_symbol_without_scrip_gives_info():
    r = Rule.from_dict(dict(id="x", type="price_above", symbol="BHARTIARTL", level=1))
    a = evaluate_stock_rule(r, md([H], {}), at(2026, 10, 1, 12, 0))
    assert a.severity == "info"


def test_bad_rules_rejected():
    with pytest.raises(ValueError):
        Rule.from_dict(dict(id="a", type="close_below", symbol="ITC"))          # no level
    with pytest.raises(ValueError):
        Rule.from_dict(dict(id="b", type="nonsense", symbol="ITC", level=1))
    with pytest.raises(ValueError):
        Rule.from_dict(dict(id="c", type="close_below", symbol="ITC", level=1, timeframe="month"))


def test_state_dedupes_and_rearms(tmp_path: Path):
    from guardian.rules import Alert
    st = AlertState(tmp_path / "s.json")
    a = Alert("r1", "t", "d")
    new, _ = st.diff([a]); assert new == [a]; st.save([a])
    st2 = AlertState(tmp_path / "s.json")
    new, _ = st2.diff([a]); assert new == []          # already sent
    new, cleared = st2.diff([]); assert cleared == ["r1"]; st2.save([])
    new, _ = AlertState(tmp_path / "s.json").diff([a]); assert new == [a]   # re-armed
