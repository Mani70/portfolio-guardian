"""Daily swing insights (trader/insights.py): information only, on synthetic prices."""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from trader import insights as I

N = 300


def _panel(market_up=True, extra=None):
    """Stocks over N sessions: UP (strong uptrend, near its high), DOWN (falling to its low), FLAT x40 (filler so
    momentum percentiles mean something), and NIFTYBEES (the market)."""
    idx = pd.bdate_range("2025-06-02", periods=N)
    rng = np.random.default_rng(0)
    cols = {}
    t = np.arange(N)
    cols["UP"] = 1000 * np.exp(0.003 * t + rng.normal(0, 0.004, N).cumsum() * 0.1)
    cols["DOWN"] = 1000 * np.exp(-0.003 * t + rng.normal(0, 0.004, N).cumsum() * 0.1)
    for k in range(40):
        cols[f"FLAT{k}"] = 1000 * np.exp(rng.normal(0, 0.01, N).cumsum() * 0.2 + 0.0002 * k * t)
    cols["NIFTYBEES"] = 100 * np.exp((0.001 if market_up else -0.001) * t)
    cols.update(extra or {})
    close = pd.DataFrame(cols, index=idx)
    panel = {"close": close, "open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
             "volume": pd.DataFrame(1e6, index=idx, columns=close.columns)}
    panel["value"] = panel["volume"] * close * 10                     # ~Rs 10 cr+ a day: all liquid
    panel["deliv"] = pd.DataFrame(50.0, index=idx, columns=close.columns)
    return panel


def test_strong_stock_is_a_buy_idea_in_an_up_market_with_its_reasons():
    p = _panel(market_up=True)
    rep = I.screen(I.facts(p), I.market(p), {})
    assert "UP" in rep.buys.index and "DOWN" not in rep.buys.index
    assert "DOWN" in rep.avoids.index
    text = "\n".join(I.compose(rep, {}))
    assert "🟢 UP" in text and "Trend: above its 50-day" in text and "idea is wrong below" in text
    assert "🔴 DOWN" in text and "What would change the view" in text
    assert "information only" in text and "Filings (30 days): none on NSE." in text


def test_down_market_gives_no_buy_but_a_labelled_watchlist_and_the_reason():
    p = _panel(market_up=False)
    rep = I.screen(I.facts(p), I.market(p), {})
    assert rep.buys.empty and "UP" in rep.watch.index
    text = "\n".join(I.compose(rep, {}))
    assert "No BUY idea today" in text and "market itself is in a downtrend" in text
    assert "WATCHLIST" in text and "👀 UP" in text


def test_nothing_qualifying_is_explained_by_the_funnel():
    p = _panel(market_up=True)
    p["value"] = p["value"] / 1000                                    # nothing liquid
    rep = I.screen(I.facts(p), I.market(p), {})
    text = "\n".join(I.compose(rep, {}))
    assert rep.buys.empty and rep.avoids.empty
    assert "• liquid (≥ ₹10 cr a day" in text and "no stock passed 'liquid" in text
    assert "No AVOID idea today" in text


def test_unchecked_filings_are_said_to_be_unchecked_never_none():
    p = _panel(market_up=True)
    rep = I.screen(I.facts(p), I.market(p), None)
    text = "\n".join(I.compose(rep, None))
    assert "NOT checked today" in text and "none on NSE" not in text


def test_red_flag_filing_blocks_a_buy_and_weak_holdings_are_flagged():
    p = _panel(market_up=True)
    flags = {"UP": [{"date": "2026-10-01", "title": "Resignation of statutory auditor", "red": True}]}
    rep = I.screen(I.facts(p), I.market(p), flags, holdings=["DOWN", "FLAT3"])
    assert "UP" not in rep.buys.index
    assert list(rep.holdings_weak.index) == ["DOWN"]
    text = "\n".join(I.compose(rep, flags))
    assert "YOU HOLD THIS" in text


def test_messages_fit_telegram():
    parts = I._split(["x" * 9000, "short"])
    assert all(len(m) <= 3800 for m in parts) and parts[-1] == "short"


def test_bonus_adjusts_earlier_prices():
    p = _panel()
    c = p["close"]
    ex = c.index[200]
    for k in ("open", "high", "low", "close"):
        p[k].loc[c.index >= ex, "UP"] /= 2                              # a 1:1 bonus halves the price
    ca = pd.DataFrame([{"series": "EQ", "symbol": "UP", "ex_date": f"{ex:%Y-%m-%d}", "purpose": "BONUS 1:1"}])
    assert I.adjust(p, ca) == 1
    jump = p["close"]["UP"].pct_change().abs().max()
    assert jump < 0.1                                                  # no fake -50% day left


def test_track_record_and_once_a_day(tmp_path):
    p = _panel()
    rep = I.screen(I.facts(p), I.market(p), {})
    old = I.Report(p["close"].index[-30], rep.market, rep.buys, rep.avoids, [], [], 0, 0)
    I.record(old, tmp_path / "ideas.csv")
    line = I.track_record(p, tmp_path / "ideas.csv")
    assert line.startswith("Track record:") and "BUY ideas (" in line and "vs Nifty ETF" in line
    (tmp_path / "state.json").write_text('{"sent": "2026-10-08"}')
    assert I.run(print, today=date(2026, 10, 8), update=False, store=tmp_path) == "insights: already sent today"
