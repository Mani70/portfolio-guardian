"""Broker client tests against a local fake INDstocks server (no network, no real account)."""
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import guardian.broker as b

STATE = {}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        STATE["generate_calls"] = STATE.get("generate_calls", 0) + 1
        if STATE.get("block_login"):
            self.send_response(429)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>")
            return
        assert self.headers["x-api-key"] == "client-1"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["mpin"] == "1234" and len(body["totp"]) == 6
        tok = f"gen-{STATE['generate_calls']}"
        STATE["valid"] = {tok}
        self._send(200, {"status": "success", "data": {"token": tok}})

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        STATE.setdefault("calls", []).append((u.path, q))
        if self.headers.get("Authorization") not in STATE.get("valid", set()):
            return self._send(403, {"status": "error", "message": "invalid token", "error_type": "TokenException"})
        flaky = STATE.get("flaky", 0)
        if flaky:
            STATE["flaky"] = flaky - 1
            return self._send(503, {"status": "error", "error_type": "NetworkException", "message": "upstream"})
        if u.path == "/portfolio/holdings":
            return self._send(200, {"status": "success", "data": [
                {"security_id": "1660", "symbol": "itc", "total_qty": 94, "avg_price": 269.05},
                {"security_id": "9", "symbol": "SOLD", "total_qty": 0, "avg_price": 1}]})
        if u.path == "/market/quotes/ltp":
            codes = q["scrip-codes"][0].split(",")
            valid = STATE.get("valid_codes")
            if valid is not None and any(c not in valid for c in codes):
                return self._send(400, {"message": "Invalid scrip codes or mode", "success": False})
            return self._send(200, {"status": "success",
                                    "data": {c: {"live_price": 100.0} for c in codes if c.startswith("NSE_")}})
        if u.path == "/market/instruments":
            csv = ("EXCH,SEGMENT,SECURITY_ID,INSTRUMENT_NAME,EXPIRY_CODE,TRADING_SYMBOL,LOT_UNITS,CUSTOM_SYMBOL,"
                   "EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE,EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SERIES,SYMBOL_NAME\n"
                   "NSE,E,1660,EQUITY,0,ITC,1,ITC,,,,0.05,,ES,EQ,ITC\n"
                   "NSE,E,777,EQUITY,0,BADX-EQ,1,BADX,,,,0.05,,ES,EQ,BADX\n"
                   "BSE,E,500875,EQUITY,0,ITC,1,ITC,,,,0.05,,ES,A,ITC\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.end_headers()
            self.wfile.write(csv.encode())
            return
        if u.path.startswith("/market/historical/"):
            if u.path.endswith("/2day"):
                return self._send(400, {"message": "Bad request", "debug_info": "invalid interval."})
            codes = q["scrip-codes"][0].split(",")
            assert len(codes) <= 5
            if "NSE_REJECT" in codes:
                return self._send(400, {"message": "Bad Request", "debug_info": "Invalid scrip codes"})
            return self._send(200, {"success": True, "data": {
                c: ({"candles": None} if c == "NSE_NULL" else
                    {"candles": [{"ts": 1790000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]}) for c in codes}})
        if u.path == "/user/profile":
            return self._send(200, {"status": "success", "data": {"first_name": "Mani", "ucc": "X1"}})
        self._send(404, {"status": "error", "error_type": "NotFoundException", "message": "nope"})


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = b.BASE_URL
    b.BASE_URL = f"http://127.0.0.1:{srv.server_port}"
    b._MIN_INTERVAL_S = 0
    sys.modules.setdefault("pyotp", types.SimpleNamespace(TOTP=lambda s: types.SimpleNamespace(now=lambda: "123456")))
    yield
    b.BASE_URL = old
    srv.shutdown()


def reset(valid=("tok",)):
    STATE.clear()
    STATE["valid"] = set(valid)


def test_holdings_quotes_and_batched_candles():
    reset()
    c = b.IndStocksClient("tok")
    hs = c.holdings()
    assert [h.symbol for h in hs] == ["ITC"]           # zero-qty rows dropped, symbol upper-cased
    assert c.ltp(["NSE_1660"]) == {"NSE_1660": 100.0}
    codes = [f"NSE_{i}" for i in range(7)]
    out = c.candles_many(codes, "1day", 0, 1)
    assert set(out) == set(codes)
    hist_calls = [p for p, _ in STATE["calls"] if p.startswith("/market/historical")]
    assert len(hist_calls) == 2                         # 7 codes -> batches of 5 + 2


def test_error_shapes_surface_the_real_reason():
    reset()
    c = b.IndStocksClient("tok")
    with pytest.raises(b.BrokerError) as e:
        c.candles("NSE_1", "2day", 0, 1)
    assert "invalid interval" in str(e.value)
    with pytest.raises(b.BrokerError) as e:
        c._get("/missing")
    assert "NotFoundException" in str(e.value)


def test_env_token_rejected_is_not_retried():
    reset(valid=())
    c = b.IndStocksClient("stale")
    with pytest.raises(b.TokenError) as e:
        c.holdings()
    assert "dashboard" in str(e.value)
    assert len(STATE["calls"]) == 1


def test_5xx_is_retried():
    reset()
    STATE["flaky"] = 1
    assert b.IndStocksClient("tok").profile()["first_name"] == "Mani"


def test_totp_token_is_cached_and_reused(tmp_path: Path):
    reset(valid=())
    cache = tmp_path / "tok.json"
    kw = dict(client_id="client-1", mpin="1234", totp_secret="SECRET", token_cache=cache)
    b.IndStocksClient(**kw).holdings()
    assert STATE["generate_calls"] == 1 and cache.exists()
    b.IndStocksClient(**kw).holdings()                  # second run reuses the cached token
    assert STATE["generate_calls"] == 1


def test_rejected_cached_token_regenerates_once(tmp_path: Path):
    import time as _t
    reset(valid=())                                     # server no longer accepts the cached token
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"token": "old", "generated_at": _t.time() - 70}))   # after today's 7 AM reset
    kw = dict(client_id="client-1", mpin="1234", totp_secret="SECRET", token_cache=cache)
    c = b.IndStocksClient(**kw)
    assert [h.symbol for h in c.holdings()] == ["ITC"]  # rejected -> regenerated -> succeeded
    assert STATE["generate_calls"] == 1
    STATE["valid"] = set()                              # replaced again in the same run
    with pytest.raises(b.TokenError):
        c.holdings()                                    # no second regeneration
    assert STATE["generate_calls"] == 1


