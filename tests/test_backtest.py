"""Backtester correctness: timing, stops, costs, sizing, data handling."""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import PriceStore, _windows, candles_to_frame, suspicious_jumps, update_history
from backtest.engine import Costs, simulate
from backtest.metrics import cagr, max_drawdown
from backtest.strategies import STRATEGIES, donchian_breakout, rsi
from guardian.broker import Candle

ZERO = Costs(brokerage_per_order=0, stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0,
             gst_pct=0, dp_per_sell=0, slippage_pct=0)


def frame(closes, opens=None, lows=None, entry=(), exit_=(), atr=1.0):
    n = len(closes)
    idx = pd.bdate_range("2025-01-01", periods=n)
    opens = opens or closes
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    df = pd.DataFrame({"open": opens, "high": [max(o, c) for o, c in zip(opens, closes)], "low": lows,
                       "close": closes, "atr": atr, "score": 0.0}, index=idx)
    df["entry"] = [i in entry for i in range(n)]
    df["exit"] = [i in exit_ for i in range(n)]
    return df


def test_fills_at_next_open_not_signal_close():
    df = frame([100, 100, 100, 100], opens=[100, 100, 110, 120], entry={0}, exit_={1})
    res = simulate({"A": df}, capital=10_000, max_positions=1, costs=ZERO, stop_atr=50)
    t = res.trades[0]
    assert t.entry_date == df.index[1] and t.entry_px == 100      # signal day 0 -> open of day 1
    assert t.exit_date == df.index[2] and t.exit_px == 110        # exit signal day 1 -> open of day 2
    assert t.qty == 99                                            # floor(10000 / (100 * 1.003))


def test_stop_fills_at_stop_or_gap_open():
    # entry day 1 at 100, stop = 100 - 3*2 = 94
    df = frame([100, 100, 99, 90], opens=[100, 100, 99, 90], lows=[100, 99, 95, 89], entry={0}, atr=2.0)
    res = simulate({"A": df}, capital=10_000, max_positions=1, costs=ZERO, stop_atr=3)
    t = res.trades[0]
    assert t.reason == "stop" and t.exit_date == df.index[3] and t.exit_px == 90   # gapped below 94: open
    df2 = frame([100, 100, 99, 95], opens=[100, 100, 99, 97], lows=[100, 99, 95, 93], entry={0}, atr=2.0)
    t2 = simulate({"A": df2}, capital=10_000, max_positions=1, costs=ZERO, stop_atr=3).trades[0]
    assert t2.reason == "stop" and t2.exit_px == 94                                 # touched intraday: stop


def test_costs_reduce_pnl_by_exact_amount():
    c = Costs()
    df = frame([100] * 4, entry={0}, exit_={1})
    res = simulate({"A": df}, capital=100_000, max_positions=1, costs=c, stop_atr=50)
    t = res.trades[0]
    buy_px, sell_px = 100 * 1.0005, 100 * 0.9995
    expected = t.qty * sell_px - c.sell(t.qty * sell_px) - (t.qty * buy_px + c.buy(t.qty * buy_px))
    assert abs(t.pnl - expected) < 1e-6
    assert abs(res.costs_paid - (c.buy(t.qty * buy_px) + c.sell(t.qty * sell_px))) < 1e-6
    assert abs(res.equity.iloc[-1] - (100_000 + t.pnl)) < 1e-6


def test_slots_limit_and_best_score_first():
    a = frame([100] * 3, entry={0})
    b = frame([100] * 3, entry={0})
    a["score"], b["score"] = 1.0, 5.0
    res = simulate({"A": a, "B": b}, capital=10_000, max_positions=1, costs=ZERO, stop_atr=50)
    assert [t.symbol for t in res.trades] == ["B"]


def test_no_lookahead_in_breakout_levels():
    closes = list(np.linspace(100, 120, 260))
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes},
                      index=pd.bdate_range("2024-01-01", periods=260))
    out = donchian_breakout(df)
    # the breakout level must come from PRIOR days, so a steadily rising close keeps breaking out
    assert out["entry"].iloc[-1]
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("high")] = 999      # today's high must not block today's signal
    assert donchian_breakout(df2)["entry"].iloc[-1]


def test_rsi_bounds_and_all_strategies_run():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c},
                      index=pd.bdate_range("2024-01-01", periods=400))
    r = rsi(df["close"], 2).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    for fn in STRATEGIES.values():
        out = fn(df)
        assert {"entry", "exit", "score", "atr"} <= set(out.columns)
        assert out["entry"].dtype == bool


def test_metrics():
    idx = pd.date_range("2020-01-01", "2022-01-01", freq="D")
    eq = pd.Series(np.linspace(100, 121, len(idx)), index=idx)
    assert abs(cagr(eq) - 10.0) < 0.1
    assert max_drawdown(pd.Series([100, 120, 90, 130])) == -25.0


def test_jump_detection():
    df = pd.DataFrame({"close": [100, 101, 50, 51]})
    assert len(suspicious_jumps(df)) == 1


def test_windows_cover_range_in_one_year_steps():
    w = _windows(datetime(2020, 1, 1), datetime(2023, 6, 1))
    assert w[0][1] == datetime(2023, 6, 1) and w[-1][0] == datetime(2020, 1, 1)
    assert all((e - s).days <= 364 for s, e in w)


class FakeClient:
    def __init__(self):
        self.calls = []

    def candles_many(self, codes, interval, start_ms, end_ms):
        self.calls.append((tuple(codes), start_ms, end_ms))
        assert len(codes) <= 5
        days = pd.bdate_range(pd.Timestamp(start_ms, unit="ms"), pd.Timestamp(end_ms, unit="ms"))
        out = {}
        for code in codes:
            out[code] = [Candle(int((d + pd.Timedelta(hours=3, minutes=45)).timestamp()), 1, 2, 0.5, 1.5, 10)
                         for d in days]
        return out


def test_update_history_batches_caches_and_tops_up(tmp_path: Path):
    store = PriceStore(tmp_path)
    codes = {f"S{i}": f"NSE_{i}" for i in range(7)}
    fc = FakeClient()
    now = datetime(2026, 10, 3, 17, 0)
    out = update_history(fc, codes, 2, store, now=now)
    assert len(out) == 7 and all(len(df) > 400 for df in out.values())
    first_calls = len(fc.calls)
    assert first_calls == 2 * 3                     # 2 batches (5 + 2) x 3 one-year windows
    update_history(fc, codes, 2, store, now=now)
    assert len(fc.calls) == first_calls             # fully cached: no new calls
    update_history(fc, codes, 2, store, now=datetime(2026, 10, 10, 17, 0))   # next Saturday
    assert len(fc.calls) == first_calls + 2         # one short top-up window per batch


def test_last_completed_session():
    from backtest.data import last_completed_session
    from datetime import date
    assert last_completed_session(datetime(2026, 10, 3, 10, 0)) == date(2026, 10, 2)   # Sat -> Fri
    assert last_completed_session(datetime(2026, 10, 5, 11, 0)) == date(2026, 10, 2)   # Mon morning -> Fri
    assert last_completed_session(datetime(2026, 10, 5, 16, 0)) == date(2026, 10, 5)   # Mon after close


def test_candles_to_frame_uses_ist_dates():
    ts = int(pd.Timestamp("2026-10-01 03:45", tz="UTC").timestamp())    # 09:15 IST
    df = candles_to_frame([Candle(ts, 1, 2, 0.5, 1.5, 10)])
    assert df.index[0] == pd.Timestamp("2026-10-01")
