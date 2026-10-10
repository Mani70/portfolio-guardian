"""COMPANY KI KUNDLI (trader/reel/company.py): sourced business facts only - never the share."""
from datetime import date
from types import SimpleNamespace

from trader.reel import company, job, script as S


class FakeClient:
    def __init__(self, facts):
        self.facts, self.calls = facts, []
        r = SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(url="https://www.nseindia.com/ar"),
                                                                    SimpleNamespace(url="https://www.livemint.com/a")])
        self.beta = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: self.calls.append(kw) or SimpleNamespace(
                stop_reason="end_turn", content=[r, SimpleNamespace(type="text", text="notes")]),
            parse=lambda **kw: self.calls.append(kw) or SimpleNamespace(parsed_output=self.facts)))


def F(text, url="https://www.nseindia.com/ar"):
    return company.Fact(text=text, source_urls=[url])


FACTS = company.CompanyFacts(
    business="Asian Paints makes paints for homes and industry.",
    how_it_earns=[F("Decorative paints are most of its revenue (FY26 annual report)")],
    numbers=[F("Revenue of about ₹35,000 crore in FY26"), F("Share price is ₹2,500 - cheap now"),       # banned
             F("About 9,000 employees", "https://unknown.example/x")],                                   # not trusted
    history=[F("Founded in 1942 in Mumbai"), F("Became India's largest paint maker by 1967", "https://www.livemint.com/a")],
    risks=[F("Crude oil prices affect raw material costs")])


def test_research_keeps_only_sourced_facts_and_never_the_share():
    got, note = company.research("ASIANPAINT", "Asian Paints", FakeClient(FACTS), feed_items=[])
    texts = [f["text"] for k in ("how_it_earns", "numbers", "history", "risks") for f in got[k]]
    assert "Founded in 1942 in Mumbai" in texts and not any("Share price" in t or "9,000" in t for t in texts)
    assert note == "5 of 7 facts passed the source and SEBI checks"
    assert company.research("X", "X", FakeClient(company.CompanyFacts(business="b", how_it_earns=[], numbers=[],
                                                                      history=[], risks=[])))[0] is None


def test_case_study_check_bans_price_valuation_and_buy_sell_but_allows_business_numbers():
    def sc(line):
        beats = [S.Scene(kind="story", narration=line, on_screen="x")] + [
            S.Scene(kind="explain", narration="Revenue matlab company ki kul kamai, profit matlab kharch ke baad bacha.",
                    on_screen="Revenue") for _ in range(8)] + [
            S.Scene(kind="question", narration="Aap kaunsa product use karte ho? Comment mein batao.", on_screen="?")]
        return S.ReelScript(title="t", caption="c", hashtags=[], scenes=beats)
    ok = S.check(sc("Asian Paints ki revenue FY26 mein ₹35,000 crore thi, 8% zyada."), ["Asian Paints"], company_mode=True)
    assert ok == []
    bad = " | ".join(S.check(sc("Asian Paints ka share price sasta hai, kharidna chahiye."), ["Asian Paints"],
                             company_mode=True))
    assert "never the share" in bad


def test_company_slot_rotates_and_skips_politely_without_facts(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert company.pick(0)[0] == "RELIANCE" and company.pick(len(company.COMPANIES))[0] == "RELIANCE"
    said = []
    msg = job.run(said.append, lambda p, c: True, today=date(2026, 10, 11), out_dir=tmp_path, slot="company")
    assert msg == "reel (company): skipped - no ANTHROPIC_API_KEY" and "skipped this week (Reliance" in said[0]
    good = {"symbol": "ASIANPAINT", "name": "Asian Paints", "business": "Paints.",
            "how_it_earns": [{"text": "Decorative paints", "source_urls": ["https://www.nseindia.com/ar"]}],
            "numbers": [{"text": "Revenue ₹35,000 crore FY26", "source_urls": ["https://www.livemint.com/a"]}],
            "history": [{"text": "Founded 1942", "source_urls": ["https://www.nseindia.com/ar"]}], "risks": []}
    monkeypatch.setattr(company, "research", lambda sym, name, client=None: (good, "4 of 4"))
    videos = []
    msg = job.run(said.append, lambda p, c: videos.append(p) or True, today=date(2026, 10, 11), out_dir=tmp_path,
                  slot="company", topic="company:ASIANPAINT")
    assert msg.startswith("reel (company): sent company") and videos
    assert "COMPANY KI KUNDLI  •  EP 1" in said[-2] and "Sources: livemint.com, nseindia.com" in said[-2]
