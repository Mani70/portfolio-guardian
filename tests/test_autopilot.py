"""Autopilot (trader/autopilot.py, trader/retest.py; research/PREREGISTRATION.md Addendum 14)."""
import shutil
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from trader import autopilot as AP
from trader import config as C
from trader.brokers.paper import PaperBroker
from trader.engine import Engine
from trader.journal import Journal
from trader.market import ReplayMarket
from trader.models import Order, OrderRequest, Position
from trader.retest import decide, run
from trader.risk import ALL_CASH, Risk
from trader.strategies import build
from tests.test_trader import ZERO, cfg

ROOT = Path(__file__).resolve().parent.parent


def _root(tmp_path, active=None):
    (tmp_path / "trader").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "trader" / "rulebook.yaml", tmp_path / "trader" / "rulebook.yaml")
    if active:
        AP.save_state(tmp_path, {"active": active})
    return tmp_path


def _cfg(**over):
    c = C._merge(C.DEFAULTS, over)
    c["_path"] = "test"
    return c


def test_off_unless_enabled(tmp_path):
    c = _cfg(strategies={"momentum_rotation": {"enabled": True, "live": True}})
    assert AP.apply(c, _root(tmp_path)) == c


def test_rulebook_decides_the_strategies(tmp_path):
    c = _cfg(mode="live", autopilot={"enabled": True, "reserve": 10_000},
             strategies={"momentum_rotation": {"enabled": True, "live": True}})
    out = AP.apply(c, _root(tmp_path))
    assert set(out["strategies"]) == {"core", "gap_reversal"}               # trader.yaml's own list is ignored
    core = out["strategies"]["core"]
    assert core["class"] == "core_allocation" and core["live"] and core["max_drawdown_pct"] == 100
    assert core["params"]["weights"]["NIFTYBEES"] == 0.45 and not out["strategies"]["gap_reversal"]["live"]
    assert out["capital"]["all_cash"] and out["capital"]["reserve"] == 10_000
    assert out["_autopilot"] == {"rule": "L1", "core": "core", "retired": ["trend_allocation", "momentum_rotation"]}
    assert [type(s).__name__ for s in build(out)] == ["CoreAllocation", "GapReversal"]


def test_the_yearly_switch_changes_the_weights(tmp_path):
    out = AP.apply(_cfg(autopilot={"enabled": True}), _root(tmp_path, active="F"))
    assert out["_autopilot"]["rule"] == "F"
    assert out["strategies"]["core"]["params"]["weights"] == {"NIFTYBEES": 0.8, "LIQUIDCASE": 0.2}
    AP.save_state(tmp_path, {"active": "NO_SUCH_RULE"})                     # a bad state file falls back safely
    assert AP.apply(_cfg(autopilot={"enabled": True}), tmp_path)["_autopilot"]["rule"] == "L1"


def test_handover_moves_retired_holdings_in_both_books_but_waits_for_working_orders(tmp_path):
    c = AP.apply(_cfg(mode="live", autopilot={"enabled": True}), _root(tmp_path))
    jp = tmp_path / "j.db"
    t = datetime(2026, 9, 30, 16, 10)
    live, paper = Journal(jp, "live"), Journal(jp, "paper")
    live.save_position(Position("trend_allocation", "MON100", "CNC", 55, 332.2, t, "T1"))
    paper.save_position(Position("trend_allocation", "MON100", "CNC", 57, 326.41, t, "T2"))
    live.save_order(Order(OrderRequest("M1", "momentum_rotation", "INFY", "1", "BUY", 5, "CNC", 1500.0, "s",
                                       "entry", t, None, None, 1500.0, True)))
    live.save_position(Position("momentum_rotation", "INFY", "CNC", 5, 1500.0, t, "M0"))
    live.close(), paper.close()
    notes = AP.handover(c, jp)
    assert any("MON100: 55 units" in n for n in notes) and not any("INFY" in n for n in notes)
    live, paper = Journal(jp, "live"), Journal(jp, "paper")
    assert [p.symbol for p in live.positions(strategy="core")] == ["MON100"]
    assert [p.symbol for p in paper.positions(strategy="core")] == ["MON100"]
    assert [p.symbol for p in live.positions(strategy="momentum_rotation")] == ["INFY"]   # its order still works
    assert AP.handover(c, jp) == []                                        # nothing more to move


def test_live_capital_is_the_whole_account_paper_keeps_its_own(tmp_path):
    c = _cfg(capital={"swing": 420_000, "intraday": 0, "all_cash": True, "live_from_account": True})
    assert Risk(c, Journal(tmp_path / "j.db", "live"), tmp_path).capital("swing") == ALL_CASH
    assert Risk(c, Journal(tmp_path / "j.db", "paper"), tmp_path).capital("swing") == 420_000


W = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}


def _frame(prices, start="2025-01-01"):
    idx = pd.bdate_range(start, periods=len(prices))
    c = np.asarray(prices, float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5}, index=idx)


