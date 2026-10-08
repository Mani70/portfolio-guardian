"""The long-term core (trader/strategies/allocation.py + Engine._allocate) on synthetic prices, paper broker."""
from datetime import date, datetime, time

import numpy as np
import pandas as pd

from trader.brokers.paper import PaperBroker
from trader.engine import Engine
from trader.journal import Journal
from trader.market import ReplayMarket
from trader.models import Position
from trader.run import transfer_positions
from trader.strategies.allocation import CoreAllocation
from tests.test_trader import ZERO, cfg

W = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}


def _frame(prices, start="2025-01-01"):
    idx = pd.bdate_range(start, periods=len(prices))
    c = np.asarray(prices, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5}, index=idx)


def _setup(tmp_path, daily, params=None):
    m = ReplayMarket(daily, {})
    p = {"weights": W, **(params or {})}
    c = cfg(capital={"swing": 100_000, "intraday": 0},
            strategies={"core": {"enabled": True, "class": "core_allocation", "params": p}})
    s = CoreAllocation("core", p)
    s.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "paper")
    eng = Engine(c, j, PaperBroker(m, ZERO), m, None, None, [s], tmp_path, [], "AAA", quiet=True)
    return m, j, eng


def _run(m, eng, first, last):
    for d in [d for d in m.sessions if first <= d <= last]:
        eng.swing_check(datetime.combine(d, time(9, 30)))
        eng.swing_plan(datetime.combine(d, time(15, 45)))


def _weights(j, daily, d):
    px = {s: float(df.loc[:pd.Timestamp(d), "close"].iloc[-1]) for s, df in daily.items()}
    v = {p.symbol: p.qty * px[p.symbol] for p in j.positions()}
    tot = sum(v.values())
    return {k: x / tot for k, x in v.items()}, tot


def test_first_purchase_matches_the_target_weights(tmp_path):
    n = 300
    daily = {k: _frame([100.0] * n) for k in W}
    m, j, eng = _setup(tmp_path, daily)
    _run(m, eng, date(2025, 10, 1), date(2025, 11, 7))               # Oct month-end buys, Nov 3 fills
    w, tot = _weights(j, daily, date(2025, 11, 7))
    assert all(abs(w[k] - W[k]) < 0.01 for k in W) and tot > 95_000


def test_drift_beyond_the_band_is_rebalanced_with_partial_sells_and_top_ups(tmp_path):
    n = 300
    up = [100.0] * 214 + [160.0] * 86                                 # AAA jumps 60% on 28 Oct 2025
    daily = {"AAA": _frame(up), "BBB": _frame([100.0] * n), "CCC": _frame([100.0] * n)}
    m, j, eng = _setup(tmp_path, daily)
    _run(m, eng, date(2025, 9, 1), date(2025, 10, 10))                # bought at Sep's end at 100
    held_before = {p.symbol: p.qty for p in j.positions()}
    _run(m, eng, date(2025, 10, 11), date(2025, 11, 12))              # Oct's end: AAA ~62% -> rebalance
    w, _ = _weights(j, daily, date(2025, 11, 12))
    assert all(abs(w[k] - W[k]) < 0.02 for k in W), w
    after = {p.symbol: p.qty for p in j.positions()}
    assert 0 < after["AAA"] < held_before["AAA"]                       # part of AAA sold, not all
    assert after["BBB"] > held_before["BBB"] and after["CCC"] > held_before["CCC"]   # topped up


def test_inside_the_band_nothing_is_traded_except_at_year_end(tmp_path):
    n = 300
    drift = [100.0] * 214 + [110.0] * 86                              # AAA +10%: 52%, inside the band
    daily = {"AAA": _frame(drift), "BBB": _frame([100.0] * n), "CCC": _frame([100.0] * n)}
    m, j, eng = _setup(tmp_path, daily)
    _run(m, eng, date(2025, 9, 1), date(2025, 10, 10))
    n_orders = len(j.load_orders())
    _run(m, eng, date(2025, 10, 11), date(2025, 11, 28))              # Oct's review: no trade
    assert len(j.load_orders()) == n_orders
    _run(m, eng, date(2025, 11, 29), date(2026, 1, 9))                # Dec's review: year-end rebalance
    assert len(j.load_orders()) > n_orders


def test_needs_rebalance_reasons():
    s = CoreAllocation("core", {"weights": W})
    assert s.needs_rebalance({}, W, date(2025, 10, 31), False) == "first purchase"
    assert s.needs_rebalance(W, W, date(2025, 10, 31), False) is None
    assert s.needs_rebalance(W, W, date(2025, 12, 31), True) == "year-end rebalance"
    assert "AAA" in s.needs_rebalance({"AAA": 0.56, "BBB": 0.26, "CCC": 0.18}, W, date(2025, 10, 31), False)
    assert CoreAllocation("x", {"weights": {"A": 2, "B": 2}}).weights() == {"A": 0.5, "B": 0.5}


