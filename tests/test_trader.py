"""Trading engine tests: paper fills, risk checks, gates, the live INDstocks broker against a fake server,
and end-to-end engine runs on synthetic history. No network, no real account, no real orders."""
import json
import threading
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

from trader import config as C
from trader.brokers.base import CostModel, Fill
from trader.brokers.paper import PaperBroker
from trader.engine import Engine
from trader.gates import check_live_drawdown, is_demoted, paper_gate, stats
from trader.instruments import Instruments, _tick, round_tick
from trader.journal import Journal
from trader.market import ReplayMarket
from trader.models import BUY, CANCELLED, CNC, FILLED, INTRADAY, OPEN, REJECTED, SELL, UNKNOWN, Order, OrderRequest, Position, Signal
from trader.risk import Risk
from trader.strategies.gap_reversal import GapReversal
from trader.strategies.gtaa import TrendAllocation
from backtest.engine import Costs
from backtest.intraday import IntradayCosts

ZERO = CostModel(Costs(brokerage_per_order=0, stt_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0, gst_pct=0,
                       dp_per_sell=0, slippage_pct=0),
                 IntradayCosts(brokerage_per_order=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0, stamp_buy_pct=0,
                               gst_pct=0, slippage_pct=0))


def cfg(**over):
    c = C._merge(C.DEFAULTS, over)
    c["_path"] = "test"
    return c


def bars_day(d, closes, open_=None, lows=None, highs=None, vol=1000):
    idx = [datetime.combine(d, time(9, 15)) + timedelta(minutes=5 * i) for i in range(len(closes))]
    c = np.asarray(closes, float)
    o = np.r_[open_ if open_ is not None else c[0], c[:-1]]
    lo = np.minimum(o, c) * 0.999 if lows is None else np.asarray(lows, float)
    hi = np.maximum(o, c) * 1.001 if highs is None else np.asarray(highs, float)
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": vol}, index=pd.DatetimeIndex(idx))


def req(tag="T1", side=BUY, qty=10, limit=101.0, product=INTRADAY, kind="entry", stop=None, target=None,
        created=datetime(2026, 1, 5, 10, 0, 15), amo=False, symbol="AAA"):
    return OrderRequest(tag, "s", symbol, "1", side, qty, product, limit, "sig", kind, created, stop, target, 100.0, amo)


# ---------------- instruments ----------------
def test_tick_units_and_rounding():
    assert _tick("5") == 0.05 and _tick("0.05") == 0.05 and _tick("") == 0.05 and _tick("10.0000") == 0.10
    assert round_tick(100.03, 0.05, "up") == 100.05 and round_tick(100.03, 0.05, "down") == 100.0
    inst = Instruments.from_rows([{"EXCH": "NSE", "SECURITY_ID": "2885", "TRADING_SYMBOL": "RELIANCE-EQ",
                                   "SYMBOL_NAME": "RELIANCE", "SERIES": "EQ", "EXPIRY_CODE": "0", "TICK_SIZE": "10"}])
    assert inst.security_id("RELIANCE") == "2885" and inst.tick("RELIANCE") == 0.10


# ---------------- paper broker ----------------
def test_paper_marketable_fill_resting_fill_and_stop_leg():
    d = date(2026, 1, 5)
    intra = {"AAA": bars_day(d, [100] * 9 + [99, 98, 97, 96, 95, 94])}
    m = ReplayMarket({}, intra)
    pb = PaperBroker(m, ZERO)
    now = datetime(2026, 1, 5, 9, 30, 15)                      # bars 09:15/20/25 complete, ltp 100
    o = pb.place(Order(req(created=now, stop=97.5)), now)
    fills = pb.update([o], now)
    assert o.status == FILLED and fills[0].price == 100 and fills[0].leg == "main"
    later = datetime(2026, 1, 5, 10, 30, 15)
    legs = pb.update([], later)
    assert legs and legs[0].leg == "stop" and legs[0].side == SELL and legs[0].price == 97.5
    rest = pb.place(Order(req("T2", limit=95.5, created=now)), now)
    assert pb.update([rest], now) == [] and rest.status == OPEN    # 100 > limit: waits
    assert pb.update([rest], later)[0].price == 95.5


def test_paper_amo_fills_at_next_open_or_not_at_all():
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    intra = {"AAA": pd.concat([bars_day(d1, [100] * 75), bars_day(d2, [104] * 75, open_=104)])}
    m = ReplayMarket({}, intra)
    pb = PaperBroker(m, ZERO)
    t = datetime(2026, 1, 5, 15, 45)
    ok = pb.place(Order(req("A1", product=CNC, limit=102, amo=True, created=t)), t)
    gap = pb.place(Order(req("A2", product=CNC, limit=106, amo=True, created=t)), t)
    assert pb.update([ok, gap], datetime(2026, 1, 5, 18, 0)) == []          # not before the next session
    fills = pb.update([ok, gap], datetime(2026, 1, 6, 9, 30))
    assert [f.tag for f in fills] == ["A2"] and fills[0].price == 104         # A1's limit 102 < open 104
    assert ok.status == OPEN


# ---------------- risk ----------------
def test_risk_checks(tmp_path):
    j = Journal(tmp_path / "j.db", "paper")
    c = cfg()
    r = Risk(c, j, tmp_path)
    now = datetime(2026, 1, 5, 10, 0)
    good = req(stop=99.0)
    assert r.check(good, now, 100.6, [])[0]
    assert not r.check(req(stop=None), now, 100.6, [])[0]                      # intraday needs a stop
    assert not r.check(req(stop=102), now, 100.6, [])[0]                       # wrong side
    assert not r.check(req(stop=99), datetime(2026, 1, 5, 14, 45), 100.6, [])[0]   # after last entry
    assert not r.check(req(stop=99, qty=10_000), now, 100.6, [])[0]            # above max order value
    assert not r.check(req(stop=99, limit=110), now, 100.0, [])[0]             # limit too far from price
    (tmp_path / "STOP").write_text("")
    assert not r.check(good, now, 100.6, [])[0]                                # kill switch blocks entries
    held = [Position("s", "AAA", INTRADAY, 10, 100.0, now, "E0")]
    assert r.check(req("X", side=SELL, kind="exit"), now, 100.6, held)[0]      # ...but never exits
    assert not r.check(req("X2", side=SELL, kind="exit", qty=11), now, 100.6, held)[0]   # never more than held
    (tmp_path / "STOP").unlink()
    j.save_order(Order(good))
    assert "duplicate" in r.check(good, now, 100.6, [])[1]


def test_daily_loss_limit_blocks_new_entries(tmp_path):
    j = Journal(tmp_path / "j.db", "paper")
    r = Risk(cfg(), j, tmp_path)
    p = Position("s", "AAA", INTRADAY, 100, 100.0, datetime(2026, 1, 5, 9, 30), "E1")
    j.record_trade(p, 100, 80.0, datetime(2026, 1, 5, 11, 0), 0.0, "X1", "stop")   # -2,000 = 2% of 1 lakh
    ok, why = r.check(req(stop=99), datetime(2026, 1, 5, 12, 0), 100.6, [])
    assert not ok and "loss limit" in why


def test_intraday_sizing_respects_risk_and_value_caps(tmp_path):
    r = Risk(cfg(), Journal(tmp_path / "j.db", "paper"), tmp_path)
    assert r.size_intraday("s", 100.0, 99.0) == 400          # 40% of 1 lakh = ₹40k cap -> 400 shares
    assert r.size_intraday("s", 100.0, 99.9) == 400
    assert r.size_intraday("s", 100.0, 95.0) == 100          # 0.5% risk = ₹500 / ₹5 per share


# ---------------- gates ----------------
def _trades(n, day0=date(2026, 1, 1), pnl=100.0, spread=50.0):
    rng = np.random.default_rng(1)
    rows = []
    for i in range(n):
        d = day0 + timedelta(days=i)
        rows.append(dict(entry_time=f"{d}T09:30:00", exit_time=f"{d}T15:15:00", net=pnl + rng.normal(0, spread)))
    return pd.DataFrame(rows)


def test_gate_stats_and_pass_fail(tmp_path):
    s = stats(_trades(80))
    assert s["trades"] == 80 and s["t"] > 2
    j = Journal(tmp_path / "j.db", "paper")
    for i in range(70):
        d = datetime(2026, 1, 1) + timedelta(days=i)
        p = Position("gx", "AAA", INTRADAY, 10, 100.0, d, f"E{i}")
        j.record_trade(p, 10, 100.0 + (12 if i % 4 else -10) / 10, d + timedelta(hours=5), 0.0, f"X{i}", "exit")
    ok, why, st = paper_gate(tmp_path / "j.db", "gx", "intraday", cfg())
    assert ok, why
    ok, why, _ = paper_gate(tmp_path / "j.db", "nobody", "intraday", cfg())
    assert not ok and "paper trades" in why


def test_live_drawdown_demotes(tmp_path):
    j = Journal(tmp_path / "j.db", "live")
    p = Position("lv", "AAA", CNC, 100, 100.0, datetime(2026, 1, 1), "E")
    j.record_trade(p, 100, 0.0, datetime(2026, 1, 2), 0.0, "X", "exit")       # lose ₹10,000
    hit, pct = check_live_drawdown(j, "lv", 100_000, 8, tmp_path)
    assert hit and pct >= 10 and is_demoted(tmp_path, "lv")


# ---------------- live broker against a fake INDstocks ----------------
class FakeINDstocks(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_POST(self):
        st = FakeINDstocks.state
        body = self._body()
        st.setdefault("posts", []).append((self.path, body))
        st.setdefault("log", []).append(self.path)
        if st.get("mode") == "drop" and self.path in ("/order", "/smart/order"):
            st.setdefault("book", []).append({"id": "EQ-9", "remarks": body["remarks"], "status": "INITIATED",
                                              "txn_type": body["txn_type"], "traded_qty": 0})
            self.connection.close()                                       # reply never arrives
            return
        if st.get("mode") == "reject":
            return self._send(400, {"status": "error", "error_type": "OrderException",
                                    "message": st.get("reject_msg", "RMS: Margin exceeds")})
        if self.path == "/order":
            return self._send(200, {"status": "success", "data": {"order_id": "EQ-1", "order_status": "INITIATED"}})
        if self.path == "/smart/order":
            return self._send(200, {"status": "success", "data": {"order_data": [
                {"order_id": "EQ-2", "order_status": "CREATED", "child_order_details": {"order_id": "GTT-3"}}]}})
        if self.path.endswith("/cancel"):
            if st.get("cancel_updates_book"):
                for row in st.get("book", []):
                    if row.get("id") == body["order_id"]:
                        row["status"] = "CANCELLED"
            if st.get("cancel_fills"):
                for row in st.get("book", []):
                    if row.get("id") == body["order_id"]:
                        row.update(status="SUCCESS", traded_qty=1, traded_price=97.0)
            return self._send(200, {"status": "success", "data": {"order_id": body["order_id"], "order_status": "CANCELLED"}})
        self._send(404, {"status": "error"})

    def do_GET(self):
        st = FakeINDstocks.state
        st.setdefault("log", []).append(self.path)
        if self.path.startswith("/order-book") and st.get("book_error"):
            return self._send(503, {"status": "error", "message": "upstream unavailable"})
        if self.path.startswith("/order-book"):
            return self._send(200, {"status": "success", "data": st.get("book", [])})
        if self.path.startswith("/funds"):
            return self._send(200, {"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 5000, "eq_mis": 7000}}})
        self._send(404, {"status": "error"})


def _live(monkeypatch):
    import guardian.broker as gb
    import trader.brokers.indstocks as ib
    srv = HTTPServer(("127.0.0.1", 0), FakeINDstocks)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(ib, "BASE_URL", base)
    monkeypatch.setattr(gb, "BASE_URL", base)
    client = gb.IndStocksClient(access_token="tok", timeout=2)
    return ib.IndStocksBroker(client, ZERO, rate_per_sec=100), srv


def test_live_order_payload_and_smart_legs(monkeypatch):
    FakeINDstocks.state = {}
    b, srv = _live(monkeypatch)
    try:
        now = datetime(2026, 1, 5, 10, 0)
        o = b.place(Order(req("tagA", product=CNC, amo=True, limit=101.05)), now)
        path, body = FakeINDstocks.state["posts"][-1]
        assert path == "/order" and o.broker_id == "EQ-1" and o.status == OPEN
        assert body["algo_id"] == "99999" and body["order_type"] == "LIMIT" and body["remarks"] == "tagA"
        assert body["is_amo"] is True and body["segment"] == "EQUITY" and body["product"] == "CNC"
        o2 = b.place(Order(req("tagB", stop=97.33, target=None)), now, tick=0.05)
        path, body = FakeINDstocks.state["posts"][-1]
        assert path == "/smart/order" and o2.child_id == "GTT-3"
        assert body["sl_trigger_price"] == 97.3 and body["sl_limit_price"] < body["sl_trigger_price"]
        assert "tgt_trigger_price" not in body and "is_amo" not in body
        assert b.available_funds(CNC) == 5000 and b.available_funds(INTRADAY) == 7000
    finally:
        srv.shutdown()


def test_live_rejection_and_lost_reply_are_never_resent(monkeypatch):
    FakeINDstocks.state = {"mode": "reject"}
    b, srv = _live(monkeypatch)
    try:
        now = datetime(2026, 1, 5, 10, 0)
        o = b.place(Order(req("tagR")), now)
        assert o.status == REJECTED and "Margin" in o.message
        FakeINDstocks.state = {"mode": "drop"}
        o = b.place(Order(req("tagD", product=CNC)), now)
        posts = [p for p in FakeINDstocks.state["posts"] if p[0] == "/order"]
        assert len(posts) == 1                                     # exactly one placement attempt
        assert o.broker_id == "EQ-9" and o.status == OPEN and "adopted" in o.message
    finally:
        srv.shutdown()


def test_live_fills_and_stop_leg_from_order_book(monkeypatch):
    FakeINDstocks.state = {}
    b, srv = _live(monkeypatch)
    try:
        now = datetime(2026, 1, 5, 10, 0)
        o = Order(req("tagF", qty=10, stop=97.0), status=OPEN, broker_id="EQ-2", child_id="GTT-3")
        FakeINDstocks.state["book"] = [
            {"id": "EQ-2", "remarks": "tagF", "status": "SUCCESS", "txn_type": "BUY", "traded_qty": 10, "traded_price": "100.5"},
            {"id": "GTT-3", "remarks": "tagF", "status": "SUCCESS", "txn_type": "SELL", "traded_qty": 0, "traded_price": ""},
            {"id": "EQ-7", "remarks": "tagF", "status": "SUCCESS", "txn_type": "SELL", "traded_qty": 10, "traded_price": "97.0"}]
        fills = b.update([o], now)
        assert [(f.leg, f.qty, f.price) for f in fills] == [("main", 10, 100.5), ("stop", 10, 97.0)]
        assert o.status == FILLED and b.update([o], now) == []        # nothing reported twice
    finally:
        srv.shutdown()


# ---------------- engine end to end on synthetic history ----------------
def _etf_daily(n=320, start="2025-01-01", up=True, seed=0):
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    drift = 0.002 if up else -0.002
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.003, n)))
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1e5}, index=idx)