def test_two_jobs_share_a_new_token_instead_of_cancelling_each_other(tmp_path: Path):
    """INDstocks keeps one token per account: a login cancels the previous token. Two all-day jobs (watch,
    intraday) must not take turns logging in."""
    reset(valid=())
    cache = tmp_path / "tok.json"
    kw = dict(client_id="client-1", mpin="1234", totp_secret="SECRET", token_cache=cache)
    watch, intraday = b.IndStocksClient(**kw), b.IndStocksClient(**kw)
    watch.holdings()                                    # logs in (gen-1); intraday reads it from the cache
    intraday.holdings()
    assert STATE["generate_calls"] == 1
    STATE["valid"] = set()                              # cancelled from outside (e.g. a login elsewhere)
    cache.write_text(json.dumps({"token": "gen-1", "generated_at": json.loads(cache.read_text())["generated_at"] - 120}))
    watch.holdings()                                    # rejected -> logs in once (gen-2)
    assert STATE["generate_calls"] == 2
    intraday.holdings()                                 # rejected -> takes gen-2 from the cache, no new login
    watch.holdings()
    assert STATE["generate_calls"] == 2 and watch._token == intraday._token == "gen-2"


def test_no_second_login_within_ten_minutes_but_later_yes(tmp_path: Path, monkeypatch):
    import time as _t
    reset(valid=())
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"token": "old", "generated_at": _t.time() - 120}))
    c = b.IndStocksClient(client_id="client-1", mpin="1234", totp_secret="SECRET", token_cache=cache)
    c.holdings()
    assert STATE["generate_calls"] == 1
    STATE["valid"] = set()
    cache.write_text(json.dumps({"token": "gen-1", "generated_at": _t.time() - 120}))
    with pytest.raises(b.TokenError) as e:
        c.holdings()
    assert "10 minutes" in str(e.value) and STATE["generate_calls"] == 1
    c._last_generated -= 601                            # ten minutes later
    c.holdings()
    assert STATE["generate_calls"] == 2


