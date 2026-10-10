"""Hear voices before choosing one: the same Hinglish lines read by different ElevenLabs voices, sent to Telegram.

  python -m trader.run voices                  # your voices + the most used Hindi voices of the Voice Library
  python -m trader.run voices --voice <ID>     # one voice: 2 models x (Roman / Devanagari Hindi) = 4 clips

Each clip is about 270 characters (~270 ElevenLabs credits; ~3k for the first command, ~1.1k for the second).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import requests

from . import voice

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "cache" / "reel" / "voices"
# the same lines in both spellings: Hindi, English finance words and numbers - what a Reel actually sounds like
ROMAN = ("Kya aap jaante ho? Humare test mein, Nifty 50 par bull put spread ne 5 mein se 4 mahine profit diya, phir bhi "
         "charges ke baad paisa gaya. P/E ratio ka matlab hai: share ka price, ek saal ke profit se divide. Dividend "
         "yield matlab saal ka dividend, price ke percentage mein.")
DEVANAGARI = ("क्या आप जानते हो? हमारे test में, Nifty 50 पर bull put spread ने 5 में से 4 महीने profit दिया, फिर भी "
              "charges के बाद पैसा गया। P/E ratio का मतलब है: share का price, एक साल के profit से divide। Dividend "
              "yield मतलब साल का dividend, price के percentage में।")
COMPARE = [("eleven_multilingual_v2", "roman"), ("eleven_multilingual_v2", "devanagari"),
           ("eleven_v3", "roman"), ("eleven_v3", "devanagari")]


def candidates(key: str, n_library: int = 8, cap: int = 10) -> tuple[List[dict], List[str]]:
    """(voices, problems): your voices first, then the library's; a list ElevenLabs refuses is reported, not fatal."""
    found, problems = [], []
    for what, get in (("your voices", lambda: voice.my_voices(key)),
                      ("Voice Library", lambda: voice.library_voices(key, "hi", n_library))):
        try:
            found += get()
        except requests.RequestException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            problems.append(f"{what}: could not be listed" + (f" (HTTP {code})" if code else ""))
    seen, out = set(), []
    for v in found:
        if v["voice_id"] not in seen:
            seen.add(v["voice_id"])
            out.append(v)
    return out[:cap], problems


def run(notify, send_audio, voice_id: Optional[str] = None, model: Optional[str] = None,
        key: Optional[str] = None, out: Path = OUT) -> str:
    key = key or os.getenv("ELEVENLABS_API_KEY")
    if not key:
        notify("Voice test: no ElevenLabs key in the vault yet.")
        return "voices: no ElevenLabs key"
    out.mkdir(parents=True, exist_ok=True)
    if voice_id:
        return _compare(notify, send_audio, voice_id, key, out)
    model = model or "eleven_multilingual_v2"
    vs, problems = candidates(key)
    if problems:
        notify("Voice test: " + "; ".join(problems) + ". If it says HTTP 401, the ElevenLabs key needs the "
               "'Voices: Read' permission (elevenlabs.io > Developers > API Keys).")
    if not vs:
        return "voices: no voices to test"
    notify(f"🎙️ Voice test: {len(vs)} voices read the same Hinglish lines (model {model}). Listen and note the "
           "number you like best. Then run the second test on that voice to compare the models.")
    lines = []
    for i, v in enumerate(vs, 1):
        f, src = voice.speak(DEVANAGARI, out / f"v{i:02d}.mp3", key=key, voice=v["voice_id"], model=model)
        label = f"{i}. {v['name']}" + (f" ({v['about']})" if v["about"] else "") + ("  - your voice" if v["mine"] else "")
        if src == "elevenlabs":
            send_audio(f, f"{label}\nvoice ID: {v['voice_id']}", title=f"{i}. {v['name']}")
        elif v.get("preview"):                     # cannot be used by ID from this account: ElevenLabs' own sample
            send_audio(v["preview"], f"{label}\nvoice ID: {v['voice_id']}\n(ElevenLabs' own sample, not our lines: "
                                     "add it to My Voices on elevenlabs.io to use it)", title=f"{i}. {v['name']}")
        else:
            label += f"  - could not be played ({src})"
        lines.append(f"{label}\n   {v['voice_id']}")
    notify("Voices tested:\n\n" + "\n".join(lines) + "\n\nNext, on the server:\n"
           ".venv/bin/python -m trader.run voices --voice <voice ID you liked>")
    return f"voices: {len(vs)} sent"


def _compare(notify, send_audio, voice_id: str, key: str, out: Path) -> str:
    notify(f"🎙️ Voice {voice_id}: the same lines 4 ways - 2 voice models x Hindi words in Roman letters or in "
           "Devanagari (हिंदी). Pick the clearest one; its caption has the lines for trader.yaml.")
    sent = 0
    for i, (model, spell) in enumerate(COMPARE, 1):
        f, src = voice.speak(DEVANAGARI if spell == "devanagari" else ROMAN, out / f"c{i}.mp3", key=key,
                             voice=voice_id, model=model)
        setting = f"reel:\n  voice_id: {voice_id}\n  voice_model: {model}\n  speak: {spell}"
        if src == "elevenlabs":
            sent += send_audio(f, f"{chr(64 + i)}. {model}, {spell}\n\ntrader.yaml:\n{setting}",
                               title=f"{chr(64 + i)}. {model} {spell}")
        else:
            notify(f"{chr(64 + i)}. {model}, {spell}: could not be made ({src})")
    return f"voices: {sent} of {len(COMPARE)} clips sent"