def test_engine_swing_trend_allocation_end_to_end(tmp_path):
    daily = {"UPA": _etf_daily(up=True, seed=1), "DOWNB": _etf_daily(up=False, seed=2), "NIFTYBEES": _etf_daily(seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": 100_000, "intraday": 0},
            strategies={"trend_allocation": {"enabled": True, "params": {"assets": ["UPA", "DOWNB"], "sma_days": 200}}})
    strat = TrendAllocation("trend_allocation", {"assets": ["UPA", "DOWNB"], "sma_days": 200})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "paper")
    eng = Engine(c, j, PaperBroker(m, ZERO), m, None, None, [strat], tmp_path, [], "NIFTYBEES", quiet=True)
    days = [d for d in m.sessions if d >= date(2025, 11, 1)]
    for d in days[:45]:
        eng.swing_check(datetime.combine(d, time(9, 30)))
        eng.swing_plan(datetime.combine(d, time(15, 45)))
    pos = {p.symbol: p for p in j.positions()}
    assert set(pos) == {"UPA"}                                     # only the uptrending ETF is held
    assert 45_000 < pos["UPA"].qty * pos["UPA"].avg_price <= 50_100   # half the capital (2 slots)
    n_orders = len(j.load_orders())
    eng.swing_plan(datetime.combine(days[44], time(15, 45)))       # re-running the same evening adds nothing
    assert len(j.load_orders()) == n_orders


def test_engine_intraday_gap_reversal_stop_and_square_off(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 1, 6)
    syms = [f"S{i}" for i in range(6)]
    daily = {s: pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e5},
                             index=pd.bdate_range(end=d0, periods=30)) for s in syms}
    intra = {}
    for s in syms:
        if s == "S0":       # gaps down 4%, recovers: exits at 15:15
            day = bars_day(d1, [96.2] + list(np.linspace(96.2, 98.0, 74)), open_=96.0)
        elif s == "S1":     # gaps down 5%, keeps falling: stopped out
            day = bars_day(d1, [95.2] + list(np.linspace(95.0, 90.0, 74)), open_=95.0)
        else:
            day = bars_day(d1, [100.0] * 75, open_=100.0)
        intra[s] = day
    m = ReplayMarket(daily, intra)
    c = cfg(intraday={"first_entry": "09:20"},
            strategies={"gap_reversal": {"enabled": True, "min_strength": 0.03}})
    strat = GapReversal("gap_reversal", {"min_gap": 0.03, "stop_atr": 1.0}, 0.03)
    j = Journal(tmp_path / "j.db", "paper")
    eng = Engine(c, j, PaperBroker(m, ZERO), m, None, None, [strat], tmp_path, syms, "NIFTYBEES", quiet=True)
    eng.intraday_start(d1, datetime.combine(d1, time(9, 10)))
    for k in range(1, 75):
        eng.intraday_step(datetime.combine(d1, time(9, 15)) + timedelta(minutes=5 * k, seconds=15))
    t = j.trades().set_index("symbol")
    assert set(t.index) == {"S0", "S1"}
    assert t.loc["S1", "reason"] == "stop" and t.loc["S1", "net"] < 0
    assert t.loc["S0", "reason"] == "exit" and t.loc["S0", "net"] > 0
    assert j.positions(product=INTRADAY) == []


def test_order_count_gate_for_monthly_strategies(tmp_path):
    j = Journal(tmp_path / "j.db", "paper")
    for i, day in enumerate([datetime(2026, 1, 30, 15, 45), datetime(2026, 3, 31, 15, 45)]):
        o = Order(OrderRequest(f"t{i}", "ta", "GOLDBEES", "1", BUY, 10, CNC, 60.0, "s", "entry", day, amo=True),
                  status=FILLED)
        j.save_order(o)
    c = cfg(strategies={"ta": {"gate": {"count": "orders", "min_trades": 2, "min_days": 60, "min_t": None,
                                        "require_profit": False}}})
    ok, why, s = paper_gate(tmp_path / "j.db", "ta", "swing", c)
    assert ok, why
    c["strategies"]["ta"]["gate"]["min_trades"] = 3
    assert "filled paper orders" in paper_gate(tmp_path / "j.db", "ta", "swing", c)[1]


def test_live_market_drops_unfinished_bar_and_maps_symbols():
    from zoneinfo import ZoneInfo
    from guardian.broker import Candle
    from trader.market import LiveMarket
    ist = ZoneInfo("Asia/Kolkata")

    class FakeClient:
        calls = 0

        def candles_many(self, codes, interval, start_ms, end_ms):
            FakeClient.calls += 1
            assert interval == "5minute" and codes == ["NSE_11"]
            t0 = datetime(2026, 1, 5, 9, 15, tzinfo=ist).timestamp()
            return {"NSE_11": [Candle(int(t0 + 300 * k), 100 + k, 101 + k, 99 + k, 100.5 + k, 1000) for k in range(4)]}

        def ltp(self, codes):
            return {"NSE_11": 123.0}

    inst = Instruments({"AAA": ("11", 0.05)})
    m = LiveMarket(FakeClient(), inst, Path("."))
    now = datetime(2026, 1, 5, 9, 33, 0)                  # bars 09:15, 09:20, 09:25 done; 09:30 still forming
    b = m.today_bars(["AAA"], now)["AAA"]
    assert list(b.index.strftime("%H:%M")) == ["09:15", "09:20", "09:25"]
    m.today_bars(["AAA"], now + timedelta(seconds=30))     # same 5-minute slot: served from memory
    assert FakeClient.calls == 1
    assert m.ltp(["AAA", "UNKNOWN"], now) == {"AAA": 123.0}


# ---------------- audit regressions ----------------
class FakeLive:
    """A scripted live broker: records every order sent and lets a test decide what happens."""
    name, live = "fake-live", True

    def __init__(self):
        self.sent, self.cancels, self.fills = [], [], []
        self.cancel_child_ok = True
        self.place_status = OPEN

    def place(self, order, now, tick=0.05):
        self.sent.append(order.req)
        order.status = self.place_status
        order.broker_id = "" if self.place_status == UNKNOWN else f"EQ-{len(self.sent)}"
        if order.req.stop is not None:
            order.child_id = f"GTT-{len(self.sent)}"
        return order

    def cancel(self, order, now):
        self.cancels.append(order.req.tag)
        from trader.models import CANCEL_SENT
        order.status = CANCEL_SENT
        return order

    def cancel_child(self, order, now):
        return self.cancel_child_ok

    def update(self, orders, now):
        out, self.fills = self.fills, []
        return out

    def available_funds(self, product):
        return None


def _live_engine(tmp_path, market, strategies=(), c=None):
    c = c or cfg()
    j = Journal(tmp_path / "j.db", "live")
    b = FakeLive()
    inst = Instruments({"AAA": ("1", 0.05)})
    eng = Engine(c, j, b, market, inst, None, list(strategies), tmp_path, ["AAA"], quiet=True)
    return eng, j, b


def test_paper_and_live_share_one_journal_without_blocking_each_other(tmp_path):
    jp, jl = Journal(tmp_path / "j.db", "paper"), Journal(tmp_path / "j.db", "live")
    o = Order(req("SAME"))
    jp.save_order(o)
    assert jp.order_exists("SAME") and not jl.order_exists("SAME")
    jl.save_order(Order(req("SAME")))
    assert len(jp.load_orders()) == 1 and len(jl.load_orders()) == 1


def test_no_exit_while_stop_leg_cancel_unconfirmed_or_reply_lost(tmp_path):
    d = date(2026, 1, 5)
    m = ReplayMarket({}, {"AAA": bars_day(d, [100.0] * 75)})
    eng, j, b = _live_engine(tmp_path, m)
    now = datetime(2026, 1, 5, 15, 15, 15)
    entry = Order(req("E1", qty=250, stop=97.0, created=datetime(2026, 1, 5, 9, 30)), status=FILLED,
                  broker_id="EQ-0", child_id="GTT-0", filled_qty=250, avg_price=100.0)
    j.save_order(entry)
    j.save_position(Position("s", "AAA", INTRADAY, 250, 100.0, datetime(2026, 1, 5, 9, 30), "E1", stop=97.0,
                             child_id="GTT-0"))
    b.cancel_child_ok = False                      # the stop leg may be executing at the broker
    eng.square_off(now)
    assert b.sent == []                            # no exit of our own: it could double-sell into a short
    b.cancel_child_ok = True
    b.place_status = UNKNOWN                       # our exit's reply gets lost
    eng.square_off(now + timedelta(minutes=1))
    eng.square_off(now + timedelta(minutes=2))
    eng.square_off(now + timedelta(minutes=3))
    exits = [r for r in b.sent if r.kind == "exit"]
    assert len(exits) == 1 and exits[0].qty == 250   # one exit, never resent while its fate is unknown


def test_working_entries_count_toward_limits_and_restarts_do_not_resend(tmp_path):
    j = Journal(tmp_path / "j.db", "paper")
    r = Risk(cfg(), j, tmp_path)
    now = datetime(2026, 1, 5, 10, 0)
    for i, sym in enumerate(["A1", "A2", "A3"]):
        j.save_order(Order(req(f"W{i}", symbol=sym, stop=99.0, qty=10), status=OPEN))
    ok, why = r.check(req("W9", symbol="A4", stop=99.0, qty=10), now, 100.6, [])
    assert not ok and "max intraday positions" in why
    j2 = Journal(tmp_path / "k.db", "paper")
    r2 = Risk(cfg(), j2, tmp_path)
    j2.save_order(Order(req("first", symbol="A1", stop=99.0), status=CANCELLED))
    ok, why = r2.check(req("after-restart", symbol="A1", stop=99.0), now, 100.6, [])
    assert not ok and "already entered" in why


def test_exits_pass_even_after_the_daily_order_limit(tmp_path):
    j = Journal(tmp_path / "j.db", "paper")
    r = Risk(cfg(limits={"max_orders_per_day": 1}), j, tmp_path)
    now = datetime(2026, 1, 5, 10, 0)
    j.save_order(Order(req("O1", symbol="ZZZ", stop=99.0), status=FILLED))
    held = [Position("s", "AAA", INTRADAY, 10, 100.0, now, "E0")]
    assert not r.check(req("N1", stop=99.0, symbol="BBB"), now, 100.6, held)[0]
    assert r.check(req("X1", side=SELL, kind="exit"), now, 100.6, held)[0]


def test_swing_check_waits_for_confirmed_cancel_before_repricing(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 1, 6)
    daily = {"AAA": pd.DataFrame({"open": [100.0, 101.0], "high": 102.0, "low": 99.0, "close": [100.0, 101.0],
                                  "volume": 1e5}, index=pd.DatetimeIndex([d0, d1]))}
    m = ReplayMarket(daily, {})
    eng, j, b = _live_engine(tmp_path, m, [TrendAllocation("ta", {"assets": ["AAA"]})])
    amo = Order(req("AMO1", product=CNC, amo=True, limit=102.0, created=datetime(2026, 1, 5, 15, 45)),
                status=OPEN, broker_id="EQ-77")
    j.save_order(amo)
    eng.settle = lambda now, tries=4: eng.sync(now)    # no sleeping in tests
    eng.swing_check(datetime(2026, 1, 6, 9, 30))
    assert b.cancels == ["AMO1"] and b.sent == []      # cancel not confirmed -> no second BUY
    assert j.order("AMO1").status == "CANCEL_SENT"


def test_live_lost_reply_adopts_main_order_not_stop_leg(monkeypatch):
    FakeINDstocks.state = {}
    b, srv = _live(monkeypatch)
    try:
        o = Order(req("tagL", stop=97.0))
        book = [{"id": "GTT-5", "remarks": "tagL", "txn_type": "SELL", "status": "CREATED", "traded_qty": 0},
                {"id": "EQ-4", "remarks": "tagL", "txn_type": "BUY", "status": "INITIATED", "traded_qty": 0}]
        assert b._match(o, book)
        assert o.broker_id == "EQ-4" and o.child_id == "GTT-5"
    finally:
        srv.shutdown()


def test_live_leg_not_double_counted_when_rows_change(monkeypatch):
    FakeINDstocks.state = {}
    b, srv = _live(monkeypatch)
    try:
        now = datetime(2026, 1, 5, 10, 0)
        o = Order(req("tagG", qty=10, stop=97.0), status=FILLED, broker_id="EQ-2", child_id="GTT-3",
                  filled_qty=10, avg_price=100.0)
        main = {"id": "EQ-2", "remarks": "tagG", "status": "SUCCESS", "txn_type": "BUY", "traded_qty": 10, "traded_price": "100"}
        FakeINDstocks.state["book"] = [main, {"id": "GTT-3", "remarks": "tagG", "status": "SUCCESS", "txn_type": "SELL",
                                              "traded_qty": 10, "traded_price": "97"}]
        assert [(f.leg, f.qty) for f in b.update([o], now)] == [("stop", 10)]
        FakeINDstocks.state["book"].append({"id": "EQ-8", "remarks": "tagG", "status": "SUCCESS", "txn_type": "SELL",
                                            "traded_qty": 10, "traded_price": "97"})
        assert b.update([o], now) == []                 # same execution, now also shown as its own order
    finally:
        srv.shutdown()


def test_paper_day_orders_expire(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 1, 6)
    m = ReplayMarket({}, {"AAA": pd.concat([bars_day(d0, [100.0] * 75), bars_day(d1, [90.0] * 75, open_=90.0)])})
    pb = PaperBroker(m, ZERO)
    t = datetime(2026, 1, 5, 15, 0, 15)
    o = pb.place(Order(req("D1", limit=95.0, created=t)), t)
    pb.update([o], t)                                   # first look: 100 > 95, rests
    assert pb.update([o], datetime(2026, 1, 6, 9, 30)) == [] and o.status == CANCELLED


def test_carried_over_intraday_position_is_booked_not_traded(tmp_path):
    d0, d1 = date(2026, 1, 5), date(2026, 1, 6)
    daily = {"AAA": pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": [100.0, 98.0],
                                  "volume": 1e5}, index=pd.DatetimeIndex([d0 - timedelta(days=1), d0]))}
    m = ReplayMarket(daily, {})
    eng, j, b = _live_engine(tmp_path, m)
    j.save_position(Position("s", "AAA", INTRADAY, 10, 100.0, datetime(2026, 1, 5, 9, 30), "E1"))
    eng.intraday_start(d1, datetime(2026, 1, 6, 9, 10))
    assert j.positions() == [] and b.sent == []
    t = j.trades()
    assert len(t) == 1 and t.iloc[0]["exit_px"] == 98.0 and "square-off" in t.iloc[0]["reason"]


