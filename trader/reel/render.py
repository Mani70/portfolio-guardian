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
          top_line: str = "", fast: bool = False) -> Path:
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
                  top_line if k == 0 or kind == "hook" else "", handle).save(p, compress_level=1)
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
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast" if fast else "medium", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out.resolve())],
                   cwd=work, capture_output=True, check=True)
    return out


def story_card(headline: str, lines: List[str], source: str, out: Path, label: str = "ABHI ABHI") -> Path:
    """A 1080x1920 Instagram Story card: label, headline, up to 4 lines, the official source - ready in a second."""
    img = _gradient((90, 10, 20), (20, 20, 40)).copy()
    d = ImageDraw.Draw(img)
    lf = font(44)
    lw = d.textlength(label, font=lf)
    d.rounded_rectangle([(W - lw) / 2 - 34, 230, (W + lw) / 2 + 34, 320], radius=44, fill=YELLOW)
    d.text(((W - lw) / 2, 248), label, font=lf, fill=(20, 20, 20))
    y = _centered(d, textwrap.wrap(headline.upper(), 18)[:5], font(86), 420, WHITE, gap=20)
    bf = font(46, bold=False)
    for line in lines[:4]:
        y = _centered(d, textwrap.wrap("• " + line, 36)[:3], bf, y + 40, (235, 235, 235), gap=10)
    _centered(d, ["Source: " + source, FOOTER], font(32, bold=False), 1700, (220, 220, 220), gap=12)
    img.save(out)
    return out


SERIES_COLOURS = {"MYTH vs SACH": ((70, 14, 30), (150, 40, 50)), "MARKET AAJ": ((8, 30, 60), (20, 80, 140)),
                  "RAAT KI REPORT": ((15, 15, 45), (60, 30, 110)), "NEWS SAMJHO": ((12, 30, 40), (30, 90, 60)),
                  "MARKET KI KAHANI": ((25, 18, 40), (90, 50, 110)), "COMPANY KI KUNDLI": ((40, 30, 10), (130, 90, 20)),
                  "BREAKING SAMJHO": ((90, 10, 20), (20, 20, 40)), "ZAROORI KHABAR": ((90, 10, 20), (20, 20, 40))}


def cover(series: str, episode: int, hook: str, out: Path, handle: str = "") -> Path:
    """The Reel's cover for the profile grid: the series in its colour, the episode, the hook in big letters. The
    middle third carries the text, so Instagram's square grid crop shows it."""
    top, bottom = SERIES_COLOURS.get(series, ((18, 24, 64), (88, 28, 135)))
    img = _gradient(top, bottom).copy()
    d = ImageDraw.Draw(img)
    lf = font(48)
    lw = d.textlength(series, font=lf)
    d.rounded_rectangle([(W - lw) / 2 - 36, 520, (W + lw) / 2 + 36, 616], radius=48, fill=YELLOW)
    d.text(((W - lw) / 2, 538), series, font=lf, fill=(20, 20, 20))
    lines, f = _big(hook)
    if f.size > 120:                                                      # a bare number: keep it, smaller
        f = font(180)
    y = _centered(d, lines, f, 700, WHITE, gap=24)
    _centered(d, [f"EP {episode}"], font(44), max(y + 30, 1250), (240, 240, 240))
    if handle:
        _centered(d, [handle], font(36, bold=False), 1800, (230, 230, 230))
    img.save(out)
    return out


CW, CH = 1080, 1350                                                       # Instagram's 4:5 carousel


def slide(title: str, lines: List[str], idx: int, total: int, out: Path, handle: str = "",
          colours=((8, 30, 60), (20, 80, 140))) -> Path:
    """One carousel slide: title, up to 6 lines, page number, footer."""
    img = Image.new("RGB", (CW, CH), colours[0])
    d = ImageDraw.Draw(img)
    for y in range(CH):
        a = y / (CH - 1)
        d.line([(0, y), (CW, y)], fill=tuple(int(colours[0][i] + (colours[1][i] - colours[0][i]) * a) for i in range(3)))
    tf, bf = font(76), font(52, bold=False)
    titles = textwrap.wrap(title.upper(), 20)[:3]
    body = [textwrap.wrap(line, 32)[:3] for line in lines[:6]]
    height = len(titles) * (tf.size + 14) + 50 + sum(len(b) * (bf.size + 12) + 26 for b in body)
    y = max(90, (CH - 120 - height) // 2)                                  # the block centred on the slide
    for line in titles:
        w = d.textlength(line, font=tf)
        d.text(((CW - w) / 2, y), line, font=tf, fill=YELLOW)
        y += tf.size + 14
    y += 50
    for parts in body:
        for i, part in enumerate(parts):
            d.text((80, y), ("• " if i == 0 else "   ") + part, font=bf, fill=WHITE)
            y += bf.size + 12
        y += 26
    foot = f"{idx}/{total}  •  {FOOTER}" + (f"  •  {handle}" if handle else "")
    ff = font(28, bold=False)
    d.text(((CW - d.textlength(foot, font=ff)) / 2, CH - 80), foot, font=ff, fill=(220, 220, 220))
    img.save(out)
    return out
