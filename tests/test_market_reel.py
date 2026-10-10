"""The weekday market wrap (trader/reel/market.py): NSE snapshot, the trusted-news rule, history lines, the brief,
the MARKET AAJ slot and the evening deep-dive on the day's top news."""
import json
from datetime import date
from types import SimpleNamespace

from trader.reel import job, market, script as S

DAY = date(2026, 10, 9)


def _ix(name, last, pct, adv=None, dec=None):
    return {"key": "X", "index": name, "last": last, "percentChange": pct, "advances": adv, "declines": dec,
            "pe": "19.27", "dy": "1.52", "perChange30d": -3.9, "perChange365d": -10.6, "yearHigh": 1, "yearLow": 1}


FAKE = {
    "allIndices": {"timestamp": "09-Oct-2026 15:30", "data": [
        _ix("NIFTY 50", 22520.45, 1.3, "46", "4"), _ix("NIFTY BANK", 55256.65, 1.36),
        _ix("NIFTY MIDCAP 150", 21603.25, 1.37), _ix("NIFTY SMALLCAP 250", 17574.85, 0.49),
        _ix("NIFTY 500", 21866.65, 1.16, "340", "157"), _ix("INDIA VIX", 14.36, -6.0),
        _ix("NIFTY IT", 28574.9, 3.02), _ix("NIFTY PHARMA", 1, 0.54), _ix("NIFTY OIL & GAS", 1, -0.09)]},
    "fiidiiTradeReact": [{"category": "DII", "date": "09-Oct-2026", "netValue": "4743.26"},
                         {"category": "FII/FPI", "date": "09-Oct-2026", "netValue": "-3568.9"}],
    "event-calendar": [],
}


def _patch_nse(monkeypatch, fake=FAKE):
    import pandas as pd
    monkeypatch.setattr(market, "nse_session", lambda: None)
    monkeypatch.setattr(market, "_get", lambda s, path: fake.get(path))
    closes = pd.Series([20000 + 10 * i for i in range(250)], dtype=float)          # a steady climb
    monkeypatch.setattr(market, "index_history", lambda path, today, closes_now, fetch=None, holidays=():
                        pd.DataFrame({"nifty50": closes.values, "niftybank": closes.values * 2}))
    monkeypatch.setattr(market, "options_sentiment", lambda d: {"expiry": "2026-10-13", "pcr": 1.28,
                                                                "call_wall": 23000.0, "put_wall": 22500.0})


def test_snapshot_reads_closing_values_breadth_and_flows():
    snap = market.snapshot(lambda p: FAKE.get(p))
    assert snap["date"] == DAY and snap["fii"] == -3568.9 and snap["dii"] == 4743.26
    assert snap["indices"]["NIFTY 500"]["adv"] == 340 and snap["indices"]["NIFTY IT"]["pct"] == 3.02


def test_trusted_rule_needs_an_official_source_or_two_outlets_that_were_actually_retrieved():
    def item(urls):
        return market.NewsItem(headline="h", facts="f", why_it_matters="w", sector="IT", companies=["Infosys"],
                               source_urls=urls)
    got = {"https://www.dol.gov/newsroom/x", "https://www.reuters.com/a", "https://www.livemint.com/b",
           "https://www.reuters.com/c"}
    kept = market.trusted([item(["https://www.dol.gov/newsroom/x"]),                     # official: kept
                           item(["https://www.reuters.com/a", "https://www.livemint.com/b"]),  # two outlets: kept
                           item(["https://www.reuters.com/a", "https://www.reuters.com/c"]),   # one outlet twice: no
                           item(["https://www.dol.gov/not-retrieved"]),                  # never retrieved: no
                           item(["https://randomblog.example/x"])], got)                 # not on the list: no
    assert [k["official"] for k in kept] == [True, False]


def test_history_line_quotes_a_pattern_only_where_the_study_found_one():
    assert "no reliable pattern" in market.history_line("NIFTY 50", 1.3)
    hit = market.history_line("NIFTY MIDCAP 150", -1.0)                      # a consistent cell of Addendum 23
    assert "rose 46% of the time" in hit and "not a prediction" in hit


