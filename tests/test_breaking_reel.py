"""BREAKING SAMJHO (trader/reel/breaking.py): official sources only, Claude reads the document, a daily cap."""
import json
from datetime import datetime
from types import SimpleNamespace

from trader.reel import breaking

NOW = datetime(2026, 10, 12, 16, 5)
SIZE = {"HCLTECH": 900.0, "TCS": 2000.0, "TINYCO": 5.0}


def _ann(seq, sym, desc, text, t="12-Oct-2026 16:01:00"):
    return {"seq_id": seq, "symbol": sym, "sm_name": sym + " Ltd", "desc": desc, "attchmntText": text,
            "attchmntFile": f"https://nsearchives.nseindia.com/corporate/{seq}.pdf", "an_dt": t}


ANN = [_ann("1", "HCLTECH", "Financial Result Updates", "HCLTECH has informed the Exchange about Financial Results"),
       _ann("2", "TCS", "Copy of Newspaper Publication", "Newspaper publication"),           # routine: ignored
       _ann("3", "TINYCO", "Bagging/Receiving of orders/contracts", "order worth 5 crore"),  # too small: ignored
       _ann("4", "TCS", "Updates", "Statement of the Company regarding the announcement")]  # checked by Claude first


def get(path):
    return ANN if path.startswith("corporate-announcements") else None


RSS = b"""<rss><channel>
<item><title>RBI keeps repo rate unchanged at 5.50%</title><link>https://www.rbi.org.in/pr/1</link></item>
<item><title>RBI felicitates staff on sports day</title><link>https://www.rbi.org.in/pr/2</link></item>
</channel></rss>"""


def fetch(url):
    return SimpleNamespace(status_code=200, content=RSS)


def test_sources_pick_well_known_companies_and_market_words_only():
    c = breaking.nse_new(get, set(), NOW.date(), SIZE)
    assert [(x["symbol"], x["kind"]) for x in c] == [("HCLTECH", "results"), ("TCS", "check")]
    f = breaking.feed_new({"RBI": "u"}, set(), fetch)
    assert [x["title"] for x in f] == ["RBI keeps repo rate unchanged at 5.50%"]
    assert sorted(c + f, key=breaking.priority)[0]["symbol"] == "HCLTECH"            # results first


def test_watch_learns_first_reads_the_document_caps_the_day_and_alerts_the_rest(monkeypatch):
    reads = []

    def fake_read(c, client=None):
        reads.append(c["id"])
        return {"material": c["kind"] != "check", "headline": f"{c['company']} news", "sector": "IT",
                "points": ["Revenue ₹30,000 crore, up 5% from a year earlier", "Net profit ₹4,000 crore, up 7%"],
                "why_it_matters": "IT is a big part of the Nifty", "companies": [c["company"]], "url": c["url"],
                "source": c["source"]}
    monkeypatch.setattr(breaking, "read", fake_read)
    chosen, alerts, st = breaking.watch({"seen": ["nse:4"]}, NOW, get, SIZE, {"RBI": "u"}, fetch=fetch)
    assert chosen["results"]["company"] == "HCLTECH Ltd" and chosen["series"] == "BREAKING SAMJHO"
    assert chosen["results"]["points"][0]["source_urls"] == ["https://nsearchives.nseindia.com/corporate/1.pdf"]
    assert reads == ["nse:1"] and any("repo rate" in a for a in alerts)               # RBI: alert (30-minute gap)
    again, _, st2 = breaking.watch(st, NOW, get, SIZE, {"RBI": "u"}, fetch=fetch)
    assert again is None and len(reads) == 1                                          # nothing new, nothing read
    capped, alerts3, _ = breaking.watch({"made": [f"2026-10-12T0{h}:00" for h in (9, 10, 11)]}, NOW, get, SIZE, {},
                                        fetch=fetch)
    assert capped is None and any("HCLTECH" in a for a in alerts3)                   # over the cap: alert only


