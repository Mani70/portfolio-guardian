"""The Reel's pictures and the final video: vertical 1080x1920 frames joined with the voice by ffmpeg into an
Instagram-ready MP4.

Built for watch time: every beat gets its own colour and big on-screen text, the spoken words appear as captions with
the current word highlighted (timed to the voice), a thin progress bar runs along the top, the gaps between beats are
trimmed, and the sound is normalised to the loudness of typical phone video (-14 LUFS).
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .voice import duration, ffmpeg

W, H = 1080, 1920
STYLE = {  # kind: (label, top colour, bottom colour)
    "hook": ("", (18, 24, 64), (88, 28, 135)),
    "myth": ("MYTH", (70, 14, 30), (150, 40, 50)),
    "truth": ("SACH", (8, 60, 40), (20, 130, 80)),
    "proof": ("HUMARA TEST", (30, 20, 60), (150, 60, 40)),
    "explain": ("SAMJHO", (10, 40, 70), (12, 110, 120)),
    "news": ("NSE NEWS", (12, 30, 40), (30, 90, 60)),
    "history": ("ITIHAAS KYA KEHTA HAI", (40, 30, 10), (130, 90, 20)),
    "story": ("KAHANI", (25, 18, 40), (90, 50, 110)),
    "twist": ("TWIST", (90, 30, 0), (190, 90, 10)),
    "takeaway": ("YAAD RAKHO", (40, 10, 50), (120, 30, 90)),
    "question": ("AAPKA JAWAB?", (10, 50, 90), (30, 100, 170)),
    "lesson": ("SAMJHO", (10, 40, 70), (12, 110, 120)),
    "research": ("HUMARA TEST", (30, 20, 60), (150, 60, 40)),
    "market": ("AAJ KA MARKET", (8, 30, 60), (20, 80, 140)),
    "sector": ("SECTORS", (30, 30, 70), (70, 60, 150)),
    "flows": ("PAISA KISNE LAGAYA", (20, 50, 40), (40, 120, 90)),
    "watch": ("KAL KYA DEKHNA HAI", (50, 30, 10), (140, 80, 20)),
    "disclaimer": ("ZAROORI BAAT", (20, 20, 20), (60, 60, 60)),
}
FOOTER = "Education only  •  Not investment advice"
YELLOW, WHITE = (255, 214, 10), (255, 255, 255)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    import matplotlib
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name), size)


@lru_cache(maxsize=32)
def _gradient(top, bottom) -> Image.Image:
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for y in range(H):
        a = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * a) for i in range(3)))
    return img


def _centered(d: ImageDraw.ImageDraw, lines: List[str], f, y: int, fill, gap: int = 18) -> int:
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, y), line, font=f, fill=fill)
        y += f.size + gap
    return y


def _big(on_screen: str) -> Tuple[List[str], ImageFont.FreeTypeFont]:
    """A short number ('93%', '₹1.8 lakh crore') is shown huge; other text large and wrapped."""
    text = on_screen.upper()
    if re.fullmatch(r"[₹+\-−]?\s?[\d.,]+\s?%?", text.strip()):
        return [text.strip()], font(230)
    for size, width in ((104, 14), (92, 16), (74, 20)):
        lines = textwrap.wrap(text, width)
        if len(lines) <= 3:
            return lines, font(size)
    return textwrap.wrap(text, 20)[:4], font(74)


def frame(kind: str, on_screen: str, words: List[str] = (), current: int = -1, progress: float = 0.0,
          top_line: str = "", handle: str = "", caption: str = "") -> Image.Image:
    """One picture: label, big text, the spoken words with the current one highlighted, progress bar, footer.
    (caption: plain caption text instead of words, for a still frame.)"""
    label, top, bottom = STYLE.get(kind, STYLE["explain"])
    img = _gradient(top, bottom).copy()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=(0, 0, 0))
    d.rectangle([0, 0, int(W * max(0.0, min(1.0, progress))), 14], fill=YELLOW)
    if top_line:
        _centered(d, [top_line], font(38), 70, (235, 235, 235))
    label = label or ""
    if label:
        lf = font(40)
        lw = d.textlength(label, font=lf)
        d.rounded_rectangle([(W - lw) / 2 - 30, 210, (W + lw) / 2 + 30, 290], radius=40, fill=WHITE)
        d.text(((W - lw) / 2, 228), label, font=lf, fill=top)
    lines, bf = _big(on_screen)
    y0 = 760 - (len(lines) * (bf.size + 26)) // 2
    _centered(d, lines, bf, max(360, y0), WHITE, gap=26)
    if caption and not words:
        words, current = caption.split(), -1
    if words:
        _caption(d, list(words), current)
    _centered(d, [FOOTER] + ([handle] if handle else []), font(30, bold=False), 1780, (220, 220, 220), gap=8)
    return img


def _caption(d: ImageDraw.ImageDraw, words: List[str], current: int, per: int = 4) -> None:
    """The group of up to 4 words around the current word, the current word in yellow, in a dark box."""
    k = max(0, current)
    start = (k // per) * per
    group = words[start:start + per]
    cf = font(66)
    space = d.textlength(" ", font=cf)
    lines, line, width = [], [], 0.0
    for i, w in enumerate(group):                                       # wrap into lines that fit the box
        wl = d.textlength(w, font=cf)
        if line and width + space + wl > W - 200:
            lines.append(line)
            line, width = [], 0.0
        line.append((start + i, w, wl))
        width += (space if width else 0) + wl
    if line:
        lines.append(line)
    box_h = len(lines) * (cf.size + 18) + 50
    d.rounded_rectangle([70, 1330, W - 70, 1330 + box_h], radius=30, fill=(12, 12, 12))
    y = 1355
    for ln in lines:
        total = sum(x[2] for x in ln) + space * (len(ln) - 1)
        x = (W - total) / 2
        for idx, w, wl in ln:
            d.text((x, y), w, font=cf, fill=YELLOW if idx == current else WHITE)
            x += wl + space
        y += cf.size + 18


def word_times(words: List[str], secs: float) -> List[float]:
    """How long each word stays highlighted: the beat's audio time shared by word length (a good fit for one voice)."""
    weights = [len(w) + 2 for w in words] or [1]
    total = sum(weights)
    return [secs * w / total for w in weights]


