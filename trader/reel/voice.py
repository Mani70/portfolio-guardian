"""The Reel's voice: ElevenLabs (premium AI voice; its multilingual models read Hinglish, best when the Hindi words
are written in Devanagari - see script.Scene.spoken).

Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID (in OCI Vault with the other secrets; trader.yaml reel.voice_id
overrides the voice). Without them each scene gets silence of the length the narration would take, so the video can
still be built and checked.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

import requests

log = logging.getLogger("trader.reel.voice")
BASE = "https://api.elevenlabs.io/v1"
API = BASE + "/text-to-speech/{voice}"
TTS_MODEL = "eleven_flash_v2_5"        # ~half the credits of eleven_multilingual_v2: a daily Reel fits the Starter plan
WORDS_PER_SECOND = 2.6


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def duration(path: Path) -> float:
    """Seconds of audio in a file (ffmpeg's own reading; no ffprobe needed)."""
    out = subprocess.run([ffmpeg(), "-i", str(path), "-f", "null", "-"], capture_output=True, text=True).stderr
    t = 0.0
    for line in out.splitlines():
        if "time=" in line:
            try:
                h, m, s = line.split("time=")[1].split()[0].split(":")
                t = int(h) * 3600 + int(m) * 60 + float(s)
            except (ValueError, IndexError):
                pass
    return t


def silence(path: Path, seconds: float) -> Path:
    subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.2f}",
                    "-q:a", "9", str(path)], capture_output=True, check=True)
    return path


def payload(text: str, model: str, prev: Optional[str] = None, nxt: Optional[str] = None) -> dict:
    """The request body. eleven_v3 takes only a stability preset (0, 0.5, 1) and no neighbouring text; the older
    models get our settings and the scenes before and after, so the tone carries on from scene to scene."""
    if model.startswith("eleven_v3"):
        return {"text": text, "model_id": model, "voice_settings": {"stability": 0.5}}
    body = {"text": text, "model_id": model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35}}
    if prev:
        body["previous_text"] = prev
    if nxt:
        body["next_text"] = nxt
    return body


def speak(text: str, path: Path, key: Optional[str] = None, voice: Optional[str] = None,
          model: Optional[str] = None, prev: Optional[str] = None, nxt: Optional[str] = None) -> tuple[Path, str]:
    """(audio file, source): 'elevenlabs', or 'silence (...)' when the voice service is not available."""
    key = key or os.getenv("ELEVENLABS_API_KEY")
    voice = voice or os.getenv("ELEVENLABS_VOICE_ID")
    if not (key and voice):
        return silence(path, max(2.0, len(text.split()) / WORDS_PER_SECOND)), "silence (no ElevenLabs key)"
    model = model or TTS_MODEL
    body = payload(text, model, prev, nxt)
    try:
        for attempt in range(2):
            r = requests.post(API.format(voice=voice), headers={"xi-api-key": key, "accept": "audio/mpeg"},
                              json=body, timeout=180)
            if r.status_code == 200 and r.content:
                path.write_bytes(r.content)
                return path, "elevenlabs"
            log.warning("ElevenLabs HTTP %s: %s", r.status_code, r.text[:200])
            if r.status_code not in (400, 422) or attempt:
                break
            body = {"text": text, "model_id": model}                     # a setting this model rejects: plain request
        why = f"ElevenLabs error {r.status_code}"
    except requests.RequestException as e:
        log.warning("ElevenLabs unreachable: %s", e)
        why = "ElevenLabs unreachable"
    return silence(path, max(2.0, len(text.split()) / WORDS_PER_SECOND)), f"silence ({why})"


def my_voices(key: str) -> List[dict]:
    """Voices in the account's 'My Voices'."""
    r = requests.get(f"{BASE}/voices", headers={"xi-api-key": key}, timeout=60)
    r.raise_for_status()
    return [{"voice_id": v["voice_id"], "name": v.get("name", ""), "about": _about(v.get("labels") or {}),
             "preview": v.get("preview_url"), "mine": True} for v in r.json().get("voices", [])]


def library_voices(key: str, language: str = "hi", n: int = 8) -> List[dict]:
    """The most used voices of the public Voice Library for a language (most used first, if the API sorts)."""
    head = {"xi-api-key": key}
    for params in ({"language": language, "page_size": n, "sort": "usage_character_count_1y"},
                   {"language": language, "page_size": n}):
        r = requests.get(f"{BASE}/shared-voices", headers=head, params=params, timeout=60)
        if r.status_code == 200:
            return [{"voice_id": v["voice_id"], "name": v.get("name", ""), "about": _about(v),
                     "preview": v.get("preview_url"), "mine": False} for v in r.json().get("voices", [])[:n]]
    r.raise_for_status()
    return []


def _about(v: dict) -> str:
    return ", ".join(str(v[k]) for k in ("gender", "age", "accent", "use_case", "descriptive") if v.get(k))