def test_compile_day_and_brief(tmp_path, monkeypatch):
    _patch_nse(monkeypatch)
    assert market.compile_day(date(2026, 10, 10), tmp_path, search=False) is None    # Saturday: no session
    day = market.compile_day(DAY, tmp_path, search=False)
    assert day["sectors"][-1] == ("IT", 3.02) and day["next_session"] == "2026-10-12"
    text = market.brief(day)
    assert "Nifty 50: 22,520.45 (+1.30%)" in text and "sold ₹3,569 crore net" in text and "IT +3.02%" in text
    assert market.parts("a" * 3000 + "\n\n" + "b" * 3000) == ["a" * 3000, "b" * 3000]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert market.web_news(DAY)["note"] == "no ANTHROPIC_API_KEY"
    stale = dict(FAKE, allIndices=dict(FAKE["allIndices"], timestamp="08-Oct-2026 15:30"))
    _patch_nse(monkeypatch, stale)
    assert market.compile_day(DAY, tmp_path, search=False) is None                    # NSE not updated yet


class FakeSearchClient:
    """beta.messages.create: a paused search turn, then the notes; beta.messages.parse: the structured items."""
    def __init__(self):
        self.calls = []
        r = SimpleNamespace(type="web_search_tool_result",
                            content=[SimpleNamespace(url="https://www.dol.gov/newsroom/perm"),
                                     SimpleNamespace(url="https://www.reuters.com/x")])
        self.turns = [SimpleNamespace(stop_reason="pause_turn", content=[r]),
                      SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="notes")])]
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create, parse=self.parse))

    def create(self, **kw):
        self.calls.append(kw)
        return self.turns.pop(0)

    def parse(self, **kw):
        self.calls.append(kw)
        items = [market.NewsItem(headline="US suspends PERM green-card filings of 8 tech firms", facts="On 8 Oct ...",
                                 why_it_matters="Indian IT firms sponsor many US employees", sector="IT",
                                 companies=["Infosys", "TCS"], source_urls=["https://www.dol.gov/newsroom/perm"]),
                 market.NewsItem(headline="rumour", facts="x", why_it_matters="y", sector="Other", companies=[],
                                 source_urls=["https://www.reuters.com/x"])]
        drivers = [market.Driver(text="IT index rose as the rupee weakened, reports said", sector="IT",
                                 source_urls=["https://www.reuters.com/x"]),
                   market.Driver(text="Infosys rose 4% after the news", sector="IT",           # company + move: dropped
                                 source_urls=["https://www.reuters.com/x"]),
                   market.Driver(text="Banks will rally tomorrow", sector="Bank",               # forecast: dropped
                                 source_urls=["https://www.reuters.com/x"]),
                   market.Driver(text="Metals fell on China data", sector="Metal",            # not retrieved: dropped
                                 source_urls=["https://www.reuters.com/never"])]
        cues = [market.Cue(what="Brent crude", value="$61.2 a barrel on 9 Oct", source_urls=["https://www.reuters.com/x"])]
        return SimpleNamespace(parsed_output=market.NewsList(items=items, drivers=drivers, cues=cues))


def test_web_news_searches_only_trusted_sites_resumes_a_paused_turn_and_applies_the_rule():
    c = FakeSearchClient()
    web = market.web_news(DAY, c, context="Nifty 50 +1.30%; IT +3.02%")
    items, note = web["items"], web["note"]
    tool = c.calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209" and "dol.gov" in tool["allowed_domains"]
    assert c.calls[0]["model"] == "claude-opus-5-5" and c.calls[0]["fallbacks"] == "default"
    assert len(c.calls) == 3 and c.calls[1]["messages"][1]["role"] == "assistant"    # resumed after pause_turn
    assert [i["headline"][:7] for i in items] == ["US susp"] and "IT +3.02%" in c.calls[0]["messages"][0]["content"]
    assert [d["text"][:8] for d in web["drivers"]] == ["IT index"] and web["cues"][0]["what"] == "Brent crude"
    assert note.startswith("1 of 2 news items, 1 of 4 reasons and 1 of 1 global cues passed")