def test_new_money_is_swept_in_mid_month_with_buys_only(tmp_path):
    from trader.strategies.allocation import CoreAllocation
    daily = {k: _frame([100.0] * 300) for k in W}
    m = ReplayMarket(daily, {})
    p = {"weights": W}

    def engine(capital):
        c = cfg(capital={"swing": capital, "intraday": 0},
                strategies={"core": {"enabled": True, "class": "core_allocation", "params": p}})
        s = CoreAllocation("core", p)
        s.sessions = m.sessions
        return Engine(c, j, PaperBroker(m, ZERO), m, None, None, [s], tmp_path, [], "AAA", quiet=True)

    j = Journal(tmp_path / "j.db", "paper")
    eng = engine(100_000)
    for d in [d for d in m.sessions if date(2025, 9, 25) <= d <= date(2025, 10, 3)]:
        eng.swing_check(datetime.combine(d, time(9, 30)))
        eng.swing_plan(datetime.combine(d, time(16, 10)))
    before = {q.symbol: q.qty for q in j.positions()}
    eng = engine(105_000)                                                   # +Rs 5,000: below the sweep threshold
    eng.swing_plan(datetime.combine(date(2025, 10, 8), time(16, 10)))
    assert {q.symbol: q.qty for q in j.positions()} == before
    eng = engine(160_000)                                                   # +Rs 60,000 deposited mid-month
    eng.swing_plan(datetime.combine(date(2025, 10, 9), time(16, 10)))
    eng.swing_check(datetime.combine(date(2025, 10, 10), time(9, 30)))
    after = {q.symbol: q.qty for q in j.positions()}
    assert all(after[k] > before[k] for k in W)                             # every ETF topped up, nothing sold
    assert not [o for o in j.load_orders() if o.req.side == "SELL"]
    vals = {k: after[k] * 100.0 for k in W}
    tot = sum(vals.values())
    assert tot > 155_000 and all(abs(vals[k] / tot - W[k]) < 0.01 for k in W)


def test_retest_rule():
    assert decide({}, True, True) == ("L1", 0, "both tests passed: L1")
    assert decide({"active": "L1"}, True, False)[0] == "L1"                 # one test-2 failure: keep
    assert decide({"active": "L1", "test2_fail_streak": 1}, True, False)[:2] == ("F", 2)
    assert decide({"active": "L1"}, False, True)[0] == "F"                  # test 1 failure: at once
    assert decide({"active": "F", "test2_fail_streak": 3}, True, True) == ("L1", 0, "both tests passed: L1 again")


def test_retest_without_data_changes_nothing(tmp_path):
    root = _root(tmp_path, active="L1")
    said = []
    msg = run(root, notify=said.append, update=False, force=True, today=date(2027, 1, 9))
    assert "could not run" in msg and said == [msg] and AP.load_state(root) == {"active": "L1"}
    assert "not January" in run(root, update=False, today=date(2027, 3, 6))


def test_all_cash_sizes_nothing_when_the_account_cannot_be_read(tmp_path):
    c = _cfg(capital={"swing": 420_000, "intraday": 0, "all_cash": True, "live_from_account": True},
             strategies={"core": {"capital_share": 1.0}})
    r = Risk(c, Journal(tmp_path / "j.db", "live"), tmp_path)
    r.account_equity = None                                                 # funds unreadable this evening
    assert r.strategy_capital("core", "swing") == 0.0
    r.account_equity, r.live_plan = 250_000.0, ALL_CASH                     # read: the account is the plan
    assert abs(r.strategy_capital("core", "swing") - 250_000) < 1


def test_live_autopilot_invests_the_free_cash_above_the_reserve(tmp_path):
    from trader.instruments import Instruments
    from trader.strategies.allocation import CoreAllocation
    from tests.test_trader import FakeLive, Sink
    daily = {k: _frame([100.0] * 300) for k in W}
    m = ReplayMarket(daily, {})
    p = {"weights": W}
    c = cfg(mode="live", capital={"swing": 420_000, "intraday": 0, "all_cash": True, "live_from_account": True,
                                  "reserve": 10_000},
            strategies={"core": {"enabled": True, "class": "core_allocation", "live": True, "override_gate": True,
                                 "max_order_value": 1e12, "max_drawdown_pct": 100, "params": p}})
    s = CoreAllocation("core", {**p, "_start_now": True})
    s.sessions = m.sessions
    b = FakeLive()
    b.available_funds = lambda product: 200_000.0
    eng = Engine(c, Journal(tmp_path / "j.db", "live"), b, m, Instruments({k: (str(i), 0.01) for i, k in enumerate(W)}),
                 Sink(), [s], tmp_path, [], "AAA", quiet=True)
    eng.swing_plan(datetime.combine(date(2025, 10, 9), time(16, 10)))
    spent = {r.symbol: r.qty * r.ref_price for r in b.sent if r.side == "BUY"}
    tot = sum(spent.values())
    assert 180_000 < tot <= 190_000                                          # Rs 2 lakh less the 10k reserve and headroom
    assert all(abs(spent[k] / tot - W[k]) < 0.01 for k in W)
    assert sum(r.qty * r.limit_price for r in b.sent) * 1.01 <= 190_000      # never more than the money there
