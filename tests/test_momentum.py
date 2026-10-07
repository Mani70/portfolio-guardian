"""Month-end momentum paper tracker: calendar gate, target rule, paper accounting, end-to-end run."""
import json
from datetime import date

import numpy as np
import pandas as pd

import backtest.data as bdata
import guardian.broker as gbroker
import guardian.main as gmain
import guardian.momentum as mom
import guardian.notifier as gnotifier
from backtest.engine import Costs

ZERO = Costs(brokerage_per_order=0, stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0,
             gst_pct=0, dp_per_sell=0, slippage_pct=0)


def test_last_weekday_of_month():
    assert mom.is_last_weekday_of_month(date(2026, 9, 30))       # Wednesday
    assert not mom.is_last_weekday_of_month(date(2026, 9, 29))
    assert mom.is_last_weekday_of_month(date(2026, 10, 30))      # Friday; 31st is a Saturday
    assert not mom.is_last_weekday_of_month(date(2026, 10, 31))  # Saturday
    assert mom.is_last_weekday_of_month(date(2026, 2, 27))


def test_choose_targets_keeps_buffer_and_respects_market_filter():
    scores = pd.Series([3, 2, 1, 0, -1], index=["A", "B", "C", "D", "E"], dtype=float)
    assert mom.choose_targets(scores, [], True, 2, 4) == ["A", "B"]
    assert mom.choose_targets(scores, ["D"], True, 2, 4) == ["D", "A"]      # D still in top 4: kept
    assert mom.choose_targets(scores, ["E"], True, 2, 4) == ["A", "B"]      # E fell out of top 4: sold
    assert mom.choose_targets(scores, ["Z"], True, 2, 4) == ["A", "B"]      # no longer eligible: sold
    assert mom.choose_targets(scores, ["A"], False, 2, 4) == []             # market below 200-day: cash


def test_rebalance_accounting_and_cash_yield():
    d0, d1 = pd.Timestamp("2026-01-30"), pd.Timestamp("2026-02-27")
    st = mom.new_state(100_000, d0, 50.0)
    acts = mom.rebalance(st, ["A", "B"], {"A": 100.0, "B": 200.0}, d0, 2, ZERO, 0.0)
    assert [(a, s) for a, s, *_ in acts] == [("BUY", "A"), ("BUY", "B")]
    assert st["holdings"]["A"]["qty"] == 498 and st["holdings"]["B"]["qty"] == 249   # 50k each, 0.3% buffer
    cash_after_buys = st["cash"]
    assert abs(cash_after_buys - (100_000 - 498 * 100 - 249 * 200)) < 1e-6

    acts = mom.rebalance(st, ["A"], {"A": 110.0, "B": 180.0}, d1, 2, ZERO, 6.0)
    sell = [a for a in acts if a[0] == "SELL"][0]
    assert sell[1] == "B" and abs(sell[4] - 249 * (180 - 200)) < 1e-6
    grown = cash_after_buys * 1.06 ** ((d1 - d0).days / 365.25)
    assert abs(st["cash"] - (grown + 249 * 180)) < 1e-6
    assert [a[0] for a in acts if a[1] == "A"] == ["HOLD"]
    assert st["rebalances"] == 2 and st["last_rebalance"] == "2026-02-27"
    assert abs(mom.portfolio_value(st, {"A": 110.0}) - (st["cash"] + 498 * 110)) < 1e-6


def test_rebalance_charges_costs_and_slippage():
    d = pd.Timestamp("2026-01-30")
    c = Costs()
    st = mom.new_state(100_000, d, None)
    acts = mom.rebalance(st, ["A"], {"A": 100.0}, d, 1, c, 0.0)
    _, _, qty, px, _ = acts[0]
    assert abs(px - 100.05) < 1e-9                                   # bought 0.05% above the close
    assert abs(st["cash"] - (100_000 - qty * px - c.buy(qty * px))) < 1e-6
    assert st["costs_paid"] > 0


