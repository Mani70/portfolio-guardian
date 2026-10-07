"""Phase-3 research additions: practitioner swing methods, extra rotations, liquid universe, intraday lab."""
import numpy as np
import pandas as pd

from backtest import intraday_lab as lab
from backtest.engine import Costs, simulate
from backtest.rotation import _rebalance_days, clenow_score, equal_weight_trend, high52_rotation
from backtest.strategies import minervini_breakout, rs_rating, weinstein_stage2
from backtest.universe import liquid_top, nse_equity_symbols

ZERO = Costs(brokerage_per_order=0, stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0,
             gst_pct=0, dp_per_sell=0, slippage_pct=0)


def daily(closes, start="2023-01-02", volume=1e5):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": volume}, index=idx)


def test_weekly_and_biweekly_rebalance_days_are_week_ends():
    d = pd.bdate_range("2024-01-01", "2024-02-02")
    wk = sorted(_rebalance_days(d, "weekly"))
    assert all(x.weekday() == 4 for x in wk) and len(wk) == 4      # the last Friday is the final date: excluded
    assert sorted(_rebalance_days(d, "biweekly")) == wk[1::2]


def test_engine_percentage_stop():
    df = daily([100] * 30 + [90] * 5)
    df["atr"] = 1.0
    df["entry"] = False
    df["exit"] = False
    df["score"] = 1.0
    df.loc[df.index[5], "entry"] = True
    res = simulate({"A": df}, capital=10_000, max_positions=1, costs=ZERO, stop_atr=50, stop_pct=8)
    t = res.trades[0]
    assert t.reason == "stop" and abs(t.exit_px - 90) < 1e-9          # gapped through the 92 stop: filled at the open


def test_minervini_needs_tight_base_breakout_on_volume():
    rng = np.random.default_rng(1)
    up = list(100 * np.exp(np.linspace(0, 0.9, 260)))
    base = [up[-1] * (1 + rng.uniform(-0.03, 0.03)) for _ in range(25)]
    closes = up + base + [base[-1] * 1.08]
    vol = np.full(len(closes), 1e5)
    vol[-1] = 3e5
    df = daily(closes)
    df["volume"] = vol
    sig = minervini_breakout(df)
    assert bool(sig["entry"].iloc[-1]) and not sig["entry"].iloc[-26:-1].any()
    df.loc[df.index[-1], "volume"] = 1e5                                  # same breakout, no volume
    assert not bool(minervini_breakout(df)["entry"].iloc[-1])


def test_weinstein_signals_land_on_the_weeks_last_session():
    closes = [100] * 200 + list(np.linspace(100, 70, 60)) + list(np.linspace(70, 70, 40)) + list(np.linspace(70, 95, 30))
    df = daily(closes)
    df.loc[df.index[-30:], "volume"] = 4e5
    sig = weinstein_stage2(df, vol_mult=1.0)
    for d in sig.index[sig["entry"] | sig["exit"]]:
        nxt = sig.index[sig.index > d]
        assert len(nxt) == 0 or nxt[0].isocalendar()[1] != d.isocalendar()[1]   # no later session that week


def test_rs_rating_ranks_stronger_stock_higher():
    a, b = daily(np.linspace(100, 200, 300)).close, daily(np.linspace(100, 110, 300)).close
    rs = rs_rating(pd.DataFrame({"A": a, "B": b}))
    assert rs["A"].iloc[-1] == 100 and rs["B"].iloc[-1] == 50


def test_clenow_prefers_smooth_trends():
    x = np.arange(120)
    smooth = pd.Series(100 * np.exp(0.002 * x))
    choppy = pd.Series(100 * np.exp(0.002 * x + 0.05 * np.sin(x)))
    assert clenow_score(smooth) > clenow_score(choppy) > 0


def test_high52_picks_stock_at_its_high_and_equal_weight_trend_respects_filter():
    near = daily(np.linspace(100, 150, 300))
    far = daily(list(np.linspace(100, 200, 150)) + list(np.linspace(200, 120, 150)))
    bench = daily(np.linspace(100, 130, 300))
    res = high52_rotation({"NEAR": near, "FAR": far}, bench, near.index[260:], 100_000, ZERO, 0.0, slots=1, exit_rank=1)
    assert {t.symbol for t in res.trades} == {"NEAR"}
    falling = daily(np.linspace(130, 100, 300))
    res = equal_weight_trend({"NEAR": near, "FAR": far}, falling, near.index[260:], 100_000, ZERO, 0.0)
    assert res.trades == [] and abs(res.equity.iloc[-1] - 100_000) < 1e-6


def test_liquid_universe_uses_liquidity_before_the_start_only():
    idx = pd.bdate_range("2022-01-03", periods=600)
    early_big = pd.DataFrame({"close": 100.0, "volume": np.r_[np.full(300, 1e6), np.full(300, 1e3)]}, index=idx)
    late_big = pd.DataFrame({"close": 100.0, "volume": np.r_[np.full(300, 1e3), np.full(300, 1e7)]}, index=idx)
    assert liquid_top({"EARLY": early_big, "LATE": late_big}, idx[300], top=1) == ["EARLY"]


def test_nse_equity_symbols_filters_etfs_and_other_series():
    class C:
        def equity_instruments(self):
            return [{"EXCH": "NSE", "SERIES": "EQ", "TRADING_SYMBOL": "RELIANCE-EQ", "EXPIRY_CODE": "0"},
                    {"EXCH": "NSE", "SERIES": "EQ", "TRADING_SYMBOL": "NIFTYBEES-EQ", "EXPIRY_CODE": "0"},
                    {"EXCH": "NSE", "SERIES": "BE", "TRADING_SYMBOL": "XYZ-BE", "EXPIRY_CODE": "0"},
                    {"EXCH": "BSE", "SERIES": "A", "TRADING_SYMBOL": "TCS", "EXPIRY_CODE": "0"}]
    assert nse_equity_symbols(C()) == ["RELIANCE"]


def test_intraday_lab_gap_fade_and_late_momentum():
    A = np.full((1, 1, lab.BARS, 5), 100.0)
    A[0, 0, :, 4] = 1000
    A[0, 0, 0, :4] = [98, 98.5, 97.8, 98]          # gaps down 2% from 100, first bar closes at 98
    A[0, 0, lab.EXIT, 3] = 99.0                      # recovers to 99 by 15:15
    ctx = {"prev_close": np.array([[100.0]]), "atr": np.array([[3.0]]), "sma20": np.array([[np.nan]])}
    tr = lab.gap_trade(A, ctx, 0.01, "fade")
    assert tr.side.tolist() == ["long"] and abs(tr.gross.iloc[0] - (99 / 98 - 1)) < 1e-12
    A[0, 0, lab.T(9, 40), 3] = 97.0                  # first half hour down -> short the last half hour
    A[0, 0, lab.T(14, 40), 3] = 100.0
    tr = lab.late_momentum(A, ctx)
    assert tr.side.tolist() == ["short"] and abs(tr.gross.iloc[0] - (1 - 99 / 100)) < 1e-12