def test_start_now_enters_mid_month_and_still_rebalances_at_month_end():
    from datetime import date
    from trader.strategies.gtaa import TrendAllocation

    class KV(dict):
        def get(self, k, default=None):
            return dict.get(self, k, default)

        def put(self, k, v):
            self[k] = v

    s = TrendAllocation("trend_allocation", {})
    s.journal = KV()
    assert s.due_period(date(2026, 10, 14)) is None           # replay/default: wait for month-end
    assert s.due_period(date(2026, 10, 5)) is None            # first-week catch-up also needs a history
    s.params["_start_now"] = True                              # the scheduled swing-plan
    assert s.due_period(date(2026, 10, 14)) == "2026-09"       # starts now, labelled as last month
    s._pending_period = "2026-09"
    s.commit(ok=True)
    assert s.due_period(date(2026, 10, 15)) is None            # does not repeat
    assert s.due_period(date(2026, 10, 30)) == "2026-10"       # month-end rebalance still happens
    s._pending_period = "2026-10"
    s.commit(ok=True)
    assert s.due_period(date(2026, 11, 2)) is None
    s2 = TrendAllocation("t2", {"_start_now": True})
    s2.journal = KV()
    assert s2.due_period(date(2026, 10, 30)) == "2026-10"      # first run ON a month-end is just a normal rebalance
    s2.journal = KV()
    s2.params = {"_start_now": True}
    s2._pending_period = None
    s2.commit(ok=False)
    assert s2.state_get("last_period") is None                 # broker trouble: stays open, retried next evening


# ---------------- live order-path test command ----------------
def test_order_path_test_places_far_limit_and_confirms_cancel(monkeypatch):
    from trader.order_test import order_path_test
    FakeINDstocks.state = {"cancel_updates_book": True,
                           "book": [{"id": "EQ-1", "status": "O-PENDING", "txn_type": "BUY", "traded_qty": 0}]}
    b, srv = _live(monkeypatch)
    try:
        ok, lines = order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 5, 9, 45),
                                    wait=lambda s: None, polls=3)
        posts = FakeINDstocks.state["posts"]
        assert ok, lines
        assert [p for p, _ in posts] == ["/order", "/order/cancel"]
        log_ = FakeINDstocks.state["log"]
        assert log_.index("/order-book") < log_.index("/order/cancel") < len(log_) - 1   # seen in book, then confirmed
        body = posts[0][1]
        assert body["qty"] == 1 and body["product"] == "CNC" and body["txn_type"] == "BUY"
        assert body["limit_price"] == 97.0 and body["algo_id"] == "99999" and "is_amo" not in body
        assert body["remarks"].startswith("pathtest-")
        assert "confirmed" in lines[-1]
    finally:
        srv.shutdown()


def test_order_path_test_reports_ip_rejection_and_unconfirmed_cancel(monkeypatch):
    from trader.order_test import order_path_test
    FakeINDstocks.state = {"mode": "reject", "reject_msg": "Request from IP 1.2.3.4 is not whitelisted"}
    b, srv = _live(monkeypatch)
    try:
        ok, lines = order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 5, 9, 45),
                                    wait=lambda s: None, polls=2)
        assert not ok and "REJECTED" in lines[0] and "static IP" in lines[1]
        FakeINDstocks.state = {"book": [{"id": "EQ-1", "status": "O-PENDING", "txn_type": "BUY", "traded_qty": 0}]}
        ok, lines = order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 5, 9, 46),
                                    wait=lambda s: None, polls=2)
        assert not ok and "not confirmed" in lines[-2]          # cancel never confirmed: tell the user
        assert "CANCEL IT BY HAND" in lines[-1] and "EQ-1" in lines[-1] and "₹97.0" in lines[-1]
    finally:
        srv.shutdown()


def test_order_path_test_after_hours_uses_amo(monkeypatch):
    from trader.order_test import order_path_test
    FakeINDstocks.state = {"cancel_updates_book": True,
                           "book": [{"id": "EQ-1", "status": "O-PENDING", "txn_type": "BUY", "traded_qty": 0}]}
    b, srv = _live(monkeypatch)
    try:
        ok, lines = order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 4, 12, 0),
                                    pct=5, wait=lambda s: None, polls=3, amo=True)
        body = FakeINDstocks.state["posts"][0][1]
        assert ok and body["is_amo"] is True and body["limit_price"] == 95.0
        assert "after-market" in lines[0]
        FakeINDstocks.state = {"mode": "reject", "reject_msg": "Order description: market closed"}
        ok, lines = order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 4, 12, 1),
                                    wait=lambda s: None, polls=2, amo=True)
        assert not ok and "after-market orders at this hour" in lines[1]   # 'description' must not read as 'IP'
    finally:
        srv.shutdown()



def test_order_path_test_failure_paths_always_say_what_to_do(monkeypatch):
    from trader.order_test import order_path_test
    b, srv = _live(monkeypatch)
    run = lambda **kw: order_path_test(b, "NIFTYBEES", "10576", 100.0, 0.01, datetime(2026, 10, 5, 9, 45),
                                       wait=lambda s: None, polls=2, **kw)
    try:
        FakeINDstocks.state = {}
        ok, lines = run(pct=0)                                  # would buy at/above the market: refused
        assert not ok and "nothing sent" in lines[0] and "posts" not in FakeINDstocks.state
        FakeINDstocks.state = {"book": []}                      # accepted but never visible in the book
        ok, lines = run()
        assert not ok and "does not show up" in lines[-2] and "CANCEL IT BY HAND" in lines[-1] and "EQ-1" in lines[-1]
        FakeINDstocks.state = {"book_error": True}              # order book down after placement
        ok, lines = run()
        assert not ok and "CANCEL IT BY HAND" in lines[-1]
        FakeINDstocks.state = {"cancel_fills": True,            # executes while the cancel is in flight
                               "book": [{"id": "EQ-1", "status": "OPEN", "txn_type": "BUY", "traded_qty": 0}]}
        ok, lines = run()
        assert "EXECUTED" in lines[-1] and "hold 1 unit" in lines[-1]
    finally:
        srv.shutdown()


def test_test_order_time_window():
    from trader.run import _test_order_window
    d = lambda h, m, day=5: datetime(2026, 10, day, h, m)       # 5 Oct 2026 is a Monday, 4 Oct a Sunday
    assert _test_order_window(d(10, 0)) == (False, None)
    assert _test_order_window(d(9, 0))[1] and _test_order_window(d(15, 40))[1]
    assert _test_order_window(d(16, 5)) == (True, None) and _test_order_window(d(8, 30)) == (True, None)
    assert _test_order_window(d(12, 0, day=4)) == (True, None)  # weekend: after-market order


def test_order_path_test_placement_crash_and_self_cancelled_are_not_passes():
    from trader.order_test import order_path_test

    class Boom:
        def place(self, o, now, tick):
            raise AttributeError("'list' object has no attribute 'get'")   # odd 200 reply after the request went out

    ok, lines = order_path_test(Boom(), "NIFTYBEES", "1", 100.0, 0.01, datetime(2026, 10, 5, 10, 0), wait=lambda s: None)
    assert not ok and "Lost contact" in lines[-2] and "CANCEL IT BY HAND" in lines[-1] and "pathtest-" in lines[-1]

    class SelfCancelled:
        def place(self, o, now, tick):
            o.broker_id, o.status = "EQ-7", "OPEN"
            return o

        def _order_book(self):
            return [{"id": "EQ-7", "status": "EXPIRED"}]

        def update(self, orders, now):
            orders[0].status, orders[0].message = "CANCELLED", "expired by exchange"
            return []

    ok, lines = order_path_test(SelfCancelled(), "NIFTYBEES", "1", 100.0, 0.01, datetime(2026, 10, 5, 10, 0),
                                wait=lambda s: None)
    assert not ok and "cancel was not tested" in lines[-1] and "expired by exchange" in lines[-1]


# ---------------- live: account holds less money than the plan ----------------
class Sink:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)


def test_live_entry_is_cut_to_available_funds_and_a_second_one_waits(tmp_path):
    m = ReplayMarket({}, {})
    j = Journal(tmp_path / "j.db", "live")
    b = FakeLive()
    b.available_funds = lambda product: 5_000.0                   # the broker keeps reporting ₹5,000 free
    inst = Instruments({"AAA": ("1", 0.05), "BBB": ("2", 0.05)})
    eng = Engine(cfg(), j, b, m, inst, Sink(), [], tmp_path, ["AAA", "BBB"], quiet=True)
    now = datetime(2026, 10, 5, 16, 10)
    s1 = Signal("s", "AAA", BUY, "entry", 1.0, 100.0, now, CNC)
    o = eng.submit(s1, 100, 100.0, now, amo=True)                 # plan: ₹10,000
    assert o is not None and o.req.qty == 49 and b.sent[-1].qty == 49
    assert any("buying 49 instead of the planned 100" in t for t in eng.notifier.msgs)
    s2 = Signal("s", "BBB", BUY, "entry", 1.0, 100.0, now, CNC)
    assert eng.submit(s2, 10, 100.0, now, amo=True) is None       # ₹5,000 already committed in this run
    assert eng.last_block.startswith("available funds") and len(b.sent) == 1
    assert any("Order NOT sent: BUY BBB" in t for t in eng.notifier.msgs)
    sell = Signal("s", "AAA", SELL, "exit", 1.0, 100.0, now, CNC)  # exits never need funds
    j.save_position(Position("s", "AAA", CNC, 49, 100.0, now, "E", "1"))
    assert eng.submit(sell, 49, 98.0, now, amo=True) is not None


def test_live_swing_plan_without_money_retries_next_evening(tmp_path):
    daily = {"UPA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": 100_000, "intraday": 0},
            strategies={"trend_allocation": {"enabled": True, "live": True, "override_gate": True,
                                             "params": {"assets": ["UPA"], "sma_days": 200}}})
    strat = TrendAllocation("trend_allocation", {"assets": ["UPA"], "sma_days": 200, "_start_now": True})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    b = FakeLive()
    money = {"free": 50.0}
    b.available_funds = lambda product: money["free"]
    inst = Instruments({"UPA": ("11", 0.01), "NIFTYBEES": ("13", 0.01)})
    eng = Engine(c, j, b, m, inst, Sink(), [strat], tmp_path, [], "NIFTYBEES", quiet=True)
    assert eng.swing_strategies() == [strat]
    days = [d for d in m.sessions if d >= date(2025, 11, 10)]
    eng.swing_plan(datetime.combine(days[0], time(16, 10)))
    assert b.sent == [] and strat.state_get("last_period") is None          # nothing bought, period left open
    assert any("Order NOT sent: BUY UPA" in t for t in eng.notifier.msgs)
    money["free"] = 1_000_000.0                                            # funds arrive
    eng.swing_plan(datetime.combine(days[1], time(16, 10)))
    assert [r.symbol for r in b.sent] == ["UPA"] and strat.state_get("last_period") is not None



def _live_trend(tmp_path, free, capital=100_000):
    daily = {"UPA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": capital, "intraday": 0},
            strategies={"trend_allocation": {"enabled": True, "live": True, "override_gate": True,
                                             "params": {"assets": ["UPA"], "sma_days": 200}}})
    strat = TrendAllocation("trend_allocation", {"assets": ["UPA"], "sma_days": 200, "_start_now": True})
    strat.sessions = m.sessions
    b = FakeLive()
    b.available_funds = lambda product: free
    eng = Engine(c, Journal(tmp_path / "j.db", "live"), b, m, Instruments({"UPA": ("11", 0.01), "NIFTYBEES": ("13", 0.01)}),
                 Sink(), [strat], tmp_path, [], "NIFTYBEES", quiet=False)
    day = [d for d in m.sessions if d >= date(2025, 11, 10)][0]
    return eng, b, datetime.combine(day, time(16, 10))


def test_live_sizes_from_the_money_in_the_account(tmp_path):
    eng, b, now = _live_trend(tmp_path / "a", 20_000.0)            # plan ₹1 lakh, account ₹20k
    eng.swing_plan(now)
    r = b.sent[0]
    assert 18_000 < r.qty * r.ref_price and r.qty * r.limit_price * 1.01 <= 20_000   # fits at the limit price
    msgs = "\n".join(eng.notifier.msgs)
    assert "sized at 19%" in msgs and "instead of the planned" not in msgs
    eng, b, now = _live_trend(tmp_path / "b", 1_000_000.0)          # account holds more than the plan
    eng.swing_plan(now)
    r = b.sent[0]
    assert 97_000 < r.qty * r.ref_price <= 100_000 and "sized at" not in "\n".join(eng.notifier.msgs)
    c = cfg(capital={"swing": 100_000, "live_from_account": False})  # switched off: plan size, cut by the funds check
    eng.cfg = c
    eng.risk.cfg = c
    assert eng.size_from_account() is None and eng.risk.account_scale() == 1.0


