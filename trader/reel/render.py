"""The Reel's pictures and the final video: vertical 1080x1920 frames (label, big on-screen text, spoken words as
captions near the bottom), joined with the voice by ffmpeg into an Instagram-ready MP4."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .voice import duration, ffmpeg

W, H = 1080, 1920
STYLE = {  # kind: (label, top colour, bottom colour)
    "hook": ("KYA AAP JAANTE HO?", (18, 24, 64), (88, 28, 135)),
    "lesson": ("AAJ KA LESSON", (10, 40, 70), (12, 110, 120)),
    "research": ("HUMARA TEST", (30, 20, 60), (150, 60, 40)),
    "news": ("NSE NEWS, SAMJHO", (12, 30, 40), (30, 90, 60)),
    "takeaway": ("YAAD RAKHO", (40, 10, 50), (120, 30, 90)),
    "disclaimer": ("ZAROORI BAAT", (20, 20, 20), (60, 60, 60)),
}
FOOTER = "Education only  •  Not investment advice"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    import matplotlib
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name), size)


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


def frame(kind: str, on_screen: str, caption: str, handle: str = "") -> Image.Image:
    label, top, bottom = STYLE.get(kind, STYLE["lesson"])
    img = _gradient(top, bottom)
    d = ImageDraw.Draw(img)
    lf = font(40)
    lw = d.textlength(label, font=lf)
    d.rounded_rectangle([(W - lw) / 2 - 30, 210, (W + lw) / 2 + 30, 290], radius=40, fill=(255, 255, 255))
    d.text(((W - lw) / 2, 228), label, font=lf, fill=top)
    big = font(92)
    _centered(d, textwrap.wrap(on_screen.upper(), 16)[:4], big, 560, (255, 255, 255), gap=26)
    if caption:
        cf = font(54)
        lines = textwrap.wrap(caption, 26)[:3]
        box_h = len(lines) * (cf.size + 16) + 50
        d.rounded_rectangle([60, 1340, W - 60, 1340 + box_h], radius=30, fill=(12, 12, 12))
        _centered(d, lines, cf, 1365, (255, 214, 10), gap=16)
    ff = font(30, bold=False)
    _centered(d, [FOOTER] + ([handle] if handle else []), ff, 1780, (220, 220, 220), gap=8)
    return img


def chunks(text: str, n: int = 6) -> List[str]:
    words = text.split()
    return [" ".join(words[i:i + n]) for i in range(0, len(words), n)] or [""]


def build(scenes: List[Tuple[str, str, str, Path]], out: Path, work: Path, handle: str = "") -> Path:
    """scenes: (kind, on_screen, narration, audio file). Captions follow the narration, evenly over each scene."""
    work.mkdir(parents=True, exist_ok=True)
    listing, audios, k = [], [], 0
    for kind, on_screen, narration, audio in scenes:
        secs = max(1.5, duration(audio))
        parts = chunks(narration)
        for part in parts:
            p = work / f"f{k:03d}.png"
            frame(kind, on_screen, part, handle).save(p)
            listing.append(f"file '{p.name}'\nduration {secs / len(parts):.3f}")
            k += 1
        audios.append(audio)
    listing.append(f"file 'f{k - 1:03d}.png'")                          # concat demuxer needs the last frame again
    (work / "frames.txt").write_text("\n".join(listing))
    (work / "audio.txt").write_text("\n".join(f"file '{a.resolve()}'" for a in audios))
    ff = ffmpeg()
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", "audio.txt", "-ac", "1", "-ar", "44100",
                    "audio.m4a"], cwd=work, capture_output=True, check=True)
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", "frames.txt", "-i", "audio.m4a",
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out.resolve())],
                   cwd=work, capture_output=True, check=True)
    return out
