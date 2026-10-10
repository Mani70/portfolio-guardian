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
    assert job.run(said.append, lambda p, c: True, today=date(2026, 10, 12), out_dir=tmp_path, store=tmp_path / "none",
                   force=True).startswith("reel: sent")


def test_voice_text_is_kept_only_when_it_says_what_the_narration_says():
    sc = _script("TCS ne buyback announce kiya, 10 crore ka.")
    sc.scenes[0].spoken = "क्या आप जानते हो?"
    sc.scenes[2].spoken = "TCS ने buyback announce किया, 10 करोड़ का।"
    assert S.vet_spoken(sc, ["TCS"]) == [] and sc.scenes[2].spoken
    sc.scenes[2].spoken = "TCS के शेयर ख़रीदो, 10 करोड़ का।"                  # a call the Roman text did not make
    sc.scenes[0].spoken = "क्या आप जानते हो? 50 साल।"                          # a number the Roman text does not have
    dropped = " | ".join(S.vet_spoken(sc, ["TCS"]))
    assert "instruction to trade" in dropped and "numbers differ" in dropped
    assert sc.scenes[0].spoken == "" and sc.scenes[2].spoken == ""          # those scenes are voiced from the narration


def test_devanagari_mode_voices_the_spoken_text_with_neighbouring_scenes(tmp_path, monkeypatch):
    from trader.reel import voice
    sc = _script("TCS ne buyback announce kiya.")
    sc.scenes[1].spoken = "Rebalancing में जो fund ज़्यादा बढ़ गया"
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
    assert texts[1] == "Rebalancing में जो fund ज़्यादा बढ़ गया" and texts[0] == "Kya aap jaante ho?"   # no spoken: Roman
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