def test_live_rebalance_waits_for_sale_money_instead_of_buying_small(tmp_path):
    daily = {"UPA": _etf_daily(up=True, seed=1), "DOWNB": _etf_daily(up=False, seed=2), "NIFTYBEES": _etf_daily(seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": 100_000, "intraday": 0},
            strategies={"trend_allocation": {"enabled": True, "live": True, "override_gate": True,
                                             "params": {"assets": ["UPA", "DOWNB"], "sma_days": 200}}})
    strat = TrendAllocation("trend_allocation", {"assets": ["UPA", "DOWNB"], "sma_days": 200, "_start_now": True})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    b = FakeLive()
    money = {"free": 5_000.0}
    b.available_funds = lambda product: money["free"]
    inst = Instruments({"UPA": ("11", 0.01), "DOWNB": ("12", 0.01), "NIFTYBEES": ("13", 0.01)})
    eng = Engine(c, j, b, m, inst, Sink(), [strat], tmp_path, [], "NIFTYBEES", quiet=False)
    days = [d for d in m.sessions if d >= date(2025, 11, 10)]
    px = float(daily["DOWNB"].loc[pd.Timestamp(days[0]), "close"])
    j.save_position(Position("trend_allocation", "DOWNB", CNC, int(45_000 / px), px, datetime(2025, 6, 2), "E0", "12"))
    eng.swing_plan(datetime.combine(days[0], time(16, 10)))
    assert [(r.side, r.symbol) for r in b.sent] == [("SELL", "DOWNB")]       # the sell goes out, the buy waits
    assert strat.state_get("last_period") is None
    assert any("waiting for the money from this evening's sells" in t for t in eng.notifier.msgs)
    j.delete_position(j.positions()[0])                                       # sold at the open; cash arrived
    money["free"] = 50_000.0
    eng.swing_plan(datetime.combine(days[1], time(16, 10)))
    buy = [r for r in b.sent if r.side == "BUY"]
    assert len(buy) == 1 and buy[0].symbol == "UPA" and buy[0].qty * buy[0].ref_price > 20_000
    assert strat.state_get("last_period") is not None


def test_live_sizing_ignores_other_strategies_and_respects_reserve(tmp_path):
    eng, b, now = _live_trend(tmp_path / "a", 10_000.0)
    eng.j.save_position(Position("someone_else", "XYZ", CNC, 900, 100.0, now, "E9", "9"))   # ₹90k, not ours
    eng.size_from_account()
    assert eng.risk.account_scale() < 0.1                              # only ₹10k is really available
    c = cfg(capital={"swing": 100_000, "reserve": 50_000})
    eng2, b2, now2 = _live_trend(tmp_path / "b", 60_000.0)
    eng2.cfg = c
    eng2.risk.cfg = c
    o = eng2.submit(Signal("trend_allocation", "UPA", BUY, "entry", 1.0, 100.0, now2, CNC), 500, 100.0, now2, amo=True)
    assert o is not None and o.req.qty * o.req.limit_price * 1.01 <= 10_000   # ₹50k of the ₹60k stays untouched


def test_live_entry_blocked_when_funds_cannot_be_read(tmp_path):
    eng, b, now = _live_trend(tmp_path, None)
    o = eng.submit(Signal("trend_allocation", "UPA", BUY, "entry", 1.0, 100.0, now, CNC), 10, 100.0, now, amo=True)
    assert o is None and b.sent == [] and eng.last_block_funds
    assert any("could not read the account's funds" in t for t in eng.notifier.msgs)


def test_live_drawdown_base_is_the_most_capital_actually_used(tmp_path):
    eng, b, now = _live_trend(tmp_path, 20_000.0)
    eng.size_from_account()
    eng.risk.note_deployed("trend_allocation")
    base = eng.risk.drawdown_base("trend_allocation", "swing")
    assert 18_000 < base < 20_000                                      # judged on the ~₹19k it really had
    b.available_funds = lambda product: 5_000.0                       # cash moved out of the account
    eng.size_from_account()
    eng.risk.note_deployed("trend_allocation")
    assert eng.risk.drawdown_base("trend_allocation", "swing") == base  # a withdrawal can't trigger a demotion



def test_live_failed_funds_read_neither_buys_at_full_size_nor_raises_the_drawdown_base(tmp_path):
    eng, b, now = _live_trend(tmp_path, 20_000.0)
    eng.swing_plan(now)                                                # normal evening: peak ~₹19k
    base = eng.risk.drawdown_base("trend_allocation", "swing")
    assert 18_000 < base < 20_000
    eng2, b2, now2 = _live_trend(tmp_path / "x", None)                 # funds unreadable all evening
    eng2.swing_plan(now2)
    assert b2.sent == [] and eng2.strategies[0].state_get("last_period") is None
    assert any("no new buys tonight" in t for t in eng2.notifier.msgs)
    assert eng2.j.get("trend_allocation:deployed_peak") is None        # nothing recorded from an unknown account
    b.available_funds = lambda product: None                           # and on the first engine: peak unchanged
    eng.swing_plan(now + timedelta(days=1))
    assert eng.risk.drawdown_base("trend_allocation", "swing") == base
    c = cfg(capital={"swing": 15_000},                                 # plan lowered: the old peak is dropped
            strategies={"trend_allocation": {"enabled": True, "live": True, "override_gate": True}})
    eng.cfg = eng.risk.cfg = c
    b.available_funds = lambda product: 10_000.0
    eng.size_from_account()
    eng.risk.note_deployed("trend_allocation")
    assert eng.risk.drawdown_base("trend_allocation", "swing") == 15_000 < base


# ---------------- holdings reconciliation ----------------
def test_reconcile_flags_shortfall_only_and_fix_lowers_the_record(tmp_path):
    from guardian.broker import Holding
    from trader.reconcile import check, fix
    j = Journal(tmp_path / "j.db", "live")
    t = datetime(2026, 10, 6, 9, 30)
    j.save_position(Position("trend_allocation", "MON100", CNC, 58, 321.0, t, "E1", "500"))
    j.save_position(Position("trend_allocation", "GOLDBEES", CNC, 100, 80.0, t, "E2", "600"))
    j.save_position(Position("momentum_rotation", "MON100", CNC, 10, 321.0, t, "E3", "500"))
    held = [Holding("MON100", "500", 40, 320.0), Holding("GOLDBEES", "600", 250, 70.0)]   # extra GOLDBEES = yours
    bad = check(j.positions(product=CNC), held)
    assert bad == [("MON100", 68, 40)]
    notes = fix(j, bad, t)
    left = {(p.strategy, p.symbol): p.qty for p in j.positions(product=CNC)}
    assert left[("trend_allocation", "MON100")] == 30 and ("momentum_rotation", "MON100") in left   # biggest gives way
    assert sum(q for (s, sym), q in left.items() if sym == "MON100") == 40 and len(notes) == 1
    assert check(j.positions(product=CNC), held) == []
    assert check(j.positions(product=CNC), [Holding("MON100", "", 40, 1.0), Holding("GOLDBEES", "", 250, 1.0)]) == []


# ---------------- market-hours watch ----------------
def test_watch_alerts_company_specific_falls_once_and_exit_levels():
    from trader.watch import evaluate
    sent = set()
    prev = {"PB": 100.0, "ITC": 260.0, "TCS": 2000.0}
    a = evaluate({"PB": 91.0, "ITC": 249.0, "TCS": 1900.0}, prev, -0.04, [("ITC", 250.0, "weekly rule")], sent)
    text = "\n".join(a)
    assert "PB is down -9.0%" in text and "RECOVERED" in text          # -9% vs Nifty -4% = -5% relative
    assert "TCS" not in text                                           # -5% on a -4% market day: not company-specific
    assert "ITC is trading at ₹249.00, below your exit level ₹250.00" in text
    assert evaluate({"PB": 90.0, "ITC": 248.0}, prev, -0.04, [("ITC", 250.0, "")], sent) == []   # no repeats
    b = evaluate({"PB": 80.0}, prev, -0.04, [], sent)                  # falls further: the next level alerts once
    assert len(b) == 1 and "12%+" in b[0]


def test_watch_check_uses_live_prices_and_rule_levels():
    from guardian.rules import Rule
    from trader.watch import Watch

    class Client:
        def ltp(self, codes):
            return {"NSE_1": 1180.0, "NSE_9": 100.0}

    class Sink2:
        def __init__(self):
            self.msgs = []

        def send(self, t):
            self.msgs.append(t)

    rules = [Rule.from_dict({"id": "h", "type": "close_below", "symbol": "HCLTECH", "level": 1195, "note": "exit half"})]
    w = Watch(Client(), Sink2(), rules, Path("."), lambda: datetime(2026, 10, 5, 10, 0))
    w.scrip, w.nifty_code, w.prev = {"HCLTECH": "NSE_1"}, "NSE_9", {"HCLTECH": 1240.0, "__NIFTY__": 100.0}
    out = w.check()
    assert len(out) == 1 and "below your exit level ₹1,195.00" in out[0] and "daily close" in out[0]
    assert w.notifier.msgs == out and w.check() == []


# ---------------- handing existing holdings to a strategy ----------------
def test_adopt_plan_excludes_the_bots_own_shares_and_is_idempotent(tmp_path):
    from guardian.broker import Holding
    from trader.adopt import adopt, plan
    j = Journal(tmp_path / "j.db", "live")
    t = datetime(2026, 10, 4, 15, 0)
    j.save_position(Position("trend_allocation", "MON100", CNC, 58, 321.0, t, "E1", "22739"))
    held = [Holding("MON100", "22739", 60, 320.0), Holding("HCLTECH", "7229", 41, 1232.77),
            Holding("YESBANK", "11915", 342, 21.15), Holding("ITC", "1660", 94, 269.05),
            Holding("TCS", "11536", 16, 2283.55, used_qty=6)]                     # 6 pledged: not ours to sell
    items = plan(held, j.positions(product=CNC), skip=["itc"])
    assert items == [("HCLTECH", 41, 1232.77, "7229"), ("MON100", 2, 320.0, "22739"), ("TCS", 10, 2283.55, "11536"),
                     ("YESBANK", 342, 21.15, "11915")]
    prices = {"HCLTECH": 1243.1, "MON100": 321.5, "TCS": 2075.0}             # no price for YESBANK today
    notes = adopt(j, "momentum_rotation", items, prices, t)
    mine = {p.symbol: p for p in j.positions(strategy="momentum_rotation")}
    assert {k: v.qty for k, v in mine.items()} == {"HCLTECH": 41, "MON100": 2, "TCS": 10}
    assert mine["TCS"].avg_price == 2075.0 and mine["TCS"].meta["owner_cost"] == 2283.55   # record starts today
    assert any("YESBANK: no live price" in n for n in notes)
    notes = adopt(j, "momentum_rotation", items, prices, t)            # running it twice changes nothing
    assert len(j.positions(strategy="momentum_rotation")) == 3


def test_adopted_holdings_are_sold_when_momentum_says_cash(tmp_path):
    from trader.strategies.momentum import MomentumRotation
    daily = {"AAA": _etf_daily(up=True, seed=1), "ZZZ": _etf_daily(up=True, seed=4),
             "NIFTYBEES": _etf_daily(up=False, seed=3)}                  # market below its 200-day average
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": 500_000, "intraday": 0},
            strategies={"momentum_rotation": {"enabled": True, "live": True, "override_gate": True,
                                              "params": {"slots": 10, "exit_rank": 20}}})
    strat = MomentumRotation("momentum_rotation", {"slots": 10, "exit_rank": 20, "_start_now": True})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    b = FakeLive()
    b.available_funds = lambda product: 0.0                            # no cash: sells must still go out
    inst = Instruments({"AAA": ("1", 0.05), "ZZZ": ("2", 0.05), "NIFTYBEES": ("3", 0.01)})
    eng = Engine(c, j, b, m, inst, Sink(), [strat], tmp_path, ["AAA"], "NIFTYBEES", quiet=False)
    day = [d for d in m.sessions if d >= date(2025, 11, 10)][0]
    t = datetime.combine(day, time(15, 0))
    j.save_position(Position("momentum_rotation", "AAA", CNC, 40, 150.0, t, "adopted:AAA", "1"))
    j.save_position(Position("momentum_rotation", "ZZZ", CNC, 15, 120.0, t, "adopted:ZZZ", "2"))   # not in universe
    eng.swing_plan(datetime.combine(day, time(16, 10)))
    sent = sorted((r.side, r.symbol, r.qty, r.amo) for r in b.sent)
    assert sent == [("SELL", "AAA", 40, True), ("SELL", "ZZZ", 15, True)]
    assert any("market below 200-day average" in t for t in eng.notifier.msgs)



class ScriptedLive(FakeLive):
    """FakeLive whose order book can change an order's status on the next update()."""
    def __init__(self):
        super().__init__()
        self.next_status = {}

    def update(self, orders, now):
        for o in orders:
            if o.req.symbol in self.next_status:
                o.status = self.next_status.pop(o.req.symbol)
                o.message = "RMS: authorisation required"
        return super().update(orders, now)


