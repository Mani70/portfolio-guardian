"""The daily education Reel (trader/reel): safety checks, the rewrite loop, and a full video build without keys."""
from datetime import date
from types import SimpleNamespace

from trader.reel import job, script as S


def _script(news_line: str) -> S.ReelScript:
    filler = "Rebalancing mein jo fund zyada badh gaya, uska thoda sell karke peeche reh gaye fund mein lagate hain. " * 7
    return S.ReelScript(title="t", scenes=[S.Scene(kind="hook", narration="Kya aap jaante ho?", on_screen="Sach"),
                                           S.Scene(kind="lesson", narration=filler, on_screen="Rebalancing"),
                                           S.Scene(kind="news", narration=news_line, on_screen="NSE news")],
                        caption="Aaj ka lesson", hashtags=["investing"])


def test_checks_allow_education_and_block_calls_predictions_and_promises():
    ok = _script("TCS ne buyback announce kiya: company apne hi shares wapas leti hai, matlab market mein kam shares.")
    assert S.check(ok, ["TCS"]) == []                                       # 'sell' in a lesson is fine
    bad = _script("TCS ne buyback announce kiya, TCS kharido abhi! Yeh badhega, paisa double.")
    issues = " | ".join(S.check(bad, ["TCS"]))
    assert "instruction to trade" in issues and "prediction" in issues and "promise" in issues
    priced = _script("TCS ke shares ₹4,100 par hain aur buyback aaya.")
    assert any("price next to a named company" in i for i in S.check(priced, ["TCS"]))


class FakeClaude:
    """beta.messages.parse returning an unsafe script first, then a safe one."""
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.outputs.pop(0))


FACTS = {"date": "2026-10-12", "lesson": ("Rebalancing", "Bringing a mix back to target."), "research": "A fact.",
         "news": {"symbol": "TCS", "subject": "Buyback", "text": "TCS informed about a buyback", "date": "2026-10-12",
                  "history": None}}


def test_unsafe_script_is_rewritten_then_template_if_still_unsafe():
    bad = _script("TCS kharido abhi, yeh badhega.")
    good = _script("TCS ne buyback announce kiya: company apne shares wapas khareedti hai, chhote investors ke liye "
                   "iska matlab samjhiye.")
    c = FakeClaude([bad, good])
    sc, src = S.write(FACTS, client=c)
    assert src == "claude" and len(c.calls) == 2 and "must be fixed" in c.calls[1]["messages"][-1]["content"]
    assert c.calls[0]["model"] == "claude-opus-5-5" and c.calls[0]["fallbacks"] == "default"
    sc, src = S.write(FACTS, client=FakeClaude([bad, bad]))
    assert src.startswith("template") and sc.scenes[-1].kind == "takeaway"


def test_full_reel_without_keys_builds_a_video_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    said, videos = [], []
    msg = job.run(said.append, lambda p, c: videos.append(p) or True, today=date(2026, 10, 12), out_dir=tmp_path,
                  store=tmp_path / "none")
    assert msg.startswith("reel: sent") and videos and videos[0].stat().st_size > 10_000
    assert "Education only - not investment advice" in said[0] and "Voice: silence (no ElevenLabs key)" in said[1]
    assert job.run(said.append, lambda p, c: True, today=date(2026, 10, 12), out_dir=tmp_path) == "reel: already sent today"