def test_refused_login_pauses_every_job_for_15_minutes(tmp_path: Path):
    import time as _t
    reset(valid=())
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"token": "old", "generated_at": _t.time() - 120}))
    kw = dict(client_id="client-1", mpin="1234", totp_secret="SECRET", token_cache=cache)
    STATE["block_login"] = True
    with pytest.raises(b.TokenError) as e:
        b.IndStocksClient(**kw).holdings()
    assert "rate-limited" in str(e.value) and "15 minutes" in str(e.value) and STATE["generate_calls"] == 1
    for _ in range(3):                                  # other jobs, later polls: no further login attempts
        with pytest.raises(b.TokenError) as e:
            b.IndStocksClient(**kw).holdings()
        assert "paused until" in str(e.value)
    assert STATE["generate_calls"] == 1
    STATE["block_login"] = False
    bo = cache.with_name(cache.name + ".backoff")
    bo.write_text(json.dumps({"until": _t.time() - 1, "why": "x"}))   # 15 minutes later
    assert [h.symbol for h in b.IndStocksClient(**kw).holdings()] == ["ITC"] and STATE["generate_calls"] == 2


def test_cached_token_dies_at_7am_ist():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    ts = lambda *a: datetime(*a, tzinfo=ist).timestamp()
    made = ts(2026, 10, 5, 15, 45)                                   # Monday after the close
    assert b._cached_token_usable(made, ts(2026, 10, 5, 20, 0))      # same evening: fine
    assert b._cached_token_usable(made, ts(2026, 10, 6, 6, 59))      # before reset: fine
    assert not b._cached_token_usable(made, ts(2026, 10, 6, 7, 1))   # after 7 AM reset: stale
    assert not b._cached_token_usable(made, ts(2026, 10, 6, 15, 45))


def test_one_bad_code_does_not_sink_the_batch():
    reset()
    STATE["valid_codes"] = {"NSE_1", "NSE_2"}
    prices = b.IndStocksClient("tok").ltp(["NSE_1", "NSE_BAD", "NSE_2"])
    assert prices == {"NSE_1": 100.0, "NSE_2": 100.0}


def test_gather_resolves_bad_holding_ids_via_instrument_master(tmp_path: Path):
    import guardian.main as m
    from guardian.broker import Holding
    from datetime import datetime
    from guardian.rules import IST
    reset()
    STATE["valid_codes"] = {"NSE_1660", "NSE_777"}
    c = b.IndStocksClient("tok")
    c.holdings = lambda: [Holding("ITC", "1660", 94, 269.05), Holding("BADX", "999", 10, 50.0)]
    old_root, m.ROOT = m.ROOT, tmp_path
    try:
        md = m.gather(c, [], datetime(2026, 10, 3, 17, 0, tzinfo=IST))
    finally:
        m.ROOT = old_root
    assert md.scrip_of == {"ITC": "NSE_1660", "BADX": "NSE_777"}
    assert md.ltp["NSE_777"] == 100.0
    assert list((tmp_path / "cache").glob("equity_index_*.json"))     # cached for the day


def test_candles_tolerate_null_and_bad_codes():
    reset()
    out = b.IndStocksClient("tok").candles_many(["NSE_1", "NSE_NULL", "NSE_REJECT", "NSE_2"], "1day", 0, 1)
    assert out["NSE_NULL"] == [] and len(out["NSE_1"]) == 1 and len(out["NSE_2"]) == 1
    assert "NSE_REJECT" not in out


def test_find_token_handles_either_envelope():
    assert b._find_token({"data": {"token": "a"}}) == "a"
    assert b._find_token({"token": "b"}) == "b"
    assert b._find_token({"data": {"access_token": "c"}}) == "c"
    assert b._find_token({"data": []}) is None