def test_sell_rejected_at_the_open_alerts_and_is_retried_next_morning(tmp_path):
    daily = {"AAA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(up=False, seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(strategies={"momentum_rotation": {"enabled": True, "live": True, "override_gate": True}})
    from trader.strategies.momentum import MomentumRotation
    strat = MomentumRotation("momentum_rotation", {"_start_now": True})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    b = ScriptedLive()
    eng = Engine(c, j, b, m, Instruments({"AAA": ("1", 0.05), "NIFTYBEES": ("3", 0.01)}), Sink(), [strat], tmp_path,
                 ["AAA"], "NIFTYBEES", quiet=False)
    days = [d for d in m.sessions if d >= date(2025, 11, 10)]
    j.save_position(Position("momentum_rotation", "AAA", CNC, 40, 150.0, datetime(2025, 11, 1), "adopted:AAA", "1"))
    eng.swing_plan(datetime.combine(days[0], time(16, 10)))
    assert [(r.side, r.symbol) for r in b.sent] == [("SELL", "AAA")]
    b.next_status["AAA"] = REJECTED                                    # rejected at the next open
    eng.swing_check(datetime.combine(days[1], time(9, 30)))
    assert any("REJECTED after it was accepted" in t and "tried again" in t for t in eng.notifier.msgs)
    eng.swing_check(datetime.combine(days[2], time(9, 30)))            # next morning: sent again, once
    sells = [r for r in b.sent if r.side == "SELL"]
    assert len(sells) == 2 and sells[-1].qty == 40
    eng.swing_check(datetime.combine(days[2], time(9, 45)))            # a second run that day adds nothing
    assert len([r for r in b.sent if r.side == "SELL"]) == 2


def test_demoted_strategy_still_sells_what_it_holds(tmp_path):
    import json as _json
    from trader.strategies.momentum import MomentumRotation
    daily = {"AAA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(up=False, seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(strategies={"momentum_rotation": {"enabled": True, "live": True, "override_gate": True}})
    (tmp_path / "trader" / "state").mkdir(parents=True)
    (tmp_path / "trader" / "state" / "demoted.json").write_text(_json.dumps({"momentum_rotation": {"drawdown_pct": 9}}))
    j = Journal(tmp_path / "j.db", "live")
    j.save_position(Position("momentum_rotation", "AAA", CNC, 40, 150.0, datetime(2025, 11, 1), "E", "1"))
    strat = MomentumRotation("momentum_rotation", {"_start_now": True})
    strat.sessions = m.sessions
    b = FakeLive()
    eng = Engine(c, j, b, m, Instruments({"AAA": ("1", 0.05), "NIFTYBEES": ("3", 0.01)}), Sink(), [strat], tmp_path,
                 ["AAA"], "NIFTYBEES", quiet=True)
    assert eng.strategies == [] and eng.exit_only == [strat]
    eng.swing_plan(datetime.combine([d for d in m.sessions if d >= date(2025, 11, 10)][0], time(16, 10)))
    assert [(r.side, r.symbol) for r in b.sent] == [("SELL", "AAA")]


def test_off_switches_release_and_frozen_engine(tmp_path):
    from trader.adopt import release
    from trader.strategies.momentum import MomentumRotation
    j = Journal(tmp_path / "j.db", "live")
    t = datetime(2026, 10, 4, 15, 0)
    j.save_position(Position("momentum_rotation", "HCLTECH", CNC, 41, 1243.1, t, "adopted:HCLTECH", "7229",
                             meta={"adopted": True, "owner_cost": 1232.77}))
    j.save_position(Position("momentum_rotation", "INFY", CNC, 48, 1035.0, t, "adopted:INFY", "1594",
                             meta={"adopted": True, "owner_cost": 1052.46}))
    j.save_position(Position("momentum_rotation", "SBIN", CNC, 10, 800.0, t, "E1", "3045"))   # its own pick
    m = ReplayMarket({}, {})
    off = cfg(strategies={"momentum_rotation": {"enabled": True, "live": False}})
    eng = Engine(off, j, FakeLive(), m, None, None, [MomentumRotation("momentum_rotation", {})], tmp_path, [])
    assert eng.strategies == [] and eng.exit_only == []                # live: false = hands off completely
    frozen = Engine(cfg(), j, FakeLive(), m, None, Sink(), [MomentumRotation("momentum_rotation", {})], tmp_path, [],
                    frozen=True)
    assert frozen.strategies == [] and frozen.submit(Signal("s", "AAA", BUY, "entry", 1, 100.0, t, CNC), 1, 100.0, t,
                                                     amo=True) is None
    assert "switched off" in frozen.last_block
    notes = release(j, "momentum_rotation", ["hcltech"])
    assert notes == ["HCLTECH: 41 given back to you; momentum_rotation no longer manages it"]
    release(j, "momentum_rotation", ["all"])
    assert [p.symbol for p in j.positions()] == ["SBIN"]               # only adopted holdings are given back


def test_rejected_sell_retries_stop_after_three(tmp_path):
    daily = {"AAA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(up=False, seed=3)}
    m = ReplayMarket(daily, {})
    c = cfg(strategies={"momentum_rotation": {"enabled": True, "live": True, "override_gate": True}})
    from trader.strategies.momentum import MomentumRotation
    strat = MomentumRotation("momentum_rotation", {"_start_now": True})
    strat.sessions = m.sessions
    j = Journal(tmp_path / "j.db", "live")
    b = ScriptedLive()
    eng = Engine(c, j, b, m, Instruments({"AAA": ("1", 0.05), "NIFTYBEES": ("3", 0.01)}), Sink(), [strat], tmp_path,
                 ["AAA"], "NIFTYBEES", quiet=False)
    days = [d for d in m.sessions if d >= date(2025, 11, 10)]
    j.save_position(Position("momentum_rotation", "AAA", CNC, 40, 150.0, datetime(2025, 11, 1), "adopted:AAA", "1"))
    eng.swing_plan(datetime.combine(days[0], time(16, 10)))
    for k in range(1, 6):                                              # rejected every single morning
        b.next_status["AAA"] = REJECTED
        eng.swing_check(datetime.combine(days[k], time(9, 30)))
    sells = [r for r in b.sent if r.side == "SELL"]
    assert len(sells) == 3                                             # first + 2 retries, then it stops
    assert sum("not retrying" in t for t in eng.notifier.msgs) == 1


# ---------------- monthly report ----------------
def test_monthly_report_pnl_vs_nifty_snapshots_and_tax_on_owner_cost(tmp_path):
    from trader.report import build, default_month, tax_estimate
    c = cfg(capital={"swing": 420_000, "intraday": 50_000},
            strategies={"trend_allocation": {"capital_share": 0.18}, "momentum_rotation": {"capital_share": 0.82}})
    live, paper = Journal(tmp_path / "j.db", "live"), Journal(tmp_path / "j.db", "paper")
    t0 = datetime(2026, 10, 6, 9, 15)
    # trend: a closed trade (+₹1,000 gross, ₹40 charges) and an open MON100 position
    live.record_trade(Position("trend_allocation", "GOLDBEES", CNC, 100, 80.0, t0, "E1", "1", entry_charges=20.0),
                      100, 90.0, datetime(2026, 10, 20, 9, 15), 20.0, "X1", "exit")
    live.save_position(Position("trend_allocation", "MON100", CNC, 58, 320.0, t0, "E2", "2", entry_charges=10.0))
    # momentum: a handed-over TCS sold slightly above the handover price but below the owner's cost
    live.put("owner_cost:momentum_rotation:TCS", 2283.55)
    live.record_trade(Position("momentum_rotation", "TCS", CNC, 16, 2075.0, datetime(2026, 10, 4, 15, 44),
                               "adopted:TCS:261004", "11536"), 16, 2100.0, datetime(2026, 10, 6, 9, 15), 30.0, "X2", "exit")
    nifty = pd.Series([100.0, 102.0], index=pd.DatetimeIndex(["2026-09-30", "2026-10-30"]))
    text = build(c, live, paper, {"MON100": 330.0}, nifty, "2026-10", True)
    assert "Nifty ETF this month: +2.0%" in text
    assert "ETF trend: +₹1,530 (+2.0% of ₹75,600) since the start" in text      # 960 realized + 580 open - 10
    assert "Momentum: +₹370" in text and "holding MON100 58 (₹19,140)" in text
    assert "Live total: +₹1,900" in text and "vs Nifty ETF +2.0%" in text
    tx = tax_estimate(live, date(2026, 10, 31))
    assert round(tx["short_term"]) == round(960 + (2100 - 2283.55) * 16 - 30)     # owner's cost, not the handover price
    assert "losses to carry forward" in text
    # next month: only the change since the October snapshot counts
    text2 = build(c, live, paper, {"MON100": 340.0}, nifty, "2026-11", True)
    assert "ETF trend: +₹580 (+0.8% of ₹75,600) | closed trades: 0" in text2                       # 58 x ₹10 more, no "since the start"
    assert default_month(date(2026, 11, 1)) == ("2026-10", True) and default_month(date(2026, 11, 15)) == ("2026-11", False)




# ---------------- corner cases: bonus issues / splits, unfinished sells, price bands, NSE list ----------------
def _split_daily(n=300, start="2026-01-01", split_at=None, ratio=0.5, adjusted=False, px=1000.0, gap=None):
    idx = pd.bdate_range(start, periods=n)
    c = px * (1 + 0.002 * np.sin(np.arange(n)))
    o = c.copy()
    if split_at is not None:
        k = idx.get_loc(pd.Timestamp(split_at))
        if adjusted:
            c, o = c * ratio, o * ratio                     # the data provider already back-adjusted the history
        else:
            c[k:] *= ratio
            o[k:] *= ratio
            if gap is not None:                             # the ex-date's own move: open at ratio, close elsewhere
                c[k:] *= gap
                o[k + 1:] *= gap
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995, "close": c,
                         "volume": 1e5}, index=idx)


def _pos(qty=40, px=1000.0, when=datetime(2026, 3, 2, 9, 15), meta=None, sid="1"):
    return Position("momentum_rotation", "AAA", CNC, qty, px, when, "adopted:AAA", sid, meta=dict(meta or {}))


def test_bonus_or_split_found_in_raw_and_adjusted_history_only_when_real():
    from trader.corporate import confirmed, find_one
    raw = _split_daily(split_at="2026-06-01")
    p = _pos()
    a = find_one(p, raw)
    assert a and a.factor == 0.5 and a.new_qty == 80 and a.when == date(2026, 6, 1) and round(a.new_avg, 2) == 500.0
    a2 = find_one(p, _split_daily(split_at="2026-06-01", adjusted=True))
    assert a2 and a2.factor == 0.5 and a2.new_qty == 80 and a2.when == date(2026, 3, 2)
    assert find_one(_pos(80, 500.0), _split_daily(split_at="2026-06-01", adjusted=True)) is None   # already done
    assert find_one(_pos(80, 500.0, datetime(2026, 7, 1)), raw) is None                          # bought after
    assert find_one(p, _split_daily(split_at="2026-06-01", ratio=0.63)) is None   # demerger-sized drop: not a ratio
    assert find_one(_pos(40, 1330.0, datetime(2026, 10, 5, 9, 15)), raw) is None  # bought today: no candle yet
    a3 = find_one(_pos(7), _split_daily(split_at="2026-06-01", ratio=0.4))
    assert a3.factor == 0.4 and a3.new_qty == 17                                    # 3:2 bonus, half share paid in cash
    # the ex-date's own move (-6%) hides the exact ratio: only the live (loose) check sees it, every time the same
    moved = _split_daily(split_at="2026-06-01", gap=0.94)
    assert find_one(p, moved) is None
    b = find_one(p, moved, loose=True)
    assert b.factor == 0.5 and b.when == date(2026, 6, 1)
    assert find_one(_pos(80, 500.0), moved, loose=True) is None                    # adjusted: stays adjusted
    assert find_one(p, moved, rejected=["2026-06-01"], loose=True) is None         # judged a real fall
    assert find_one(p, raw, rejected=["2026-06-01"]) is None
    # the account confirms only by GROWING by the bonus shares since the last count
    assert not confirmed({"1": 80}, p, a)                                           # no count yet
    assert not confirmed({"1": 100}, _pos(meta={"acct_qty": 100}), a)              # your own 60 shares, no bonus
    assert confirmed({"1": 200}, _pos(meta={"acct_qty": 100}), a)                  # yours doubled too
    assert confirmed({"1": 80}, _pos(meta={"acct_qty": 40}), a)


def _momentum_live():
    from trader.strategies.momentum import MomentumRotation
    c = cfg(strategies={"momentum_rotation": {"enabled": True, "live": True, "override_gate": True}})
    return c, [MomentumRotation("momentum_rotation", {})]


def _bonus_engine(tmp_path, broker, held, daily=None, meta=None):
    from guardian.broker import Holding
    m = ReplayMarket({"AAA": daily if daily is not None else _split_daily(split_at="2026-06-01"),
                      "NIFTYBEES": _split_daily(px=250.0)}, {})
    j = Journal(tmp_path / "j.db", "live")
    broker.holdings = lambda: [Holding("AAA", "1", held["q"], 1000.0)]
    c, strats = _momentum_live()
    eng = Engine(c, j, broker, m, Instruments({"AAA": ("1", 0.05)}), Sink(), strats, tmp_path, ["AAA"], quiet=False)
    j.save_position(_pos(meta={"adopted": True, "owner_cost": 900.0, **(meta or {})}))
    j.put("owner_cost:momentum_rotation:AAA", 900.0)
    return eng, j, m


def test_live_bonus_waits_for_the_new_shares_then_adjusts_once(tmp_path):
    held = {"q": 40}
    eng, j, _ = _bonus_engine(tmp_path, FakeLive(), held)
    eng.swing_check(datetime(2026, 5, 29, 9, 30))                    # before the ex-date: the account's count is kept
    assert j.positions()[0].meta["acct_qty"] == 40
    eng.swing_check(datetime(2026, 6, 2, 9, 30))                     # ex-date yesterday; shares not credited yet
    assert j.positions()[0].qty == 40
    assert sum("should soon show 80" in t for t in eng.notifier.msgs) == 1
    eng.swing_check(datetime(2026, 6, 2, 9, 45))
    assert sum("should soon show 80" in t for t in eng.notifier.msgs) == 1        # said once
    held["q"] = 80
    eng.swing_check(datetime(2026, 6, 3, 9, 30))
    p = j.positions()[0]
    assert p.qty == 80 and round(p.avg_price, 2) == 500.0 and p.meta["owner_cost"] == 450.0 and p.meta["acct_qty"] == 80
    assert j.get("owner_cost:momentum_rotation:AAA") == 450.0 and not j.get("ca_wait:momentum_rotation:AAA")
    assert any("now holds 80 shares at ₹500.00 (was 40 at ₹1,000.00)" in t for t in eng.notifier.msgs)
    eng.swing_check(datetime(2026, 6, 4, 9, 30))
    assert j.positions()[0].qty == 80                                 # never adjusted twice


def test_crash_at_a_bonus_ratio_with_own_shares_is_not_taken_for_a_bonus(tmp_path):
    held = {"q": 100}                                                 # the bot's 40 + 60 of your own
    eng, j, _ = _bonus_engine(tmp_path, FakeLive(), held)
    eng.swing_check(datetime(2026, 5, 29, 9, 30))
    for d in (2, 3, 4, 5, 8):                                         # the account never grows: a real fall
        eng.swing_check(datetime(2026, 6, d, 9, 30))
    p = j.positions()[0]
    assert p.qty == 40 and p.avg_price == 1000.0
    assert any("treats it as a real price fall" in t for t in eng.notifier.msgs)
    assert j.get("ca_rejected:momentum_rotation:AAA") == ["2026-06-01"] and not j.get("ca_wait:momentum_rotation:AAA")
    assert eng._ca_hold(p, datetime(2026, 6, 9, 9, 30)) is None       # its sells are no longer held


def test_paper_bonus_is_adjusted_at_once_consolidation_never(tmp_path):
    m = ReplayMarket({"AAA": _split_daily(split_at="2026-06-01"), "BBB": _split_daily(split_at="2026-06-01", ratio=2.0)}, {})
    j = Journal(tmp_path / "j.db", "paper")
    eng = Engine(cfg(), j, PaperBroker(m, ZERO), m, Instruments({"AAA": ("1", 0.05), "BBB": ("2", 0.05)}), Sink(), [],
                 tmp_path, ["AAA"], quiet=False)
    j.save_position(Position("trend_allocation", "AAA", CNC, 40, 1000.0, datetime(2026, 3, 2, 9, 15), "E", "1"))
    j.save_position(Position("trend_allocation", "BBB", CNC, 40, 1000.0, datetime(2026, 3, 2, 9, 15), "E", "2"))
    assert eng.corporate_actions(datetime(2026, 6, 1, 16, 10))
    q = {p.symbol: p.qty for p in j.positions()}
    assert q == {"AAA": 80, "BBB": 40} and any("Not applied automatically" in t for t in eng.notifier.msgs)
    assert not eng.corporate_actions(datetime(2026, 6, 2, 16, 10))


def test_ex_date_morning_holds_the_sell_then_sells_the_new_count(tmp_path):
    class InstantCancel(FakeLive):
        def cancel(self, order, now):
            self.cancels.append(order.req.tag)
            order.status = CANCELLED
            return order

    held = {"q": 40}
    b = InstantCancel()
    eng, j, _ = _bonus_engine(tmp_path, b, held, meta={"acct_qty": 40})
    amo = Order(OrderRequest("momentum_rotation:AAA:SE:260529161000", "momentum_rotation", "AAA", "1", SELL, 40, CNC,
                             980.0, "sig", "exit", datetime(2026, 5, 29, 16, 10), amo=True), status=OPEN, broker_id="EQ-1")
    j.save_order(amo)                                                 # Friday's sell for 40; Monday 1 June goes ex
    eng.swing_check(datetime(2026, 6, 1, 9, 30))                     # data ends Friday: the drop is seen only in the price
    assert b.cancels == [amo.req.tag] and not [r for r in b.sent if r.side == SELL]
    assert any("Sell of AAA (momentum_rotation) held" in t and "going ex today" in t for t in eng.notifier.msgs)
    eng.swing_check(datetime(2026, 6, 2, 9, 30))                     # ex-date in the data, shares not here: still held
    assert not [r for r in b.sent if r.side == SELL]
    held["q"] = 80
    eng.swing_check(datetime(2026, 6, 3, 9, 30))                     # shares arrived: adjusted, then sold in full
    sells = [r for r in b.sent if r.side == SELL]
    assert j.positions()[0].qty == 80 and len(sells) == 1 and sells[0].qty == 80 and 480 < sells[0].limit_price < 510


def _retry_engine(tmp_path, book):
    from guardian.broker import Holding
    daily = {"AAA": _etf_daily(up=True, seed=1), "NIFTYBEES": _etf_daily(up=False, seed=3)}
    m = ReplayMarket(daily, {})
    days = [d for d in m.sessions if d >= date(2025, 11, 10)]
    book.holdings = lambda: [Holding("AAA", "1", 40, 100.0)]
    j = Journal(tmp_path / "j.db", "live")
    c, strats = _momentum_live()
    eng = Engine(c, j, book, m, Instruments({"AAA": ("1", 0.05)}), Sink(), strats, tmp_path, ["AAA"], quiet=False)
    px0 = float(daily["AAA"].loc[pd.Timestamp(days[0]), "close"])
    j.save_position(Position("momentum_rotation", "AAA", CNC, 40, px0, datetime.combine(days[0], time(9, 15)), "E", "1"))
    return eng, j, m, days, px0


class _Book(FakeLive):
    def __init__(self):
        super().__init__()
        self.next = {}

    def update(self, orders, now):
        for o in orders:
            if o.req.tag in self.next:
                o.status, o.message = self.next.pop(o.req.tag)
        return super().update(orders, now)


def _open_sell(j, tag, created, px):
    o = Order(OrderRequest(tag, "momentum_rotation", "AAA", "1", SELL, 40, CNC, px, "sig", "exit", created),
              status=OPEN, broker_id="EQ-" + tag[-1])
    j.save_order(o)
    return o


def test_sell_that_expires_or_hits_the_price_band_is_sent_again(tmp_path):
    b = _Book()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(9, 30)), px0 * 0.99)
    b.next[o.req.tag] = (CANCELLED, "expired: not filled by the close")
    eng.sync(datetime.combine(days[1], time(16, 10)))
    assert j.get(f"ext_cancel:{o.req.tag}")
    eng.swing_check(datetime.combine(days[2], time(9, 30)))           # next morning: sent again, for the position
    sells = [r for r in b.sent if r.side == SELL]
    ltp2 = m.ltp(["AAA"], datetime.combine(days[2], time(9, 30)))["AAA"]
    assert len(sells) == 1 and sells[0].qty == 40 and sells[0].limit_price < ltp2 * 0.996
    b.next[sells[0].tag] = (REJECTED, "Order price is outside the daily price band")
    eng.swing_check(datetime.combine(days[2], time(9, 45)))           # rejected: alert, no second try today
    assert len([r for r in b.sent if r.side == SELL]) == 1
    assert any("REJECTED after it was accepted" in t for t in eng.notifier.msgs)
    eng.swing_check(datetime.combine(days[3], time(9, 30)))           # band rejection: re-sent AT the last price
    sells = [r for r in b.sent if r.side == SELL]
    ltp3 = m.ltp(["AAA"], datetime.combine(days[3], time(9, 30)))["AAA"]
    assert len(sells) == 2 and abs(sells[-1].limit_price - ltp3) < 0.051
    assert not any("not retrying" in t for t in eng.notifier.msgs)


