"""The daily education Reels (trader/reel): safety checks, the rewrite loop, numbers as words, the episode formats
and a full video build without keys."""
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd

from trader.reel import content, job, numbers, render, script as S


def _script(news_line: str) -> S.ReelScript:
    beat = "Rebalancing mein jo fund zyada badh gaya, uska thoda sell karke peeche reh gaye fund mein lagate hain."
    return S.ReelScript(title="t", scenes=[S.Scene(kind="hook", narration="Kya aap jaante ho?", on_screen="Sach"),
                                           *[S.Scene(kind="explain", narration=beat, on_screen="Rebalancing")
                                             for _ in range(6)],
                                           S.Scene(kind="news", narration=news_line, on_screen="NSE news"),
                                           S.Scene(kind="question", narration="Aapne kabhi rebalance kiya? Comment karo.",
                                                   on_screen="Comment karo")],
                        caption="Aaj ka lesson", hashtags=["investing"])


def test_checks_allow_education_and_block_calls_predictions_and_promises():
    ok = _script("TCS ne buyback announce kiya: company apne hi shares wapas leti hai, matlab market mein kam shares.")
    assert S.check(ok, ["TCS"]) == []                                       # 'sell' in a lesson is fine
    bad = _script("TCS ne buyback announce kiya, TCS kharido abhi! Yeh badhega, paisa double.")
    issues = " | ".join(S.check(bad, ["TCS"]))
    assert "instruction to trade" in issues and "prediction" in issues and "promise" in issues
    priced = _script("TCS ke shares ₹4,100 par hain aur buyback aaya.")
    assert any("price next to a named company" in i for i in S.check(priced, ["TCS"]))
    no_question = _script("TCS ne buyback announce kiya.")
    no_question.scenes.pop()
    assert any("comment question" in i for i in S.check(no_question, ["TCS"]))


class FakeClaude:
    """beta.messages.parse returning an unsafe script first, then a safe one."""
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.outputs.pop(0))