def test_market_slot_sends_the_brief_and_a_reel_and_the_evening_explains_the_top_news(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    _patch_nse(monkeypatch)
    news = [{"headline": "US suspends PERM filings", "facts": "f", "why_it_matters": "w", "sector": "IT",
             "companies": ["Infosys"], "source_urls": ["https://www.dol.gov/x"], "official": True}]
    monkeypatch.setattr(market, "web_news", lambda d, c=None, max_searches=10, context="": {
        "items": news, "note": "ok", "cues": [{"what": "Brent crude", "value": "$61", "source_urls": ["https://reuters.com/x"]}],
        "drivers": [{"text": "IT rose as the rupee fell", "sector": "IT", "source_urls": ["https://reuters.com/x"]}]})
    said, videos = [], []
    msg = job.run(said.append, lambda p, c: videos.append(p) or True, today=DAY, out_dir=tmp_path,
                  store=tmp_path / "none", slot="market")
    assert msg.startswith("reel (market): sent market") and videos
    assert said[0].startswith("📊 DAILY MARKET BRIEF") and "US suspends PERM filings [official source]" in said[0]
    assert "CHART READING" in said[0] and "above all three averages" in said[0] and "put-call ratio" in said[0]
    assert "WHY IT MOVED (as reported)" in said[0] and "Brent crude: $61 (reuters.com)" in said[0]
    assert "MARKET AAJ  •  EP 1" in said[1]
    saved = json.loads((tmp_path / "market_20261009.json").read_text())
    assert saved["news"][0]["sector_today"] == 3.02
    f = job.facts_for(DAY, "evening", {}, tmp_path / "none", out_dir=tmp_path)
    assert f["format"] == "news" and f["macro"]["headline"] == "US suspends PERM filings"
    assert job.run(said.append, lambda p, c: True, today=date(2026, 10, 10), out_dir=tmp_path,
                   slot="market") == "reel (market): no market session today, or NSE's closing data is not out yet"


def test_a_company_never_appears_with_a_move_in_the_market_wrap():
    sc = S.ReelScript(title="t", caption="c", hashtags=[], scenes=[
        S.Scene(kind="hook", narration="IT index aaj 3% upar gaya.", on_screen="IT +3%"),
        S.Scene(kind="news", narration="Infosys ke shares 4% chadhe.", on_screen="Infosys"),
        *[S.Scene(kind="explain", narration="FII matlab videshi investors jo Indian shares khareedte bechte hain. " * 2,
                  on_screen="FII") for _ in range(6)],
        S.Scene(kind="question", narration="Aapko kya lagta hai? Comment karo.", on_screen="Comment")])
    issues = S.check(sc, ["Infosys"], strict=True)
    assert any("move (%) next to a named company" in i for i in issues)
    assert not any("scene 1" in i for i in issues)                          # index moves are fine


def test_chart_facts_describe_the_index_and_the_poll_is_revealed_next_session(tmp_path):
    import pandas as pd
    s = pd.Series([100.0 + i for i in range(220)] + [318.0, 317.0, 316.0])
    f = market.chart_facts("Nifty 50", s, 330.0, 100.0)
    lines = " ".join(market.chart_lines(f))
    assert "above all three averages" in lines and "closed down 3 sessions in a row" in lines and "overbought" in lines
    text, polls = market.poll_reveal([{"asked": "2026-10-08"}], DAY, 1.3)
    assert "Answer: UP (+1.30%)" in text and "closed up 1 of 1" in text and polls[-1]["result"] == "UP"
    assert market.poll_reveal(polls, DAY, 1.3)[0] is None                    # answered once only
    assert market.poll_reveal([{"asked": "2026-10-09"}], DAY, 1.3)[0] is None  # asked today: not yet


def test_index_history_backfills_once_from_nse_daily_files(tmp_path):
    from trader import index_data as I
    calls = []

    def fetch(url):
        calls.append(url)
        return SimpleNamespace(status_code=200, text="Index Name,Closing Index Value\nNifty 50,22000\nNifty Bank,50000\n")
    h = market.index_history(tmp_path / "h.csv", DAY, {"NIFTY 50": 22520.45, "NIFTY BANK": 55256.65}, fetch,
                             sessions=12)
    assert len(calls) == 12 and len(h) == 13 and h["nifty50"].iloc[-1] == 22520.45
    assert I.URL.format(d=date(2026, 10, 8)) in calls