def test_run_starts_quietly_then_makes_and_sends_a_breaking_reel(tmp_path, monkeypatch):
    monkeypatch.setattr(breaking, "read", lambda c, client=None: {
        "material": True, "headline": "HCL Tech results", "sector": "IT", "companies": ["HCL Technologies"],
        "points": ["Revenue up 5% from a year earlier", "Profit up 7%"], "why_it_matters": "w", "url": c["url"],
        "source": c["source"]})
    said, made = [], []
    first = breaking.run(said.append, lambda p, c: True, made.append, now=NOW, out_dir=tmp_path, get=get, size=SIZE,
                         feeds={})
    assert first.startswith("breaking: started watching") and not said and not made   # old news is not breaking
    ANN.append(_ann("5", "HCLTECH", "Financial Result Updates", "Results for the quarter", "12-Oct-2026 16:20:00"))
    try:
        msg = breaking.run(said.append, lambda p, c: True,
                           lambda f: made.append(f) or {"video": "v.mp4", "caption": "cap", "script_source": "claude",
                                                        "voice": "elevenlabs"},
                           now=NOW.replace(minute=25), out_dir=tmp_path, get=get, size=SIZE, feeds={})
    finally:
        ANN.pop()
    assert msg.startswith("breaking: sent") and made[0]["breaking"] and "Caption" in said[0]


def test_freshness_two_hours_by_day_and_overnight_news_until_the_morning():
    c = lambda t: {"time": t}                                                       # noqa: E731
    assert breaking.fresh(c("12-Oct-2026 14:30:00"), datetime(2026, 10, 12, 16, 0))          # 90 minutes old
    assert not breaking.fresh(c("12-Oct-2026 13:00:00"), datetime(2026, 10, 12, 16, 0))      # 3 hours old
    assert breaking.fresh(c("12-Oct-2026 02:10:00"), datetime(2026, 10, 12, 8, 45))          # overnight, before 09:30
    assert not breaking.fresh(c("12-Oct-2026 02:10:00"), datetime(2026, 10, 12, 10, 0))
    assert breaking.fresh(c("11-Oct-2026 23:40:00"), datetime(2026, 10, 12, 9, 0))           # late-night news
    rss = {"time": "Mon, 12 Oct 2026 10:00:00 GMT"}                                          # 15:30 in India
    assert breaking.published(rss) == datetime(2026, 10, 12, 15, 30)


def test_story_cards_go_out_before_the_reel_and_stale_news_gets_no_reel(tmp_path, monkeypatch):
    monkeypatch.setattr(breaking, "read", lambda c, client=None: {
        "material": True, "headline": "HCL Tech Q2: profit up 7%", "sector": "IT", "companies": ["HCL Technologies"],
        "points": ["Revenue ₹30,000 crore, up 5%", "Net profit ₹4,000 crore, up 7%"], "why_it_matters": "w",
        "url": c["url"], "source": c["source"]})
    (tmp_path / "breaking.json").write_text('{"seen": [], "made": [], "episode": 0}')
    photos, order = [], []
    msg = breaking.run(lambda t: order.append("text"), lambda p, c: order.append("video") or True,
                       lambda f: order.append("make") or {"video": "v", "caption": "c", "script_source": "claude",
                                                          "voice": "elevenlabs"},
                       now=NOW, out_dir=tmp_path, get=get, size=SIZE, feeds={},
                       send_photo=lambda p, c: photos.append(p) or order.append("photo"))
    assert msg.startswith("breaking: sent") and order[:4] == ["photo", "photo", "make", "video"]
    assert all(p.exists() for p in photos)                                    # ABHI ABHI card, then KYA HUA card
    (tmp_path / "breaking.json").write_text('{"seen": [], "made": [], "episode": 0}')
    late = breaking.run(lambda t: None, lambda p, c: True, lambda f: 1 / 0, now=NOW.replace(hour=19), out_dir=tmp_path,
                        get=get, size=SIZE, feeds={}, send_photo=lambda p, c: True)
    assert late.startswith("breaking: nothing made")                          # 3 hours old: alert only