FACTS = {"date": "2026-10-12", "format": "news", "episode": 3,
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
    assert "NEWS SAMJHO, episode 3" in c.calls[0]["messages"][0]["content"]
    sc, src = S.write(FACTS, client=FakeClaude([bad, bad]))
    assert src.startswith("template") and sc.scenes[-1].kind == "question"


def test_numbers_are_spoken_as_an_indian_narrator_says_them():
    hi = numbers.speakable("2012 से 2026 तक 2536 announcements, ₹10 crore, +1.34%, 0.44%, Nifty 50, 52-week low, 3-4 हफ्ते")
    assert hi == ("दो हज़ार बारह से दो हज़ार छब्बीस तक दो हज़ार पाँच सौ छत्तीस announcements, दस करोड़ रुपये, प्लस एक "
                  "पॉइंट तीन चार percent, शून्य पॉइंट चार चार percent, Nifty fifty, fifty-two week low, तीन से चार हफ्ते")
    assert numbers.speakable("1992 mein 2536 log, 2012-26", hindi_words=False) == \
        "one thousand nine hundred ninety-two mein two thousand five hundred thirty-six log, two thousand twelve to " \
        "two thousand twenty-six"
    assert numbers.hindi(1992) == "उन्नीस सौ बानवे" and numbers.hindi(12500000) == "एक करोड़ पच्चीस लाख"


def test_morning_is_a_myth_episode_and_evening_falls_back_to_a_story(tmp_path):
    m = job.facts_for(date(2026, 10, 12), "morning", {"myth": 4}, tmp_path / "none")
    assert m["format"] == "myth" and m["episode"] == 5 and m["lesson"][1] and m["next"].endswith("?")
    e = job.facts_for(date(2026, 10, 12), "evening", {"story": 12}, tmp_path / "none")
    assert e["format"] == "story" and e["story"] == content.STORIES[12 % len(content.STORIES)] and e["episode"] == 13
    # myths are questions, never instructions; history uses only what the regulator / courts settled
    assert all(m[0].endswith("?") and not S.COMMAND.search(m[0]) for m in content.MYTHS)


def test_a_topic_can_be_chosen_for_the_first_reels(tmp_path):
    f = job.facts_for(date(2026, 10, 12), "morning", {"myth": 2}, tmp_path, topic="myth:13")
    assert f["myth"] == content.MYTHS[13][0] and f["episode"] == 3
    f = job.facts_for(date(2026, 10, 12), "morning", {}, tmp_path, topic="story:0")
    assert f["format"] == "story" and f["story"][0].startswith("Harshad Mehta") and f["episode"] == 1
    import pytest
    with pytest.raises(ValueError):
        job.facts_for(date(2026, 10, 12), "morning", {}, tmp_path, topic="myth:99")


def test_news_skips_routine_employee_allotments_and_prefers_a_type_with_a_track_record(tmp_path):
    pd.DataFrame([
        {"date": "2026-10-12", "symbol": "ICICIBANK", "subject": "Allotment of Securities", "link": "x",
         "text": "Allotment of equity shares under ESOS; preferential allotment"},
        {"date": "2026-10-12", "symbol": "SMALLCO", "subject": "Bagging/Receiving of orders/contracts", "link": "x",
         "text": "order worth Rs 50 crore received"},
        {"date": "2026-10-12", "symbol": "TCS", "subject": "Bagging/Receiving of orders/contracts", "link": "x",
         "text": "TCS has received an order worth Rs 900 crore"}]).to_csv(tmp_path / "filings.csv", index=False)
    pd.DataFrame({"symbol": ["ICICIBANK", "SMALLCO", "TCS"], "value": [9e9, 1e7, 5e9]}).to_csv(
        tmp_path / "px_20261012.csv", index=False)
    n = content.news_item(date(2026, 10, 12), tmp_path)
    assert n["symbol"] == "TCS" and n["type"] == "order won" and "beat the Nifty" in n["history"]


def test_word_captions_follow_the_voice_and_frames_have_a_progress_bar():
    t = render.word_times(["Kya", "aap", "jaante", "ho?"], 2.0)
    assert abs(sum(t) - 2.0) < 1e-9 and t[2] > t[0]
    img = render.frame("myth", "93%", ["F&O", "mein", "93%", "log", "haare"], 2, 0.5, "MYTH vs SACH  •  EP 1")
    assert img.size == (1080, 1920) and img.getpixel((200, 5)) == render.YELLOW and img.getpixel((900, 5)) == (0, 0, 0)


def test_full_reel_without_keys_builds_a_video_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    said, videos = [], []
    msg = job.run(said.append, lambda p, c: videos.append(p) or True, today=date(2026, 10, 12), out_dir=tmp_path,
                  store=tmp_path / "none")
    assert msg.startswith("reel (morning): sent myth") and videos and videos[0].stat().st_size > 10_000
    assert "MYTH vs SACH  •  EP 1" in said[0] and "Education only - not investment advice" in said[0]
    assert "Voice: silence (no ElevenLabs key)" in said[1] and "pin a comment" in said[1]
    assert job.run(said.append, lambda p, c: True, today=date(2026, 10, 12), out_dir=tmp_path) == \
        "reel (morning): already sent today"
    assert job.run(said.append, lambda p, c: True, today=date(2026, 10, 12), out_dir=tmp_path, store=tmp_path / "none",
                   slot="evening").startswith("reel (evening): sent story")
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["episodes"] == {"myth": 1, "story": 1} and st["sent_evening"] == "2026-10-12"


def test_voice_text_is_kept_only_when_it_says_what_the_narration_says():
    sc = _script("TCS ne buyback announce kiya, 10 crore ka.")
    sc.scenes[0].spoken = "क्या आप जानते हो?"
    sc.scenes[7].spoken = "TCS ने buyback announce किया, 10 करोड़ का।"
    assert S.vet_spoken(sc, ["TCS"]) == [] and sc.scenes[7].spoken
    sc.scenes[7].spoken = "TCS के शेयर ख़रीदो, 10 करोड़ का।"                  # a call the Roman text did not make
    sc.scenes[0].spoken = "क्या आप जानते हो? 50 साल।"                          # a number the Roman text does not have
    dropped = " | ".join(S.vet_spoken(sc, ["TCS"]))
    assert "instruction to trade" in dropped and "numbers differ" in dropped
    assert sc.scenes[0].spoken == "" and sc.scenes[7].spoken == ""          # those scenes are voiced from the narration


def test_devanagari_mode_voices_the_spoken_text_with_numbers_as_words(tmp_path, monkeypatch):
    from trader.reel import voice
    sc = _script("TCS ne buyback announce kiya.")
    sc.scenes[1].spoken = "2536 बार ऐसा हुआ"
    monkeypatch.setattr(S, "write", lambda facts, client=None: (sc, "claude"))
    calls = []

    def fake_speak(text, path, key=None, voice=None, model=None, prev=None, nxt=None):
        calls.append((text, voice, model, prev, nxt))
        return real_silence(path, 1.0), "elevenlabs"
    real_silence = voice.silence
    monkeypatch.setattr(voice, "speak", fake_speak)
    job.make(date(2026, 10, 12), tmp_path, store=tmp_path / "none", voice_model="eleven_v3", voice_id="V1",
             speak="devanagari")
    texts = [c[0] for c in calls]
    assert texts[1] == "दो हज़ार पाँच सौ छत्तीस बार ऐसा हुआ" and texts[0] == "Kya aap jaante ho?"   # no spoken: Roman
    assert texts[-1] == S.DISCLAIMER_SPOKEN and calls[1][1:3] == ("V1", "eleven_v3")
    assert calls[1][3] == texts[0] and calls[1][4] == texts[2]


def test_voice_request_fits_the_model_and_retries_plain_on_a_rejected_setting(tmp_path, monkeypatch):
    from trader.reel import voice
    assert voice.payload("x", "eleven_v3", "a", "b") == {"text": "x", "model_id": "eleven_v3",
                                                         "voice_settings": {"stability": 0.5}}
    assert voice.payload("x", "eleven_multilingual_v2", "a", "b")["previous_text"] == "a"
    sent = []

    def post(url, headers, json, timeout):
        sent.append(json)
        return SimpleNamespace(status_code=400 if len(sent) == 1 else 200, content=b"mp3", text="bad setting")
    monkeypatch.setattr(voice.requests, "post", post)
    f, src = voice.speak("namaste", tmp_path / "a.mp3", key="k", voice="v", model="eleven_multilingual_v2", prev="p")
    assert src == "elevenlabs" and sent[1] == {"text": "namaste", "model_id": "eleven_multilingual_v2"}


def test_voice_audition_sends_each_voice_and_the_four_way_comparison(tmp_path, monkeypatch):
    from trader.reel import audition, voice
    monkeypatch.setattr(voice, "my_voices", lambda key: [{"voice_id": "MINE", "name": "Mine", "about": "",
                                                          "preview": None, "mine": True}])
    monkeypatch.setattr(voice, "library_voices", lambda key, lang, n: [
        {"voice_id": "LIB1", "name": "Lib", "about": "female, young", "preview": "https://x/p.mp3", "mine": False},
        {"voice_id": "MINE", "name": "Mine", "about": "", "preview": None, "mine": False}])
    ok = {"MINE"}
    monkeypatch.setattr(voice, "speak", lambda text, path, key=None, voice=None, model=None, **kw:
                        (path, "elevenlabs" if voice in ok else "silence (ElevenLabs error 404)"))
    said, clips = [], []
    msg = audition.run(said.append, lambda p, c, title="": clips.append((p, c)) or True, key="k", out=tmp_path)
    assert msg == "voices: 2 sent" and len(clips) == 2
    assert clips[1][0] == "https://x/p.mp3" and "ElevenLabs' own sample" in clips[1][1]       # library voice preview
    assert "MINE" in said[-1] and "LIB1" in said[-1]
    clips.clear()
    ok.add("LIB1")
    assert audition.run(said.append, lambda p, c, title="": clips.append(c) or True, voice_id="LIB1", key="k",
                        out=tmp_path) == "voices: 4 of 4 clips sent"
    assert "voice_model: eleven_v3\n  speak: devanagari" in clips[3]


def test_voice_audition_reports_a_refused_list_instead_of_crashing(tmp_path, monkeypatch):
    import requests
    from trader.reel import audition, voice

    def refused(*a):
        raise requests.HTTPError(response=SimpleNamespace(status_code=401))
    monkeypatch.setattr(voice, "my_voices", refused)
    monkeypatch.setattr(voice, "library_voices", refused)
    said = []
    assert audition.run(said.append, lambda *a, **k: True, key="k", out=tmp_path) == "voices: no voices to test"
    assert "HTTP 401" in said[0] and "Voices: Read" in said[0]