def test_sell_cancelled_by_you_is_not_resent_and_old_failures_dont_sell_a_new_position(tmp_path):
    b = _Book()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(9, 30)), px0 * 0.99)
    b.next[o.req.tag] = (CANCELLED, "Cancelled by user")
    eng.sync(datetime.combine(days[1], time(11, 0)))
    assert not j.get(f"ext_cancel:{o.req.tag}")
    assert any("cancelled outside the bot" in t and "NOT sent again" in t for t in eng.notifier.msgs)
    eng.swing_check(datetime.combine(days[2], time(9, 30)))
    assert not [r for r in b.sent if r.side == SELL]
    # an expired sell of an EARLIER position must not sell a position opened later
    o2 = _open_sell(j, "momentum_rotation:AAA:SE:2", datetime.combine(days[2], time(9, 30)), px0 * 0.99)
    b.next[o2.req.tag] = (CANCELLED, "expired")
    eng.sync(datetime.combine(days[2], time(16, 10)))
    j.delete_position(j.positions()[0])                               # sold by hand, then bought again
    px4 = float(m.daily_frames["AAA"].loc[pd.Timestamp(days[4]), "close"])
    j.save_position(Position("momentum_rotation", "AAA", CNC, 30, px4, datetime.combine(days[4], time(9, 15)), "E2", "1"))
    eng.swing_check(datetime.combine(days[5], time(9, 30)))
    assert not [r for r in b.sent if r.side == SELL]


def test_split_command_records_a_bonus_by_hand(tmp_path, monkeypatch, capsys):
    from trader import run as R
    monkeypatch.setattr(R, "ROOT", tmp_path)
    j = Journal(tmp_path / "j.db", "live")
    j.save_position(_pos(meta={"acct_qty": 40, "owner_cost": 900.0}))
    j.close()
    c = cfg(journal="j.db")

    class A:
        stock, ratio = "aaa", 2.0

    assert R.cmd_split(c, A()) == 0
    j = Journal(tmp_path / "j.db", "live")
    p = j.positions()[0]
    assert p.qty == 80 and p.avg_price == 500.0 and p.meta["owner_cost"] == 450.0 and p.meta["acct_qty"] == 80
    assert "now holds 80 shares" in capsys.readouterr().out
    A.stock = "ZZZ"
    assert R.cmd_split(c, A()) == 1


def test_watch_tells_a_bonus_or_split_from_a_crash_and_mentions_dividends():
    from trader.watch import evaluate
    a = evaluate({"HDFCBANK": 500.0, "ITC": 245.0}, {"HDFCBANK": 1000.0, "ITC": 260.0}, 0.0,
                 [("HDFCBANK", 900.0, "")], set())
    hd = [t for t in a if t.startswith("⚠️ HDFCBANK")][0]
    assert "1:1 bonus or 2-for-1 split" in hd and "ONLY if HDFCBANK announced one" in hd and "real crash" in hd
    assert "12%+" in hd                                                         # the crash guidance is still there
    it = [t for t in a if t.startswith("⚠️ ITC")][0]
    assert "ex-dividend" in it and "RECOVERED" in it
    lv = [t for t in a if t.startswith("🔻")][0]
    assert "below your exit level" in lv and "same adjustment" in lv
    crash = evaluate({"PB": 57.0, "XY": 72.0}, {"PB": 100.0, "XY": 100.0}, 0.0, [], set())
    assert all("bonus" not in t for t in crash) and all("12%+" in t for t in crash)   # -43%, -28%: plain crashes
    near = evaluate({"ZZ": 74.7}, {"ZZ": 100.0}, 0.0, [], set())[0]                   # -25.3%: could be a 1:3 bonus
    assert "1:3 bonus" in near and "real crash" in near and "12%+" in near


def test_nifty50_list_refresh_from_nse(tmp_path):
    import json as _json
    import pytest
    from trader import universe_update as U
    base = [f"S{i:02d}" for i in range(50)]
    rows = base[:48] + ["NEWA", "NEWB"]
    text = "﻿Company Name,Industry,Symbol,Series,ISIN Code\n" + "\n".join(
        f"Co {s},X,{s},EQ,INE{i:07d}" for i, s in enumerate(rows))

    class R:
        def __init__(self, code, body):
            self.status_code, self.text = code, body

    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        return R(403, "denied") if len(calls) == 1 else R(200, text)          # first source refuses, second answers

    changed, msg = U.update(tmp_path, base, date(2026, 10, 5), known=lambda s: True, get=get)
    assert changed and "added NEWA, NEWB" in msg and "removed S48, S49" in msg and len(calls) == 2
    assert C.universe({}, root=tmp_path) == sorted(rows)
    assert C.universe({}, root=tmp_path, nse=False) != sorted(rows)               # replays keep the research list
    assert C.universe({"universe": ["x"]}, root=tmp_path) == ["X"]                # an explicit list still wins
    changed, msg = U.update(tmp_path, sorted(rows), date(2026, 10, 12), get=lambda *a, **k: R(200, text))
    assert not changed and "unchanged" in msg
    with pytest.raises(RuntimeError):
        U.update(tmp_path, base, date(2026, 10, 19), get=lambda *a, **k: R(403, "no"))
    assert U.check(base[:30], base)[0] is None                                   # too short
    assert U.check([f"Z{i:02d}" for i in range(50)], base)[0] is None              # nothing in common
    assert U.check(base[:40] + [f"N{i}" for i in range(10)], base)[0] is None      # 10 swapped at once
    new, note = U.check(base[:47] + ["Q1", "Q2", "Q3"], base, known=lambda s: not s.startswith("Q"))
    assert len(new) == 47 and "Q1" in note
    assert U.check(base[:46] + ["Q1", "Q2", "Q3", "Q4"], base, known=lambda s: not s.startswith("Q"))[0] is None
    assert U.staleness(tmp_path, {}, date(2026, 10, 20)) is None
    assert "last confirmed 49 days ago" in U.staleness(tmp_path, {}, date(2026, 11, 30))
    assert "never downloaded" in U.staleness(tmp_path / "none", {}, date(2026, 11, 30))
    assert U.staleness(tmp_path / "none", {"universe": ["A"]}, date(2026, 11, 30)) is None
    (tmp_path / U.STATE).write_text(_json.dumps(["not", "a", "dict"]))           # damaged file: ignored, no crash
    assert U.load(tmp_path) is None and C.universe({}, root=tmp_path) == C.universe({}, root=tmp_path, nse=False)


def test_cancel_forget_clears_an_order_the_broker_lost_and_the_sell_is_resent(tmp_path, monkeypatch):
    from trader import run as R
    b = _Book()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(9, 30)), px0 * 0.99)

    def fail(order, now):
        order.message = "cancel failed: /order/cancel: HTTP 400 order not found"
        return order

    b.cancel = fail
    monkeypatch.setattr(R, "build_engines", lambda cfg, dry: [eng])
    clock = {"t": datetime.combine(days[2], time(8, 50))}
    monkeypatch.setattr(R, "now_ist", lambda: clock["t"])

    class A:
        dry_run, forget = True, True

    assert R.cmd_cancel(cfg(), A()) == 1 and j.order(o.req.tag).status == OPEN   # not before 15:35
    A.forget, clock["t"] = False, datetime.combine(days[2], time(15, 40))
    R.cmd_cancel(cfg(), A())
    assert j.order(o.req.tag).status == OPEN                          # without --forget nothing is assumed
    A.forget, clock["t"] = True, datetime.combine(days[2], time(15, 45))
    R.cmd_cancel(cfg(), A())
    assert j.order(o.req.tag).status == CANCELLED and j.get(f"ext_cancel:{o.req.tag}")
    eng.swing_check(datetime.combine(days[3], time(9, 30)))
    assert [r.qty for r in b.sent if r.side == SELL] == [40]


def test_big_ex_date_move_snaps_to_the_bonus_ratio():
    from trader.corporate import find_one
    a = find_one(_pos(), _split_daily(split_at="2026-06-01", ratio=0.475))       # 1:1 bonus and -5% that day
    assert a is not None and a.factor == 0.5 and a.new_qty == 80


def test_waiting_for_bonus_shares_times_out_even_without_holdings_and_cancel_drops_held_sells(tmp_path):
    def broken():
        raise RuntimeError("holdings API down")

    b = FakeLive()
    eng, j, _ = _bonus_engine(tmp_path, b, {"q": 40}, meta={"acct_qty": 40})
    b.holdings = broken
    for d in (2, 3, 4, 5):
        eng.swing_check(datetime(2026, 6, d, 9, 30))
    assert any("treats it as a real price fall" in t for t in eng.notifier.msgs)
    assert j.positions()[0].qty == 40
    # a held sell is dropped by `trader.run cancel`
    eng2, j2, _ = _bonus_engine(tmp_path / "b", FakeLive(), {"q": 40}, meta={"acct_qty": 40})
    eng2._defer_exit(j2.positions()[0], datetime(2026, 6, 1, 9, 30), "test")
    j2.put("cancel_all_at", datetime(2026, 6, 1, 12, 0).isoformat())
    j2.put("ca_rejected:momentum_rotation:AAA", ["2026-06-01"])                   # nothing else holds it
    eng2.swing_check(datetime(2026, 6, 3, 9, 30))
    assert not [r for r in eng2.broker.sent if r.side == SELL] and not j2.get("deferred_exit:momentum_rotation:AAA")


def test_exchange_cancel_is_resent_and_a_switched_off_strategy_is_not(tmp_path):
    b = _Book()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(9, 30)), px0 * 0.99)
    b.next[o.req.tag] = (CANCELLED, "Cancelled by exchange: corporate action")
    eng.sync(datetime.combine(days[1], time(16, 10)))
    assert j.get(f"ext_cancel:{o.req.tag}")
    eng.strategies = []                                                # owner set live: false
    eng.swing_check(datetime.combine(days[2], time(9, 30)))
    assert not [r for r in b.sent if r.side == SELL]


def test_split_ratio_one_stops_waiting(tmp_path, monkeypatch, capsys):
    from trader import run as R
    monkeypatch.setattr(R, "ROOT", tmp_path)
    monkeypatch.setattr(R, "now_ist", lambda: datetime(2026, 6, 2, 10, 0))
    j = Journal(tmp_path / "j.db", "live")
    j.save_position(_pos())
    j.put("ca_wait:momentum_rotation:AAA", {"when": "2026-06-01", "since": "2026-06-02", "entry": "adopted:AAA"})
    j.close()

    class A:
        stock, ratio = "AAA", 1.0

    assert R.cmd_split(cfg(journal="j.db"), A()) == 0
    j = Journal(tmp_path / "j.db", "live")
    assert not j.get("ca_wait:momentum_rotation:AAA")
    assert j.get("ca_rejected:momentum_rotation:AAA") == ["2026-06-01", "2026-06-02"] and j.positions()[0].qty == 40


def test_order_call_retries_once_after_a_token_handover():
    import guardian.broker as gb
    from trader.brokers.indstocks import IndStocksBroker

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class Client:
        timeout, tokens, sent = 5, ["old", "new"], []

        def __init__(self):
            self._session = self

        def _ensure_token(self):
            return self.tokens[0]

        def request(self, method, url, json=None, params=None, headers=None, timeout=None):
            self.sent.append(headers["Authorization"])
            if headers["Authorization"] == "old":
                return Resp(401, {"status": "error", "error_type": "TokenException", "message": "invalid token"})
            return Resp(200, {"status": "success", "data": {"order_id": "EQ-1"}})

        _is_token_error = staticmethod(gb.IndStocksClient._is_token_error)

        def recover_token(self, rejected):
            self.tokens.pop(0)
            return True

    c = Client()
    br = IndStocksBroker(c)
    assert br._send("POST", "/order", {"x": 1})["data"]["order_id"] == "EQ-1" and c.sent == ["old", "new"]


