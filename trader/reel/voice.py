"""The Reel's voice: ElevenLabs (premium AI voice; its multilingual model reads Hinglish in Roman letters).

Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID (in OCI Vault with the other secrets). Without them each scene gets
silence of the length the narration would take, so the video can still be built and checked.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("trader.reel.voice")
API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
TTS_MODEL = "eleven_multilingual_v2"
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


def speak(text: str, path: Path, key: Optional[str] = None, voice: Optional[str] = None) -> tuple[Path, str]:
    """(audio file, source): 'elevenlabs', or 'silence (...)' when the voice service is not available."""
    key = key or os.getenv("ELEVENLABS_API_KEY")
    voice = voice or os.getenv("ELEVENLABS_VOICE_ID")
    if not (key and voice):
        return silence(path, max(2.0, len(text.split()) / WORDS_PER_SECOND)), "silence (no ElevenLabs key)"
    try:
        r = requests.post(API.format(voice=voice), headers={"xi-api-key": key, "accept": "audio/mpeg"},
                          json={"text": text, "model_id": TTS_MODEL,
                                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35}},
                          timeout=120)
        if r.status_code == 200 and r.content:
            path.write_bytes(r.content)
            return path, "elevenlabs"
        log.warning("ElevenLabs HTTP %s: %s", r.status_code, r.text[:200])
        why = f"ElevenLabs error {r.status_code}"
    except requests.RequestException as e:
        log.warning("ElevenLabs unreachable: %s", e)
        why = "ElevenLabs unreachable"
    return silence(path, max(2.0, len(text.split()) / WORDS_PER_SECOND)), f"silence ({why})"