def tighten(audio: Path, out: Path) -> Path:
    """The clip without its silent start and end, plus a short breath, as WAV (keeps the original if it is all
    silence, e.g. the no-key fallback)."""
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,apad=pad_dur=0.15")
    subprocess.run([ffmpeg(), "-y", "-i", str(audio), "-af", trim, "-ac", "1", "-ar", "44100", str(out)],
                   capture_output=True)
    if not out.exists() or duration(out) < 0.4:
        subprocess.run([ffmpeg(), "-y", "-i", str(audio), "-ac", "1", "-ar", "44100", str(out)], capture_output=True,
                       check=True)
    return out


def build(scenes: List[Tuple[str, str, str, Path]], out: Path, work: Path, handle: str = "",
          top_line: str = "") -> Path:
    """scenes: (kind, on_screen, narration, audio file). One frame per spoken word, timed to that beat's voice."""
    work.mkdir(parents=True, exist_ok=True)
    clips = [tighten(a, work / f"t{i:02d}.wav") for i, (_, _, _, a) in enumerate(scenes)]
    secs = [max(1.0, duration(c)) for c in clips]
    total, done = sum(secs), 0.0
    listing, k = [], 0
    for (kind, on_screen, narration, _), s in zip(scenes, secs):
        words = narration.split()
        for j, t in enumerate(word_times(words, s)):
            p = work / f"f{k:04d}.png"
            frame(kind, on_screen, words, j, (done + sum(word_times(words, s)[:j])) / total,
                  top_line if k == 0 or kind == "hook" else "", handle).save(p)
            listing.append(f"file '{p.name}'\nduration {t:.3f}")
            k += 1
        done += s
    listing.append(f"file 'f{k - 1:04d}.png'")                         # concat demuxer needs the last frame again
    (work / "frames.txt").write_text("\n".join(listing))
    (work / "audio.txt").write_text("\n".join(f"file '{c.resolve()}'" for c in clips))
    ff = ffmpeg()
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", "audio.txt", "-af",
                    "loudnorm=I=-14:TP=-1.5:LRA=11", "-ac", "1", "-ar", "44100", "audio.m4a"],
                   cwd=work, capture_output=True, check=True)
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", "frames.txt", "-i", "audio.m4a",
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out.resolve())],
                   cwd=work, capture_output=True, check=True)
    return out
