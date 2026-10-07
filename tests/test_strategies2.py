"""Momentum rotation, index trend filter and intraday ORB: timing, costs, honesty rules."""
import numpy as np
import pandas as pd

from backtest.engine import Costs
from backtest.intraday import IntradayCosts, opening_stats, simulate_orb
from backtest.rotation import _rebalance_days, index_trend, market_ok, momentum_scores, simulate_rotation

ZERO = Costs(brokerage_per_order=0, stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0,
             gst_pct=0, dp_per_sell=0, slippage_pct=0)
IZERO = IntradayCosts(brokerage_per_order=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0,
                      stamp_buy_pct=0, gst_pct=0, slippage_pct=0)


def daily(closes, start="2023-01-02"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e5}, index=idx)


# ---------- rotation ----------
def test_rebalance_days_are_month_ends():
    d = pd.bdate_range("2024-01-01", "2024-04-30")
    r = sorted(_rebalance_days(d))
    assert [x.strftime("%Y-%m-%d") for x in r] == ["2024-01-31", "2024-02-29", "2024-03-29"]
    assert [x.month for x in _rebalance_days(d, "quarterly")] == [3]


def test_rotation_trades_next_open_after_month_end():
    df = daily([100.0] * 60)
    df.loc[df.index > pd.Timestamp("2023-01-31"), "open"] = 105.0   # open after month-end differs from close
    res = simulate_rotation({"A": df}, lambda d, cur: ["A"], df.index, capital=10_000, slots=1, costs=ZERO)
    first = res.trades[0]
    assert first.entry_date == pd.Timestamp("2023-02-01") and first.entry_px == 105.0


def test_cash_earns_yield_when_out_of_market():
    df = daily([100.0] * 300)
    res = simulate_rotation({"A": df}, lambda d, cur: [], df.index, capital=100_000, slots=1,
                            costs=ZERO, cash_yield_pct=6.0)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    assert abs(res.equity.iloc[-1] - 100_000 * 1.06 ** yrs) < 1


def test_momentum_ranks_stronger_trend_higher_and_filters_downtrends():
    up_fast = daily(100 * np.exp(np.linspace(0, 0.8, 300)))
    up_slow = daily(100 * np.exp(np.linspace(0, 0.3, 300)))
    down = daily(100 * np.exp(np.linspace(0, -0.5, 300)))
    rng = np.random.default_rng(0)
    for df in (up_fast, up_slow, down):                      # add noise so volatility is non-zero
        df["close"] *= np.exp(rng.normal(0, 0.005, len(df)))
    d = up_fast.index[-1]
    s = momentum_scores({"FAST": up_fast, "SLOW": up_slow, "DOWN": down}, d)
    assert list(s.index) == ["FAST", "SLOW"]                 # DOWN fails the trend template


def test_market_filter_and_index_trend():
    up = daily(np.linspace(100, 200, 260))
    down = daily(np.linspace(200, 100, 260))
    assert market_ok(up, up.index[-1]) and not market_ok(down, down.index[-1])
    res = index_trend("NIFTYBEES", down, down.index, 100_000, ZERO, 0.0)
    assert len(res.trades) <= 1                              # mostly in cash on a falling index


# ---------- intraday ----------
def session(day, bars):
    """bars: list of (o, h, l, c, v) for 09:15, 09:20, ..."""
    idx = [pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=15 + 5 * i) for i in range(len(bars))]
    return pd.DataFrame(bars, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


def history(n_days=16, last_day_bars=None, base_vol=1000):
    days = pd.bdate_range("2026-01-01", periods=n_days)
    parts = []
    for d in days[:-1]:
        parts.append(session(d, [(100, 100.2, 99.8, 100, base_vol)] + [(100, 100.1, 99.9, 100, 500)] * 3))
    parts.append(session(days[-1], last_day_bars))
    intra = pd.concat(parts)
    dly = pd.DataFrame({"open": 100, "high": 102, "low": 98, "close": 100, "volume": 1e5},
                       index=pd.DatetimeIndex(days))                              # ATR = 4
    return intra, dly, days[-1]


def test_opening_relvol_uses_prior_sessions_only():
    intra, _, last = history(last_day_bars=[(100, 101, 99.9, 100.8, 3000), (100.8, 101, 100.5, 100.9, 500)])
    st = opening_stats(intra)
    assert abs(st.loc[last, "relvol"] - 3.0) < 1e-9
    assert np.isnan(st["relvol"].iloc[0])


def test_long_breakout_entry_stop_and_close_exit():
    bars = [(100, 101, 99.9, 100.8, 3000),            # bullish opening candle, range high 101
            (100.9, 100.95, 100.7, 100.9, 500),       # no breakout
            (100.9, 101.5, 100.9, 101.4, 500),        # breaks 101 -> long at 101
            (101.4, 102.0, 101.2, 101.9, 500)]
    intra, dly, last = history(last_day_bars=bars)
    eq, trades, _ = simulate_orb({"A": intra}, {"A": dly}, capital=100_000, max_positions=1, costs=IZERO,
                                 stop_atr_frac=0.1, exit_time="15:15", start=last)
    t = trades[0]
    assert t.side == "long" and t.entry_px == 101 and t.reason == "close" and t.exit_px == 101.9
    assert t.entry_time == last + pd.Timedelta(hours=9, minutes=25)


def test_short_stop_and_worst_case_on_entry_bar():
    bars = [(100, 100.1, 99, 99.2, 3000),             # bearish opening candle, range low 99
            (99.1, 99.5, 98.8, 99.0, 500)]            # trades through 99 AND above stop 99.4 in the same bar
    intra, dly, last = history(last_day_bars=bars)
    _, trades, _ = simulate_orb({"A": intra}, {"A": dly}, capital=100_000, max_positions=1, costs=IZERO,
                                stop_atr_frac=0.1, start=last)
    t = trades[0]
    assert t.side == "short" and t.entry_px == 99 and t.reason == "stop" and abs(t.exit_px - 99.4) < 1e-9
    assert t.pnl < 0


def test_low_relvol_or_flat_candle_is_skipped():
    flat = [(100, 101, 99, 100, 3000), (100, 102, 98, 101, 500)]
    intra, dly, last = history(last_day_bars=flat)
    assert simulate_orb({"A": intra}, {"A": dly}, costs=IZERO, start=last)[1] == []
    quiet = [(100, 101, 99.9, 100.8, 900), (100.9, 101.5, 100.9, 101.4, 500)]
    intra, dly, last = history(last_day_bars=quiet)
    assert simulate_orb({"A": intra}, {"A": dly}, costs=IZERO, start=last)[1] == []


def test_intraday_costs():
    c = IntradayCosts()
    v = 100_000
    assert abs(c.sell(v) - (10 + v * 0.0000307 + v * 0.00025 + (10 + v * 0.0000307) * 0.18)) < 0.01
    assert abs(c.buy(v) - (10 + v * 0.0000307 + v * 0.00003 + (10 + v * 0.0000307) * 0.18)) < 0.01