def test_evening_run_builds_todays_candle_when_the_daily_one_is_late(tmp_path):
    from trader.market import LiveMarket
    m = LiveMarket(None, Instruments({"AAA": ("1", 0.05)}), tmp_path)
    old = pd.DataFrame({"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [100.0]},
                       index=pd.DatetimeIndex([pd.Timestamp("2026-10-01")], name="date"))
    idx = pd.date_range("2026-10-05 09:15", periods=75, freq="5min")
    bars = pd.DataFrame({"open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 10.0}, index=idx)
    bars.iloc[-1, bars.columns.get_loc("close")] = 11.8
    m._daily = {"AAA": old, "BBB": old}
    m.today_bars = lambda syms, now: {"AAA": bars, "BBB": bars.iloc[:0]}          # BBB: no bars (holiday-like)
    m._fill_today(datetime(2026, 10, 5, 16, 10))
    a = m._daily["AAA"]
    assert a.index[-1] == pd.Timestamp("2026-10-05") and a["close"].iloc[-1] == 11.8 and a["volume"].iloc[-1] == 750
    assert a["open"].iloc[-1] == 11.0 and a["high"].iloc[-1] == 12.0 and a["low"].iloc[-1] == 10.0
    assert m._daily["BBB"].index[-1] == pd.Timestamp("2026-10-01")



class _KeepBook(_Book):
    """Confirms our cancels at the next sync when `confirm` is set."""
    confirm = True

    def update(self, orders, now):
        from trader.models import CANCEL_SENT
        for o in orders:
            if o.status == CANCEL_SENT and self.confirm:
                o.status, o.message = CANCELLED, "cancelled"
        return super().update(orders, now)


def test_keep_replaces_the_pending_sell_with_a_smaller_one(tmp_path, monkeypatch):
    from trader import run as R
    from trader import engine as E
    from trader.instruments import round_tick
    monkeypatch.setattr(E._time, "sleep", lambda s: None)
    b = _KeepBook()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(16, 10)), px0 * 0.98)
    monkeypatch.setattr(R, "build_engines", lambda cfg, dry: [eng])
    clock = {"t": datetime.combine(days[2], time(10, 0))}
    monkeypatch.setattr(R, "now_ist", lambda: clock["t"])

    class A:
        dry_run, stock, qty = True, "AAA", 1

    assert R.cmd_keep(cfg(), A()) == 1 and not b.cancels                       # market hours: refused
    clock["t"] = datetime.combine(days[1], time(17, 30))
    A.qty = 40
    assert R.cmd_keep(cfg(), A()) == 1 and not b.cancels                       # all of them: use adopt --release
    A.qty, b.confirm = 1, False
    assert R.cmd_keep(cfg(), A()) == 1 and b.cancels == [o.req.tag]            # cancel not confirmed: nothing changed
    assert j.positions()[0].qty == 40 and not [r for r in b.sent if r.side == SELL]
    j.save_order(o)                                                            # (back to OPEN for the next try)
    b.cancels, b.confirm = [], True
    assert R.cmd_keep(cfg(), A()) == 0
    sells = [r for r in b.sent if r.side == SELL]
    assert j.positions()[0].qty == 39 and len(sells) == 1 and sells[0].qty == 39 and sells[0].amo
    assert sells[0].limit_price == round_tick(px0 * 0.98, 0.05, "down") and j.order(o.req.tag).status == CANCELLED
    assert any("1 share kept for you" in t for t in eng.notifier.msgs)
    assert not [t for t in eng.notifier.msgs if "cancelled outside the bot" in t]


def test_keep_falls_back_to_the_morning_sell_if_the_new_order_is_rejected(tmp_path, monkeypatch):
    from trader import run as R
    b = _KeepBook()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(16, 10)), px0 * 0.98)
    b.place_status = REJECTED
    monkeypatch.setattr(R, "build_engines", lambda cfg, dry: [eng])
    monkeypatch.setattr(R, "now_ist", lambda: datetime.combine(days[1], time(17, 30)))

    class A:
        dry_run, stock, qty = True, "AAA", 2

    assert R.cmd_keep(cfg(), A()) == 1 and j.positions()[0].qty == 38
    b.place_status = OPEN
    eng.swing_check(datetime.combine(days[2], time(9, 30)))
    assert [r.qty for r in b.sent if r.side == SELL] == [38, 38]                # rejected AMO, then the morning sell


def test_keep_run_again_after_a_late_cancel_confirmation_finishes_the_job(tmp_path, monkeypatch):
    from trader import run as R
    from trader import engine as E
    monkeypatch.setattr(E._time, "sleep", lambda s: None)
    b = _KeepBook()
    b.confirm = False
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    o = _open_sell(j, "momentum_rotation:AAA:SE:1", datetime.combine(days[1], time(16, 10)), px0 * 0.98)
    monkeypatch.setattr(R, "build_engines", lambda cfg, dry: [eng])
    monkeypatch.setattr(R, "now_ist", lambda: datetime.combine(days[1], time(17, 30)))

    class A:
        dry_run, stock, qty = True, "AAA", 1

    assert R.cmd_keep(cfg(), A()) == 1 and j.positions()[0].qty == 40
    b.confirm = True                                                           # INDstocks confirms it a bit later
    assert R.cmd_keep(cfg(), A()) == 0 and j.order(o.req.tag).status == CANCELLED
    assert j.positions()[0].qty == 39 and [r.qty for r in b.sent if r.side == SELL] == [39]


def _nse_holidays(*days):
    return {"CM": [{"tradingDate": d, "weekDay": "x", "description": "h", "Sr_no": i} for i, d in enumerate(days)],
            "FO": [{"tradingDate": "01-Jan-2026"}]}


class _Reply:
    def __init__(self, code, body):
        self.status_code, self.text = code, body


def test_holiday_list_is_parsed_checked_and_saved(tmp_path):
    import json as _json
    import pytest
    from trader import holidays as H
    days = ["26-Jan-2026", "03-Mar-2026", "26-Mar-2026", "31-Mar-2026", "03-Apr-2026", "14-Apr-2026", "01-May-2026",
            "28-May-2026", "02-Oct-2026", "20-Oct-2026", "10-Nov-2026", "24-Nov-2026", "25-Dec-2026",
            "15-Aug-2026"]                                                 # a Saturday: dropped
    get = lambda url, headers=None, timeout=None: _Reply(200, _json.dumps(_nse_holidays(*days)))   # noqa: E731
    changed, msg = H.update(tmp_path, date(2026, 10, 5), get=get)
    hol = H.load(tmp_path)
    assert changed and date(2026, 10, 20) in hol and date(2026, 8, 15) not in hol and len(hol) == 13
    assert "Tue 20 Oct" in msg
    assert not H.update(tmp_path, date(2026, 10, 12), get=get)[0]          # same list: no message
    changed, msg = H.update(tmp_path, date(2026, 10, 19), get=lambda *a, **k: _Reply(200, _json.dumps(
        _nse_holidays(*(days[:-2] + ["30-Oct-2026"])))))
    assert changed and "added 30 Oct 2026" in msg and "removed 25 Dec 2026" in msg
    assert H.load(tmp_path, {"holidays": ["2026-12-25"]}) >= {date(2026, 12, 25), date(2026, 10, 30)}
    with pytest.raises(Exception):
        H.update(tmp_path, date(2026, 10, 26), get=lambda *a, **k: _Reply(403, "denied"))
    with pytest.raises(ValueError):
        H.update(tmp_path, date(2026, 10, 26), get=lambda *a, **k: _Reply(200, _json.dumps({"FO": []})))
    with pytest.raises(ValueError):                                         # too few for the year: not used
        H.update(tmp_path, date(2026, 10, 26), get=lambda *a, **k: _Reply(200, _json.dumps(_nse_holidays("26-Jan-2026"))))
    assert date(2026, 10, 30) in H.load(tmp_path)                           # the saved list stays
    assert H.staleness(tmp_path, date(2026, 10, 26)) is None
    assert "last checked" in H.staleness(tmp_path, date(2026, 12, 1))
    import json as _j
    st = _j.loads((tmp_path / H.STATE).read_text())
    st["checked"] = "2027-01-11"
    (tmp_path / H.STATE).write_text(_j.dumps(st))
    assert H.staleness(tmp_path, date(2027, 1, 11)) is None                  # early January: no warning yet
    assert "no 2027 dates" in H.staleness(tmp_path, date(2027, 1, 18))
    assert "never downloaded" in H.staleness(tmp_path / "none", date(2026, 10, 26))
    (tmp_path / H.STATE).write_text("not json")
    assert H.load(tmp_path) == set()                                        # damaged file: weekdays only, no crash


def test_month_end_uses_the_holiday_calendar():
    from trader.strategies.momentum import last_session_of_month, rebalance_period
    # 30 Oct 2026 is a Friday; if it were a holiday, Thursday 29 Oct is the month's last session
    assert not last_session_of_month(date(2026, 10, 29))
    assert last_session_of_month(date(2026, 10, 29), holidays={date(2026, 10, 30)})
    assert rebalance_period(date(2026, 10, 29), holidays={date(2026, 10, 30)}) == "2026-10"
    assert rebalance_period(date(2026, 10, 29), holidays={date(2026, 10, 30)}, last="2026-10") is None
    assert last_session_of_month(date(2026, 10, 30))                         # no holiday: the Friday
    # replays keep using the exact sessions
    assert last_session_of_month(date(2026, 10, 29), sessions=[date(2026, 10, 29), date(2026, 11, 2)])


def test_engine_gives_live_strategies_the_saved_holidays(tmp_path):
    import json as _json
    from trader.holidays import STATE
    from trader.market import LiveMarket
    (tmp_path / STATE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / STATE).write_text(_json.dumps({"dates": ["2026-10-30"], "checked": "2026-10-26"}))
    c, strats = _momentum_live()
    eng = Engine(c, Journal(tmp_path / "j.db", "live"), FakeLive(), None, Instruments({"AAA": ("1", 0.05)}), None,
                 strats, tmp_path, ["AAA"], quiet=True)
    s = next(s for s in eng.strategies if s.name == "momentum_rotation")
    assert date(2026, 10, 30) in s.holidays and s.due_period(date(2026, 10, 29)) is not None
    lm = LiveMarket.__new__(LiveMarket)
    lm.root = tmp_path
    assert lm.next_session_after(datetime(2026, 10, 29, 16, 10)) == date(2026, 11, 2)   # paper AMOs: the real next session
    assert not lm.is_session(date(2026, 10, 30)) and lm.is_session(date(2026, 10, 29))


def test_holidays_from_trader_yaml_are_robust_and_reach_the_paper_market(tmp_path):
    from trader import holidays as H
    from trader.market import LiveMarket
    assert H.load(tmp_path, {"holidays": date(2026, 10, 30)}) == {date(2026, 10, 30)}          # one date, no list
    assert H.load(tmp_path, {"holidays": ["2026-10-30 00:00:00", "30-Oct-2026", "junk"]}) == {date(2026, 10, 30)}
    assert H.config_problems({"holidays": ["2026-10-30", "junk"]}) == ["junk"]
    assert H.load(tmp_path, {"holidays": {"bad": "shape"}}) == set()                           # never raises
    (tmp_path / H.STATE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / H.STATE).write_text("{broken")
    assert "damaged" in H.staleness(tmp_path, date(2026, 10, 26))
    c, strats = _momentum_live()
    c["holidays"] = ["2026-10-30"]                                                               # not in NSE's file
    lm = LiveMarket.__new__(LiveMarket)
    lm.root = tmp_path
    Engine(c, Journal(tmp_path / "j.db", "paper"), FakeLive(), lm, Instruments({"AAA": ("1", 0.05)}), None,
           strats, tmp_path, ["AAA"], quiet=True)
    assert lm.next_session_after(datetime(2026, 10, 29, 16, 10)) == date(2026, 11, 2)


def test_tranche_review_days_follow_the_trading_calendar():
    from trader.strategies.momentum import MomentumRotation, review_period
    hol = {date(2026, 10, 2), date(2026, 10, 20)}
    due = lambda d, k, last=None: review_period(d, k, None, last, hol)                    # noqa: E731
    assert [d.day for d in (date(2026, 10, x) for x in range(1, 32)) if due(d, 7) and d.weekday() < 5][:1] == [12]
    assert due(date(2026, 10, 22), 14) == "2026-10#s14" and due(date(2026, 10, 21), 14) is None
    assert due(date(2026, 10, 30), "last") == "2026-10#last" and due(date(2026, 10, 29), "last") is None
    assert due(date(2026, 10, 13), 7) == "2026-10#s7"                                   # missed 12 Oct: caught up
    assert due(date(2026, 10, 13), 7, last="2026-10#s7") is None                        # done once only
    assert due(date(2026, 10, 16), 7) is None                                           # too late: wait for November
    assert due(date(2026, 10, 20), 14) is None                                          # a holiday is not a session
    # replays: exact sessions; a short month uses its last session for k
    feb = [date(2026, 2, d) for d in range(2, 28) if date(2026, 2, d).weekday() < 5][:12] + [date(2026, 3, 2)]
    assert review_period(feb[11], 14, feb) == "2026-02#s14"
    s = MomentumRotation("momentum_t07", {"review_session": 7})
    s.holidays = hol
    s.params["_start_now"] = True                                                       # tranches wait for their day
    assert s.due_period(date(2026, 10, 9)) is None and s.due_period(date(2026, 10, 12)) == "2026-10#s7"


def test_lab_settings_are_paper_only_and_allow_tranches_to_share_stocks(tmp_path):
    from trader.risk import Risk
    (tmp_path / "trader").mkdir()
    (tmp_path / "trader" / "experiments.yaml").write_text(
        "capital: 345000\nstrategies:\n  t1: {enabled: true, class: momentum_rotation, live: true, override_gate: true,"
        " capital_share: 0.5, params: {review_session: 7}}\n  off: {enabled: false}\n")
    base = cfg(mode="live")
    lab = C.lab(base, tmp_path)
    assert lab["mode"] == "paper" and list(lab["strategies"]) == ["t1"] and lab["strategies"]["t1"]["live"] is False
    assert lab["capital"]["swing"] == 345000 and lab["_shared_symbols"] and base["mode"] == "live"
    (tmp_path / "trader" / "experiments.yaml").write_text("strategies: [broken")
    assert C.lab(base, tmp_path) is None                                                 # broken file: off, no crash
    assert C.lab(base, tmp_path / "none") is None
    now = datetime(2026, 10, 12, 16, 10)
    held = [Position("t1", "AAA", CNC, 10, 100.0, now, "E", "1")]
    for shared, ok in ((True, True), (False, False)):
        c = cfg(**({"_shared_symbols": True} if shared else {}), strategies={"t2": {"capital_share": 1.0}})
        r = Risk(c, Journal(tmp_path / f"r{shared}.db", "lab"), tmp_path)
        rq = OrderRequest("t2:AAA:BE:1", "t2", "AAA", "1", BUY, 5, CNC, 100.0, "sig", "entry", now, ref_price=100.0,
                          amo=True)
        assert r.check(rq, now, None, held)[0] is ok


def test_lab_engine_failure_never_stops_the_real_engines(monkeypatch):
    import pytest
    from trader import run as R
    calls = []

    class E:
        def __init__(self, mode, fail=False):
            self.mode, self.fail = mode, fail

        def step(self):
            calls.append(self.mode)
            if self.fail:
                raise RuntimeError("boom")

    R._run_each([E("paper"), E("live"), E("lab", fail=True)], lambda e: e.step())
    assert calls == ["paper", "live", "lab"]
    with pytest.raises(RuntimeError):                                                    # real engines still raise
        R._run_each([E("live", fail=True)], lambda e: e.step())


def test_report_shows_the_tranche_experiment(tmp_path):
    from trader.report import build as build_report
    lab = Journal(tmp_path / "j.db", "lab")
    lc = cfg(capital={"swing": 300000}, strategies={"momentum_control": {"capital_share": 1.0},
                                                    "momentum_t07": {"capital_share": 1 / 3}})
    lab.save_position(Position("momentum_control", "AAA", CNC, 100, 100.0, datetime(2026, 10, 30), "E1", "1"))
    lab.save_position(Position("momentum_t07", "AAA", CNC, 100, 100.0, datetime(2026, 10, 12), "E2", "1"))
    text = build_report(cfg(), None, Journal(tmp_path / "j.db", "paper"), {"AAA": 110.0}, pd.Series(dtype=float),
                        "2026-10", False, lab=lab, lab_cfg=lc)
    assert "Tranche A (7th session)" in text and "One portfolio (last session)" in text
    assert "3 tranches together +1.0% vs one portfolio +0.3%" in text


def test_last_session_tranche_waits_for_month_end_and_catches_up_only_briefly():
    from trader.strategies.momentum import review_period
    hol = {date(2026, 10, 2), date(2026, 10, 20)}
    assert review_period(date(2026, 10, 6), "last", None, None, hol) is None            # new: waits for 30 Oct
    assert review_period(date(2026, 10, 6), "last", None, "2026-08#last", hol) == "2026-09#last"   # missed: catch up
    assert review_period(date(2026, 10, 8), "last", None, "2026-08#last", hol) is None  # 4th session: too late
    assert review_period(date(2026, 10, 30), "last", None, "2026-09#last", hol) == "2026-10#last"