def test_compose_message_mentions_trades_and_benchmark():
    d = pd.Timestamp("2026-02-27")
    st = mom.new_state(100_000, pd.Timestamp("2026-01-30"), 250.0)
    acts = mom.rebalance(st, ["A"], {"A": 100.0}, d, 1, ZERO, 0.0)
    msg = mom.compose_message(d, True, "NIFTYBEES", 275.0, 260.0, pd.Series({"A": 1.5}), acts, st,
                              {"A": 100.0}, 6.25, ["XYZ"], False, 1)
    assert "INVEST" in msg and "BUY  A" in msg and "+10.0%" in msg and "XYZ" in msg and "no orders" in msg


# ---------- end-to-end with a fake broker ----------
def _frame(drift, n=400, end="2026-09-30", seed=0):
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5}, index=idx)


def _run(tmp_path, frames, argv):
    sent = []
    saved = (mom.ROOT, mom.PAPER_DIR, bdata.update_history, gmain.instrument_index,
             gbroker.IndStocksClient.from_env, gnotifier.Notifier.send)
    mom.ROOT, mom.PAPER_DIR = tmp_path, tmp_path / "paper"
    bdata.update_history = lambda client, codes, years, store, **kw: {s: frames[s] for s in codes}
    gmain.instrument_index = lambda client, cache: {"NSE": {s: str(i) for i, s in enumerate(frames)}}
    gbroker.IndStocksClient.from_env = classmethod(lambda cls, *a, **k: object())
    gnotifier.Notifier.send = lambda self, text: sent.append(text)
    try:
        rc = mom.main(argv)
    finally:
        (mom.ROOT, mom.PAPER_DIR, bdata.update_history, gmain.instrument_index,
         gbroker.IndStocksClient.from_env, gnotifier.Notifier.send) = saved
    return rc, sent


def test_main_end_to_end(tmp_path):
    (tmp_path / "backtest.yaml").write_text(
        "capital: 100000\nbenchmark: NIFTYBEES\nuniverse: [AAA, BBB, CCC, DDD]\n"
        "strategies:\n  momentum_rotation: {slots: 2, exit_rank: 3}\n", encoding="utf-8")
    frames = {"AAA": _frame(0.0030, seed=1), "BBB": _frame(0.0020, seed=2), "CCC": _frame(0.0010, seed=3),
              "DDD": _frame(-0.0020, seed=4), "NIFTYBEES": _frame(0.0008, seed=5)}
    state_file = tmp_path / "paper" / "momentum_portfolio.json"

    rc, sent = _run(tmp_path, frames, ["--preview", "--dry-run"])
    assert rc == 0 and "Would trade" in sent[0] and not state_file.exists()

    rc, sent = _run(tmp_path, frames, ["--force", "--dry-run"])
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert rc == 0 and sorted(st["holdings"]) == ["AAA", "BBB"] and "DDD" not in sent[0]
    log_rows = (tmp_path / "paper" / "momentum_log.csv").read_text(encoding="utf-8").splitlines()

    rc, sent = _run(tmp_path, frames, ["--force", "--dry-run"])          # same day again: no new trades
    assert json.loads(state_file.read_text(encoding="utf-8"))["rebalances"] == 1
    assert (tmp_path / "paper" / "momentum_log.csv").read_text(encoding="utf-8").splitlines() == log_rows

    falling = {s: _frame(0.003 if s != "NIFTYBEES" else -0.002, end="2026-10-30", seed=i)
               for i, s in enumerate(frames, 1)}
    rc, sent = _run(tmp_path, falling, ["--force", "--dry-run"])
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert st["holdings"] == {} and "STAY IN CASH" in sent[0] and "SELL" in sent[0]


def test_main_skips_quietly_off_month_end(tmp_path):
    rc, sent = _run(tmp_path, {}, ["--dry-run"]) if not mom.is_last_weekday_of_month(date.today()) else (0, [])
    assert rc == 0 and sent == []