def test_transfer_moves_and_merges_positions(tmp_path):
    j = Journal(tmp_path / "j.db", "live")
    t = datetime(2026, 1, 5, 9, 30)
    j.save_position(Position("trend_allocation", "MON100", "CNC", 55, 332.2, t, "T1"))
    j.save_position(Position("trend_allocation", "JUNIORBEES", "CNC", 24, 754.44, t, "T2"))
    j.save_position(Position("core", "JUNIORBEES", "CNC", 6, 800.0, t, "C1"))
    notes = transfer_positions(j, "trend_allocation", "core")
    assert len(notes) == 2 and not j.positions(strategy="trend_allocation")
    pos = {p.symbol: p for p in j.positions(strategy="core")}
    assert pos["MON100"].qty == 55 and pos["MON100"].avg_price == 332.2
    assert pos["JUNIORBEES"].qty == 30 and abs(pos["JUNIORBEES"].avg_price - (24 * 754.44 + 6 * 800) / 30) < 1e-9


def test_live_rebalance_sells_tonight_and_buys_once_the_sale_money_is_in(tmp_path):
    """Real-money path: AAA is far over target and the account has little free cash. The evening sends only the
    partial sell of AAA (buys wait for its money) and keeps the review open; the next evening, after the sale filled,
    it sends the top-ups and closes the review."""
    from trader.brokers.base import Fill
    from trader.instruments import Instruments
    from trader.models import FILLED
    from tests.test_trader import FakeLive, Sink
    n = 300
    daily = {k: _frame([100.0] * n) for k in W}
    m = ReplayMarket(daily, {})
    p = {"weights": W}
    c = cfg(capital={"swing": 100_000, "intraday": 0},
            strategies={"core": {"enabled": True, "class": "core_allocation", "live": True, "override_gate": True,
                                 "params": p}})
    s = CoreAllocation("core", p)
    s.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    t0 = datetime(2025, 9, 1, 9, 30)
    j.save_position(Position("core", "AAA", "CNC", 900, 100.0, t0, "OLD"))     # 90% in AAA (e.g. transferred)
    b = FakeLive()
    money = {"free": 10_000.0}
    b.available_funds = lambda product: money["free"]
    inst = Instruments({k: (str(i + 1), 0.01) for i, k in enumerate(W)})
    eng = Engine(c, j, b, m, inst, Sink(), [s], tmp_path, [], "AAA", quiet=True)
    d1, d2 = date(2025, 10, 31), date(2025, 11, 3)
    eng.swing_plan(datetime.combine(d1, time(16, 10)))
    sells = [r for r in b.sent if r.side == "SELL"]
    assert [r.symbol for r in sells] == ["AAA"] and 398 <= sells[0].qty <= 402  # 90% -> 50% of ~₹1 lakh (account)
    assert not [r for r in b.sent if r.side == "BUY"]                          # the buys wait for that money
    assert s.state_get("last_period") is None                                  # review still open
    sell = next(o for o in j.load_orders() if o.req.side == "SELL")
    q = sell.req.qty
    b.fills = [Fill(sell.req.tag, q, 100.0, datetime.combine(d2, time(9, 15)), 0.0, side="SELL")]

    def update(orders, now):                                                   # the broker reports the fill
        out, b.fills = b.fills, []
        for o in orders:
            if any(f.tag == o.req.tag for f in out):
                o.status, o.filled_qty, o.avg_price = FILLED, o.req.qty, 100.0
        return out
    b.update = update
    money["free"] = 50_000.0
    eng.swing_plan(datetime.combine(d2, time(16, 10)))
    buys = {r.symbol: r.qty for r in b.sent if r.side == "BUY"}
    assert set(buys) == {"BBB", "CCC"} and 25_000 < buys["BBB"] * 100 <= 30_000 and 15_000 < buys["CCC"] * 100 <= 20_000
    assert s.state_get("last_period") == "2025-10"                             # review done
    assert {p.symbol: p.qty for p in j.positions()}["AAA"] == 900 - q


def test_topped_up_position_is_not_mistaken_for_a_split_but_a_real_bonus_still_is():
    from trader import corporate
    px = list(np.linspace(100, 300, 400))                             # a long rally: bought at 100, topped up at 300
    df = _frame(px, start="2024-01-01")
    first, last = df.index[0].to_pydatetime(), df.index[-1]
    p = Position("core", "AAA", "CNC", 200, 200.0, first, "E1")        # average of 100 and 300: matches no single day
    assert corporate.find_one(p, df) is not None                      # without a reference: a false "split" (the bug)
    p.meta = {"ca_px": 300.0, "ca_day": last.date().isoformat()}       # what Engine._apply_fill now records
    assert corporate.find_one(p, df) is None
    bonus = pd.concat([df, _frame([150.0] * 5, start=(last + pd.offsets.BDay(1)).strftime("%Y-%m-%d"))])
    a = corporate.find_one(p, bonus)                                   # a real 1:1 bonus after the top-up
    assert a is not None and a.factor == 0.5 and a.new_qty == 400