def test_lab_is_swing_only_quiet_and_left_alone_by_cancel(tmp_path, monkeypatch):
    from trader import run as R
    (tmp_path / "trader").mkdir()
    (tmp_path / "trader" / "experiments.yaml").write_text(
        "strategies:\n  x: {enabled: true, class: gap_reversal}\n  y: {enabled: true, class: momentum_rotation}\n")
    assert list(C.lab(cfg(), tmp_path)["strategies"]) == ["y"]
    sink = Sink()
    eng = Engine(cfg(), Journal(tmp_path / "j.db", "lab"), FakeLive(), None, None, sink, [], tmp_path, [], quiet=True)
    eng._say("corporate action: run trader.run split", force=True)
    assert not sink.msgs
    lab_o = Order(OrderRequest("lab:AAA:BE:1", "y", "AAA", "1", BUY, 5, CNC, 100.0, "s", "entry",
                               datetime(2026, 10, 12, 16, 10), amo=True), status=OPEN, broker_id="P-1")
    eng.j.save_order(lab_o)
    monkeypatch.setattr(R, "build_engines", lambda c, dry: [eng])
    monkeypatch.setattr(R, "now_ist", lambda: datetime(2026, 10, 12, 17, 0))

    class A:
        dry_run, forget = True, False

    R.cmd_cancel(cfg(), A())
    assert eng.j.order("lab:AAA:BE:1").status == OPEN and not eng.j.get("cancel_all_at")


class _Scripted:
    """A swing strategy whose decisions a test writes by hand: due = {date: period}, buys/sells = {date: [sym]}."""
    engine, min_strength = "swing", float("-inf")

    def __init__(self, name, due=None, buys=None, sells=None):
        self.name, self.params, self.journal, self.sessions, self.holidays = name, {}, None, None, set()
        self.due, self.buys, self.sells = due or {}, buys or {}, sells or {}
        self.committed, self._pending_period = [], None

    def symbols(self, universe):
        return list(universe)

    def slots(self):
        return 10

    def due_period(self, d):
        return self.due.get(d)

    def signals(self, frames, bench, d, held, now):
        self._pending_period = self.due.get(d)                    # as the real strategies do
        out = [Signal(self.name, s, SELL, "exit", 1.0, float(frames[s]["close"].iloc[-1]), now, CNC)
               for s in self.sells.get(d, []) if s in held]
        out += [Signal(self.name, s, BUY, "entry", 1.0, float(frames[s]["close"].iloc[-1]), now, CNC)
                for s in self.buys.get(d, [])]
        return out

    def commit(self, ok=True):
        self.committed.append(ok)
        if ok and self._pending_period:
            self.due = {k: v for k, v in self.due.items() if v != self._pending_period}   # done: not due again
        self._pending_period = None


def _park_engine(tmp_path, broker, strat, funds=None, sid=True):
    daily = {"AAA": _etf_daily(seed=1), "BBB": _etf_daily(seed=2), "NIFTYBEES": _etf_daily(seed=3),
             "LIQUIDCASE": _etf_daily(seed=4)}
    m = ReplayMarket(daily, {})
    c = cfg(capital={"swing": 300_000, "intraday": 0, "live_from_account": False},
            strategies={strat.name: {"enabled": True, "live": True, "override_gate": True, "capital_share": 1.0,
                                     "park": {"symbol": "LIQUIDCASE", "min": 25000}}})
    if funds is not None:
        broker.available_funds = lambda product: funds
    inst = Instruments({"AAA": ("1", 0.05), "BBB": ("2", 0.05), "NIFTYBEES": ("3", 0.01),
                        **({"LIQUIDCASE": ("9", 0.01)} if sid else {})})
    j = Journal(tmp_path / "j.db", "live")
    eng = Engine(c, j, broker, m, inst, Sink(), [strat], tmp_path, ["AAA", "BBB"], quiet=False)
    eng.strategies, eng.exit_only = [strat], []
    strat.journal = j
    return eng, j, m


def _fill_park(j, tag, when):
    o = j.order(tag)
    o.status, o.filled_qty, o.avg_price = FILLED, o.req.qty, o.req.limit_price
    j.save_order(o)
    j.save_position(Position(o.req.strategy, o.req.symbol, CNC, o.req.qty, o.req.limit_price, when, tag, "9"))
    return o


def test_idle_cash_is_parked_and_sold_before_the_next_buys(tmp_path):
    b = FakeLive()
    days = [d.date() for d in _etf_daily().index]
    d1, rv = days[300], days[305]
    s = _Scripted("momentum_rotation", due={rv: "2026-x"}, buys={rv: ["AAA", "BBB"]})
    eng, j, m = _park_engine(tmp_path, b, s, funds=1_000_000)
    eng.swing_plan(datetime.combine(days[303], time(16, 10)))                    # review in 2 sessions: keep cash
    assert not b.sent
    eng.swing_plan(datetime.combine(d1, time(16, 10)))
    park = [r for r in b.sent if r.symbol == "LIQUIDCASE"]
    assert len(park) == 1 and park[0].side == BUY and park[0].amo
    assert 280_000 < park[0].qty * park[0].limit_price < 300_000                 # one order above max_order_value
    o = _fill_park(j, park[0].tag, datetime.combine(days[301], time(9, 15)))
    b.available_funds = lambda product: 5_000                                     # the money is in the ETF now
    eng.swing_plan(datetime.combine(rv, time(16, 10)))                            # review with buys
    sells = [r for r in b.sent if r.side == SELL]
    assert [r.symbol for r in sells] == ["LIQUIDCASE"] and sells[0].qty == o.req.qty
    assert not [r for r in b.sent if r.side == BUY and r.symbol in ("AAA", "BBB")]   # buys wait for the money
    assert s.committed[-1] is False and j.get("momentum_rotation:waiting_buys")["buys"][0]["symbol"] in ("AAA", "BBB")
    # next evening: the ETF is sold, the money is free; the SAME buys go in although the strategy now wants others
    j.delete_position(j.positions(strategy="momentum_rotation")[0])
    so = j.order(sells[0].tag)
    so.status, so.filled_qty = FILLED, so.req.qty
    j.save_order(so)
    b.available_funds = lambda product: 1_000_000
    nxt = days[306]
    s.due[nxt] = "2026-x"
    s.buys[nxt] = ["NIFTYBEES"]
    eng.swing_plan(datetime.combine(nxt, time(16, 10)))
    bought = sorted(r.symbol for r in b.sent if r.side == BUY and r.symbol != "LIQUIDCASE")
    assert bought == ["AAA", "BBB"] and s.committed[-1] is True and not j.get("momentum_rotation:waiting_buys")


def test_a_failed_etf_sale_never_shrinks_the_buys_and_a_demoted_strategy_sells_the_etf(tmp_path):
    b = FakeLive()
    days = [d.date() for d in _etf_daily().index]
    rv = days[305]
    s = _Scripted("momentum_rotation", due={rv: "2026-x"}, buys={rv: ["AAA"]})
    eng, j, m = _park_engine(tmp_path, b, s, funds=20_000)
    j.save_position(Position("momentum_rotation", "LIQUIDCASE", CNC, 2000, 140.0, datetime(2026, 1, 5), "P", "9"))
    b.place_status = REJECTED
    eng.swing_plan(datetime.combine(rv, time(16, 10)))
    assert [r.symbol for r in b.sent] == ["LIQUIDCASE"] and s.committed[-1] is False   # no small AAA buy
    # demoted to exit-only: the ETF is sold even without buys
    b2 = FakeLive()
    s2 = _Scripted("momentum_rotation")
    eng2, j2, _ = _park_engine(tmp_path / "x", b2, s2, funds=0)
    j2.save_position(Position("momentum_rotation", "LIQUIDCASE", CNC, 2000, 140.0, datetime(2026, 1, 5), "P", "9"))
    eng2.strategies, eng2.exit_only = [], [s2]
    eng2.swing_plan(datetime.combine(days[300], time(16, 10)))
    assert [(r.side, r.symbol) for r in b2.sent] == [(SELL, "LIQUIDCASE")]


def test_no_parking_after_the_owner_cancels_until_the_next_order(tmp_path):
    b = FakeLive()
    days = [d.date() for d in _etf_daily().index]
    s = _Scripted("momentum_rotation")
    eng, j, m = _park_engine(tmp_path, b, s, funds=1_000_000)
    j.put("cancel_all_at", datetime.combine(days[299], time(18, 0)).isoformat())
    eng.swing_plan(datetime.combine(days[300], time(16, 10)))
    assert not b.sent


def test_parking_needs_a_listed_symbol_and_only_the_park_order_gets_the_higher_cap(tmp_path):
    from trader.risk import Risk
    b = FakeLive()
    days = [d.date() for d in _etf_daily().index]
    s = _Scripted("momentum_rotation")
    eng, j, m = _park_engine(tmp_path, b, s, funds=1_000_000, sid=False)
    eng.swing_plan(datetime.combine(days[300], time(16, 10)))
    assert not b.sent and any("doesn't list" in t for t in eng.notifier.msgs)
    r = Risk(eng.cfg, j, tmp_path)
    now = datetime.combine(days[300], time(16, 10))
    big = lambda sym: OrderRequest(f"x:{sym}", "momentum_rotation", sym, "1", BUY, 2000, CNC, 100.0, "s", "entry",  # noqa: E731
                                   now, ref_price=100.0, amo=True)
    assert r.check(big("LIQUIDCASE"), now, None, [])[0]
    assert "max_order_value" in r.check(big("AAA"), now, None, [])[1]


def test_parked_cash_is_taxed_apart_and_a_flat_etf_raises_no_fall_alert(tmp_path):
    from trader.report import park_symbols, tax_estimate
    from trader.watch import evaluate
    j = Journal(tmp_path / "j.db", "live")
    c = cfg(strategies={"momentum_rotation": {"park": {"symbol": "LIQUIDCASE"}}, "x": {"park": "liquidbees"}})
    assert park_symbols(c) == {"LIQUIDCASE", "LIQUIDBEES"}
    for sym, px in (("LIQUIDCASE", 101.0), ("AAA", 110.0)):
        p = Position("momentum_rotation", sym, CNC, 100, 100.0, datetime(2026, 5, 4, 9, 15), f"E{sym}", "1")
        j.record_trade(p, 100, px, datetime(2026, 9, 1, 9, 15), 0.0, f"X{sym}", "test")
    tx = tax_estimate(j, date(2026, 9, 30), park_symbols(c))
    assert round(tx["slab"]) == 100 and round(tx["short_term"]) == 1000             # the ETF gain is not equity STCG
    assert evaluate({"LIQUIDCASE": 116.0}, {"LIQUIDCASE": 116.0}, 0.06, [], set()) == []
    assert evaluate({"AAA": 90.0}, {"AAA": 100.0}, 0.0, [], set())                # a real fall still alerts


def test_a_buy_rejected_by_an_indstocks_error_reopens_the_decision_at_most_3_times_a_week(tmp_path):
    b = _Book()
    eng, j, m, days, px0 = _retry_engine(tmp_path, b)
    j.put("momentum_rotation:last_period", "2026-09")

    def buy(tag, created, msg):
        o = Order(OrderRequest(tag, "momentum_rotation", "BBB", "2", BUY, 10, CNC, 100.0, "sig", "entry", created,
                               amo=True), status=OPEN, broker_id="EQ-" + tag)
        j.save_order(o)
        b.next[tag] = (REJECTED, msg)
        return o

    t0 = datetime.combine(days[1], time(16, 10))
    buy("b1", t0, "We experienced problem in placing your trade. Please try again in sometime while we fix!")
    eng.sync(datetime.combine(days[2], time(9, 30)))
    assert j.get("momentum_rotation:last_period") is None
    assert any("decides again at this evening's plan" in t for t in eng.notifier.msgs)
    j.put("momentum_rotation:last_period", "2026-09")
    buy("b2", t0, "Insufficient funds")                                                   # the order's own problem
    eng.sync(datetime.combine(days[2], time(9, 31)))
    assert j.get("momentum_rotation:last_period") == "2026-09"
    for k in range(3, 6):                                                                 # 3 a week at most
        j.put("momentum_rotation:last_period", "2026-09")
        buy(f"b{k}", t0, "technical error, try again")
        eng.sync(datetime.combine(days[2], time(10, k)))
    assert j.get("momentum_rotation:last_period") == "2026-09" and len(j.get("momentum_rotation:reopened")) == 3


def test_nse_index_closes_are_read_kept_and_judged(tmp_path):
    from trader import index_data as X
    text = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value\n"
            "Nifty 50,05-10-2026,1,1,1,22421.95\nNIFTY100 Quality 30,05-10-2026,1,1,1,\"5,512.30\"\n")
    assert X.parse(text) == {"nifty50": 22421.95, "nifty100quality30": 5512.30}

    class R:
        status_code = 200

    R.text = text
    calls = []
    msg = X.update(tmp_path, date(2026, 10, 6), get=lambda url: calls.append(url) or R(), max_days=3)
    s = X.load(tmp_path, "quality30")
    assert len(calls) == 2 and len(s) == 2 and s.iloc[-1] == 5512.30 and "quality30: 2 days" in msg   # Mon, Tue
    idx = pd.bdate_range("2026-01-01", periods=200)
    up = pd.Series(np.linspace(100, 120, 200), index=idx)
    assert X.above_average(up, idx[-1].date(), 150) is True
    assert X.above_average(up[::-1].set_axis(idx), idx[-1].date(), 150) is False
    assert X.above_average(up, (idx[-1] + pd.Timedelta(days=10)).date(), 150) is None             # stale
    assert X.above_average(up[:100], idx[99].date(), 150) is None                                 # too short


def test_momentum_goes_to_cash_when_the_quality_index_is_weak(tmp_path):
    from trader import index_data as X
    from trader.strategies.momentum import MomentumRotation
    daily = {s: _etf_daily(seed=i, up=True) for i, s in enumerate(["AAA", "BBB", "CCC"])}
    bench = _etf_daily(seed=9, up=True)
    d = bench.index[-1].date()
    held = {"AAA": Position("q", "AAA", CNC, 10, 100.0, datetime(2026, 1, 5), "E", "1")}
    s = MomentumRotation("q", {"review_session": "last", "_force": True,
                               "quality_filter": {"index": "quality30", "days": 150}})
    s.root = tmp_path
    p = X._path(tmp_path, "quality30")
    p.parent.mkdir(parents=True)
    pd.DataFrame({"date": bench.index, "close": np.linspace(120, 100, len(bench))}).to_csv(p, index=False)
    sig = s.signals(daily, bench, d, held, datetime.combine(d, time(16, 10)))
    assert [x.side for x in sig] == [SELL] and "Quality 30" in sig[0].reason
    pd.DataFrame({"date": bench.index, "close": np.linspace(100, 120, len(bench))}).to_csv(p, index=False)
    sig = s.signals(daily, bench, d, held, datetime.combine(d, time(16, 10)))
    assert not [x for x in sig if x.side == SELL and "Quality" in x.reason]