def test_catch_up_search_makes_a_reel_for_major_missed_news_and_logs_the_rest(tmp_path, monkeypatch):
    found = [{"headline": "US suspends PERM filings of 8 tech firms", "facts": "On 8 Oct the US Labor Department ...",
              "why_it_matters": "Indian IT firms sponsor many US staff", "sector": "IT", "companies": ["Infosys"],
              "source_urls": ["https://www.dol.gov/newsroom/x"], "official": True, "importance": "major"},
             {"headline": "Minor update", "facts": "f", "why_it_matters": "w", "sector": "Other", "companies": [],
              "source_urls": ["https://www.reuters.com/y"], "official": False, "importance": "notable"}]
    asked = {}
    monkeypatch.setattr(breaking, "sweep", lambda now, covered, client=None: asked.setdefault("covered", covered) and []
                        or found)
    (tmp_path / "breaking.json").write_text(json.dumps({"seen": ["nse:1", "nse:2", "nse:3", "nse:4"], "made": [],
        "episode": 0, "covered": [{"date": "2026-10-12", "headline": "HCL results", "kind": "results"}]}))
    made, photos = [], []
    msg = breaking.run(lambda t: None, lambda p, c: True,
                       lambda f: made.append(f) or {"video": "v", "caption": "c", "script_source": "claude",
                                                    "voice": "elevenlabs"},
                       now=NOW, out_dir=tmp_path, get=get, size=SIZE, feeds={}, send_photo=lambda p, c: photos.append(p))
    st = json.loads((tmp_path / "breaking.json").read_text())
    assert asked["covered"] == ["HCL results"]                                # it is told what is already covered
    assert made[0]["series"] == "ZAROORI KHABAR" and made[0]["macro"]["headline"].startswith("US suspends")
    assert [c["headline"] for c in st["covered"]][-2:] == [f["headline"] for f in found] and photos
    again = breaking.run(lambda t: None, lambda p, c: True, made.append, now=NOW.replace(minute=30), out_dir=tmp_path,
                         get=get, size=SIZE, feeds={})
    assert again.startswith("breaking: nothing made") and len(made) == 1     # next search only after 2 hours


def test_night_report_gathers_the_day_with_sources_and_reads_what_was_only_alerted(tmp_path, monkeypatch):
    from datetime import date
    from trader.reel import job, market, night, script as S
    day = {"date": "2026-10-12", "broad": {"Nifty 50": {"close": 22600.0, "pct": 0.4}}, "sectors": [("Metal", -1.0),
           ("IT", 2.1)], "fii": -1200.0, "dii": 900.0, "calendar": ["Quarterly results due: Wipro"],
           "history": ["Nifty 50 moved ... no reliable pattern"], "news": [
               {"headline": "RBI keeps repo rate at 5.5%", "facts": "f", "why_it_matters": "w", "sector": "Bank",
                "companies": [], "source_urls": ["https://www.rbi.org.in/x"], "official": True}],
           "results": [{"company": "HCL Technologies", "quarter": "Jul-Sep 2026", "official": True, "points": [
               {"text": "Revenue up 5%", "source_urls": ["https://www.nseindia.com/a"]}]}]}
    market.save(day, tmp_path)
    (tmp_path / "breaking.json").write_text(json.dumps({"covered": [
        {"date": "2026-10-12", "kind": "results", "company": "HCL Technologies", "headline": "HCL Tech results",
         "points": ["Profit up 7%"], "why_it_matters": "w", "url": "https://www.nseindia.com/b", "context": []}],
        "missed": [{"date": "2026-10-12", "kind": "order won", "company": "L&T", "title": "Order", "url": "https://u",
                    "source": "NSE", "id": "nse:9", "time": "12-Oct-2026 15:00:00"}]}))
    monkeypatch.setattr(breaking, "read", lambda c, client=None, fast=True: {
        "material": True, "headline": "L&T wins a large order", "points": ["Order worth ₹5,000 crore"],
        "why_it_matters": "w", "url": c["url"], "source": "NSE", "companies": ["L&T"], "sector": "Other", "context": []})
    b = night.gather(date(2026, 10, 12), tmp_path)
    titles = [s["title"] for s in b["stories"]]
    assert titles[0].startswith("HCL Technologies results") and len(titles) == 3          # HCL once, RBI, L&T order
    assert "L&T wins a large order" in titles and "Nifty 50 closed 22,600 (+0.40%)" in b["close"][0]
    f = job.facts_for(date(2026, 10, 12), "evening", {}, tmp_path / "none", day=b, out_dir=tmp_path)
    assert f["format"] == "night" and "STORY 1 (results)" in f["night_text"] and "RAAT KI REPORT" in S._brief(f)
    one = night.single({**b, "stories": b["stories"][:1]})
    assert one["results"]["company"] == "HCL Technologies"
