"""The Reel's pictures and the final video: vertical 1080x1920, 30 frames a second, joined with the voice by ffmpeg
into an Instagram-ready MP4.

Built for watch time (trader/reel/RESEARCH.md, section 8): nothing on screen stands still. Each beat brings a picture -
a big icon, an animated chart of the day's index or sectors, or a number that counts up - with a short headline whose
words pop in, over a slowly drifting background. Captions show 3 spoken words at a time with the current word
highlighted (timed to the voice); a progress bar runs along the top; a soft whoosh marks each new beat and a pop each
reveal, well under the voice. Everything important sits inside Instagram's safe zone (clear of the top bar, the
caption overlay at the bottom and the buttons on the right). Gaps between beats are trimmed and the sound is
normalised to the loudness of typical phone video (-14 LUFS).

Fonts: Poppins (SIL Open Font License); icons: Noto Emoji images (Apache 2.0) - both bundled in assets/.
"""
from __future__ import annotations

import math
import random
import re
import subprocess
import wave
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .voice import duration, ffmpeg

W, H = 1080, 1920
FPS = 30
ASSETS = Path(__file__).parent / "assets"
# Instagram's overlays: the top bar, the caption/username block at the bottom, the like/comment/share column on the
# right. Key content stays inside x 70-950, y 270-1300.
SAFE_TOP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT = 270, 1300, 70, 950
CX = (SAFE_LEFT + SAFE_RIGHT) // 2 + 25                                   # visual centre: a little left of the middle

STYLE = {  # kind: (label, top colour, bottom colour)
    "hook": ("", (18, 24, 64), (88, 28, 135)),
    "myth": ("MYTH", (70, 14, 30), (150, 40, 50)),
    "truth": ("SACH", (8, 60, 40), (20, 130, 80)),
    "proof": ("HUMARA TEST", (30, 20, 60), (150, 60, 40)),
    "explain": ("SAMJHO", (10, 40, 70), (12, 110, 120)),
    "news": ("NEWS", (12, 30, 40), (30, 90, 60)),
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
# the icon a beat gets when the script names none (or one we do not have)
KIND_EMOJI = {"hook": "🤔", "myth": "❌", "truth": "✅", "proof": "🔍", "explain": "💡", "news": "📰", "history": "⏳",
              "story": "🎬", "twist": "🤯", "takeaway": "🧠", "question": "👇", "lesson": "📚", "research": "🔍",
              "market": "📊", "sector": "🏭", "flows": "💰", "watch": "👀", "disclaimer": "⚖️"}
FOOTER = "Education only  •  Not investment advice"
YELLOW, WHITE, GREEN, RED = (255, 214, 10), (255, 255, 255), (46, 204, 113), (255, 82, 82)
INK = (12, 12, 20)


# ---------------------------------------------------------------- fonts, icons, easing

@lru_cache(maxsize=64)
def font(size: int, bold: bool = True, weight: str = "") -> ImageFont.FreeTypeFont:
    """Poppins: 'black' for headlines, 'bold' (ExtraBold) for captions and labels, 'semi' for small text."""
    weight = weight or ("bold" if bold else "semi")
    name = {"black": "Poppins-Black.ttf", "bold": "Poppins-ExtraBold.ttf", "semi": "Poppins-SemiBold.ttf"}[weight]
    try:
        return ImageFont.truetype(str(ASSETS / "fonts" / name), size)
    except OSError:                                                       # assets missing: the old system font
        import matplotlib
        f = "DejaVuSans-Bold.ttf" if weight != "semi" else "DejaVuSans.ttf"
        return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / f), size)


def _emoji_key(ch: str) -> str:
    return "_".join(f"{ord(c):x}" for c in ch.strip() if ord(c) != 0xFE0F)


def emoji_list() -> List[str]:
    """The icons we have pictures for (the script may pick one per beat)."""
    return ["".join(chr(int(x, 16)) for x in p.stem.split("_")) for p in sorted((ASSETS / "emoji").glob("*.png"))]


def has_emoji(ch: str) -> bool:
    return bool(ch) and (ASSETS / "emoji" / f"{_emoji_key(ch)}.png").exists()


@lru_cache(maxsize=64)
def emoji(ch: str, size: int) -> Optional[Image.Image]:
    p = ASSETS / "emoji" / f"{_emoji_key(ch)}.png"
    if not ch or not p.exists():
        return None
    return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(p: float) -> float:
    p = _clamp(p)
    return 1 - (1 - p) ** 3


def ease_back(p: float) -> float:
    """Overshoots a little, then settles: the 'pop'."""
    p = _clamp(p)
    c = 1.9
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2


def _fade(img: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
    return out


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> List[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if cur and f.getlength(t) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def _text_img(text: str, f: ImageFont.FreeTypeFont, fill, stroke: int = 0, shadow: bool = True) -> Image.Image:
    """One word or line as a transparent picture: thick dark outline and a soft shadow, so it reads on anything."""
    asc, desc = f.getmetrics()
    m = stroke + 14
    w = int(f.getlength(text)) + 2 * m
    h = asc + desc + 2 * m
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((m, m + 7), text, font=f, fill=(0, 0, 0, 150), stroke_width=stroke,
                                stroke_fill=(0, 0, 0, 150))
        img = sh.filter(ImageFilter.GaussianBlur(6))
        d = ImageDraw.Draw(img)
    d.text((m, m), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=INK)
    return img


def _paste(base: Image.Image, img: Image.Image, cx: float, cy: float, scale: float = 1.0, alpha: float = 1.0,
           angle: float = 0.0) -> None:
    """img centred on (cx, cy), scaled, faded and turned."""
    if scale <= 0.02 or alpha <= 0.01:
        return
    if abs(scale - 1) > 0.005:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    if abs(angle) > 0.2:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    img = _fade(img, alpha)
    x, y = int(cx - img.width / 2), int(cy - img.height / 2)
    sx, sy = max(0, -x), max(0, -y)                                      # clip to the frame
    if sx >= img.width or sy >= img.height or x >= base.width or y >= base.height:
        return
    if sx or sy or x + img.width > base.width or y + img.height > base.height:
        img = img.crop((sx, sy, min(img.width, base.width - x), min(img.height, base.height - y)))
    base.alpha_composite(img, (max(0, x), max(0, y)))


# ---------------------------------------------------------------- backgrounds

PAD = 70


@lru_cache(maxsize=24)
def _backdrop(top, bottom) -> Image.Image:
    """A background a little bigger than the frame (it drifts): gradient, soft glows, a faint dot grid, vignette."""
    w, h = W + 2 * PAD, H + 2 * PAD
    y = np.linspace(0, 1, h)[:, None, None]
    top_a, bot_a = np.array(top, float) * 0.85, np.array(bottom, float) * 0.85
    img = np.broadcast_to(top_a + (bot_a - top_a) * y, (h, w, 3)).copy()
    rng = random.Random(hash((top, bottom)) & 0xFFFF)
    yy, xx = np.mgrid[0:h, 0:w]
    glow = np.minimum(255, np.array(bottom, float) * 1.6 + 40)
    for _ in range(3):                                                    # soft light blobs
        gx, gy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(320, 560)
        a = 0.32 * np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * r * r)))[..., None]
        img = img * (1 - a) + glow * a
    grid = ((xx % 54 < 3) & (yy % 54 < 3))[..., None]                     # faint dots: they show the drift
    img = np.where(grid, img * 0.85 + 255 * 0.15, img)
    vx, vy = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)               # darker edges keep the eye in the middle
    img *= (1 - 0.35 * np.clip(vx ** 2 * 0.6 + vy ** 2 * 0.5, 0, 1))[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def _gradient(top, bottom) -> Image.Image:
    return _backdrop(top, bottom).crop((PAD, PAD, PAD + W, PAD + H))


# ---------------------------------------------------------------- one beat

NUMBER_ONLY = re.compile(r"\s*([₹+\-−]?\s?)([\d.,]+)(\s?(?:%|x|X|crore|lakh|cr|L)?)\s*")


@dataclass
class Beat:
    kind: str
    on_screen: str
    words: List[str]
    start: float = 0.0
    secs: float = 1.0
    icon: str = ""
    visual: Optional[dict] = None
    times: List[float] = field(default_factory=list)                     # each word's start, from the beat's start
    first: bool = False
    head: list = field(default_factory=list)                             # (picture, x, y) of each headline word
    head_size: int = 0
    counter: Optional[tuple] = None                                      # (prefix, value, decimals, commas, suffix)


def _accent(kind: str) -> tuple:
    return {"myth": (255, 120, 120), "truth": GREEN, "twist": YELLOW}.get(kind, YELLOW)


def _counter(text: str) -> Optional[tuple]:
    m = NUMBER_ONLY.fullmatch(text)
    if not m or not re.search(r"\d", m.group(2)):
        return None
    raw = m.group(2).rstrip(".,")
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return m.group(1).replace(" ", ""), value, decimals, "," in raw, m.group(3).strip()


def _indian(n: float, decimals: int) -> str:
    s = f"{abs(n):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        whole = f"{head},{tail}"
    return whole + (f".{frac}" if frac else "")


def _count_text(c: tuple, p: float) -> str:
    prefix, value, decimals, commas, suffix = c
    v = value * ease_out(p)
    num = _indian(v, decimals) if commas else f"{v:.{decimals}f}"
    return f"{prefix}{num}{(' ' if suffix.isalpha() and len(suffix) > 1 else '')}{suffix}"


def _layout_headline(b: Beat, top: int, bottom: int) -> None:
    """The headline as separate word pictures (each pops in); numbers and *starred* words in the accent colour."""
    text = b.on_screen.strip()
    words = [w for w in text.upper().split() if w.strip("*")]
    if not words:
        return
    max_w = SAFE_RIGHT - SAFE_LEFT - 30
    rows_max = 2 if b.icon else 3                                         # under an icon: two lines at most
    for size in (118, 104, 92, 80, 70, 62):
        f = font(size, weight="black")
        lines = _wrap(" ".join(w.strip("*") for w in words), f, max_w)
        if len(lines) <= rows_max and all(f.getlength(ln) <= max_w for ln in lines):
            break
    b.head_size = size
    accent = _accent(b.kind)
    space = f.getlength(" ")
    rows, k = [], 0
    for ln in lines:
        row = []
        for w in ln.split():
            src = words[k]
            k += 1
            hot = src.startswith("*") or src.endswith("*") or bool(re.search(r"\d|₹|%", src))
            row.append((_text_img(w, f, accent if hot else WHITE, stroke=5), f.getlength(w)))
        rows.append(row)
    lh = int(size * 1.18)
    y = (top + bottom) / 2 - lh * len(rows) / 2 + lh / 2
    for row in rows:
        total = sum(wl for _, wl in row) + space * (len(row) - 1)
        x = CX - total / 2
        for img, wl in row:
            b.head.append((img, x + wl / 2, y))
            x += wl + space
        y += lh


def _prepare(b: Beat) -> None:
    b.counter = _counter(b.on_screen) if not b.visual else None
    if b.visual:
        _layout_headline_small(b)
    elif b.counter:
        pass
    else:
        _layout_headline(b, 740 if b.icon else 520, 1060)


def _layout_headline_small(b: Beat) -> None:
    """Above a chart: the headline in one or two smaller lines."""
    f = font(66, weight="black")
    lines = _wrap(b.on_screen.upper().replace("*", ""), f, SAFE_RIGHT - SAFE_LEFT - 30)[:2]
    y = 440
    for ln in lines:
        b.head.append((_text_img(ln, f, WHITE, stroke=4), CX, y))
        y += 80
    b.head_size = 66


def _label(kind: str) -> Optional[Image.Image]:
    label, top, _ = STYLE.get(kind, STYLE["explain"])
    if not label:
        return None
    return _pill(label, font(40), WHITE, top)


@lru_cache(maxsize=64)
def _pill(text: str, f, bg, fg) -> Image.Image:
    w = int(f.getlength(text)) + 64
    asc, desc = f.getmetrics()
    h = asc + desc + 22
    img = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 14, w + 10, h + 14], radius=h // 2, fill=(0, 0, 0, 90))
    d.rounded_rectangle([6, 6, w + 6, h + 6], radius=h // 2, fill=bg)
    d.text((38, 6 + 11), text, font=f, fill=fg)
    return img


# ---------------------------------------------------------------- charts

def _draw_bars(img: Image.Image, v: dict, t: float, top: int = 560) -> None:
    """Horizontal bars that grow in, one row each: the name, the bar (green up, red down), the value at its end."""
    items = v["items"][:6]
    if not items:
        return
    d = ImageDraw.Draw(img, "RGBA")
    lf, vf = font(38, weight="bold"), font(38, weight="black")
    col_w = min(360, max(lf.getlength(x[0]) for x in items))
    row = min(130, (1110 - top) / len(items))
    top += ((1110 - top) - row * len(items)) / 2                        # few rows: centred in the space
    big = max(abs(x[1]) for x in items) or 1
    x0 = SAFE_LEFT + 10
    b0, b1 = x0 + col_w + 24, SAFE_RIGHT - 170
    for i, (label, value, text) in enumerate(items):
        p = ease_out((t - 0.15 - 0.12 * i) / 0.6)
        if p <= 0:
            continue
        cy = top + row * (i + 0.5)
        a = int(255 * min(1, p * 2))
        d.text((b0 - 24 - lf.getlength(label), cy - 28), label, font=lf, fill=(255, 255, 255, a))
        bw = max(14, (b1 - b0) * abs(value) / big * p)
        col = GREEN if value >= 0 else RED
        d.rounded_rectangle([b0, cy - 20, b0 + bw, cy + 20], radius=20, fill=col + (235,))
        d.text((b0 + bw + 14, cy - 28), text, font=vf, fill=col + (a,))


def _draw_line(img: Image.Image, v: dict, t: float, top: int = 600) -> None:
    """A line that draws itself left to right, with the area under it and a glowing dot on the last point."""
    vals = [float(x) for x in v["values"] if x is not None]
    if len(vals) < 2:
        return
    left, right, bottom = SAFE_LEFT + 20, SAFE_RIGHT - 20, 1050
    top += 40
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [(left + (right - left) * i / (len(vals) - 1), bottom - (bottom - top) * (x - lo) / span)
           for i, x in enumerate(vals)]
    p = ease_out((t - 0.15) / 1.3)
    n = max(2, int(len(pts) * p))
    col = GREEN if vals[-1] >= vals[0] else RED
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([left - 10, top - 20, right + 10, bottom + 10], fill=(0, 0, 0, 70))
    for gy in (top, (top + bottom) / 2, bottom):
        d.line([(left, gy), (right, gy)], fill=(255, 255, 255, 40), width=2)
    shown = pts[:n]
    d.polygon(shown + [(shown[-1][0], bottom), (shown[0][0], bottom)], fill=col + (60,))
    d.line(shown, fill=col + (255,), width=8, joint="curve")
    ex, ey = shown[-1]
    pulse = 1 + 0.25 * math.sin(t * 6)
    d.ellipse([ex - 22 * pulse, ey - 22 * pulse, ex + 22 * pulse, ey + 22 * pulse], fill=col + (70,))
    d.ellipse([ex - 11, ey - 11, ex + 11, ey + 11], fill=WHITE + (255,))
    sf = font(32, weight="semi")
    if v.get("from"):
        d.text((left, bottom + 18), v["from"], font=sf, fill=(230, 230, 230, 220))
    if v.get("to"):
        d.text((right - sf.getlength(v["to"]), bottom + 18), v["to"], font=sf, fill=(230, 230, 230, 220))
    if p >= 0.98 and v.get("last"):
        lf = font(44, weight="black")
        tw = lf.getlength(v["last"])
        bx = min(right - tw - 30, max(left, ex - tw / 2 - 15))
        by = ey - 100 if ey - 100 > top - 40 else ey + 30
        d.rounded_rectangle([bx, by, bx + tw + 30, by + 66], radius=20, fill=col + (240,))
        d.text((bx + 15, by + 4), v["last"], font=lf, fill=INK + (255,))
    img.alpha_composite(layer)


# ---------------------------------------------------------------- captions

@lru_cache(maxsize=256)
def _caption_img(group: Tuple[str, ...], current: int) -> Image.Image:
    """Up to 3 spoken words, the current one in yellow, in big outlined letters (no box)."""
    cf = font(64, weight="bold")
    space = cf.getlength(" ")
    lines, line, width = [], [], 0.0
    for i, w in enumerate(group):
        wl = cf.getlength(w)
        if line and width + space + wl > SAFE_RIGHT - SAFE_LEFT - 20:
            lines.append(line)
            line, width = [], 0.0
        line.append((i, w, wl))
        width += (space if width else 0) + wl
    if line:
        lines.append(line)
    lh = int(cf.size * 1.25)
    img = Image.new("RGBA", (SAFE_RIGHT - SAFE_LEFT + 40, lh * len(lines) + 40), (0, 0, 0, 0))
    y = 10
    for ln in lines:
        total = sum(x[2] for x in ln) + space * (len(ln) - 1)
        x = (img.width - total) / 2
        for i, w, wl in ln:
            word = _text_img(w, cf, YELLOW if i == current else WHITE, stroke=6)
            img.alpha_composite(word, (int(x) - 20, y - 20))
            x += wl + space
        y += lh
    return img


def word_times(words: List[str], secs: float) -> List[float]:
    """How long each word stays highlighted: the beat's audio time shared by word length (a good fit for one voice)."""
    weights = [len(w) + 2 for w in words] or [1]
    total = sum(weights)
    return [secs * w / total for w in weights]


# ---------------------------------------------------------------- the frame

def _draw(b: Beat, t: float, progress: float, top_line: str = "", handle: str = "") -> Image.Image:
    """Beat b at t seconds into it."""
    label, top, bottom = STYLE.get(b.kind, STYLE["explain"])
    bg = _backdrop(top, bottom)
    drift = (b.start + t) * 0.6                                           # the background drifts slowly
    ox = int(PAD + PAD * 0.9 * math.sin(drift * 0.35))
    oy = int(PAD + PAD * 0.9 * math.cos(drift * 0.27))
    img = bg.crop((ox, oy, ox + W, oy + H)).convert("RGBA")
    a = 1.0 if b.first else ease_out(t / 0.18)                            # the first frame is already complete
    tt = t + (0.6 if b.first else 0.0)

    if top_line:
        _paste(img, _pill(top_line, font(34), (0, 0, 0, 120), (240, 240, 240)), CX, SAFE_TOP + 10, alpha=0.95)
    lab = _label(b.kind)
    if lab is not None:
        _paste(img, lab, CX, SAFE_TOP + 95 - 30 * (1 - ease_out(tt / 0.3)), alpha=ease_out(tt / 0.3))

    if b.visual:
        (_draw_line if b.visual.get("type") == "line" else _draw_bars)(img, b.visual, tt, 520 + 80 * len(b.head))
    else:
        icon = emoji(b.icon, 280) if b.icon else None
        if icon is not None:
            p = (tt - 0.05) / 0.4
            bob = 12 * math.sin(tt * 2.6) if p >= 1 else 0
            iy = 560
            _paste(img, icon, CX, iy + bob, scale=ease_back(p), alpha=min(1.0, p * 3))
        if b.counter:
            p = (tt - 0.12) / 0.9
            txt = _count_text(b.counter, p)
            f = font(200 if len(txt) <= 6 else 150 if len(txt) <= 9 else 112, weight="black")
            _paste(img, _text_img(txt, f, _accent(b.kind), stroke=6), CX, 900 if icon is not None else 760,
                   scale=0.85 + 0.15 * ease_back((tt - 0.1) / 0.3), alpha=min(1.0, max(0.0, (tt - 0.05) * 6)))
    for i, (word, x, y) in enumerate(b.head):
        p = (tt - 0.12 - 0.07 * i) / 0.28
        _paste(img, word, x, y, scale=0.55 + 0.45 * ease_back(p), alpha=min(1.0, p * 2.5))

    if b.words:                                                          # spoken words, 3 at a time
        cur = 0
        while cur + 1 < len(b.times) and b.times[cur + 1] <= t:
            cur += 1
        start = (cur // 3) * 3
        cap = _caption_img(tuple(b.words[start:start + 3]), cur - start)
        pop = 1 + 0.06 * (1 - ease_out((t - b.times[cur]) / 0.12)) if b.times else 1
        _paste(img, cap, CX, 1190, scale=pop)

    ff = font(28, weight="semi")
    foot = FOOTER + (f"  •  {handle}" if handle else "")
    d = ImageDraw.Draw(img, "RGBA")
    d.text((CX - ff.getlength(foot) / 2, 1290), foot, font=ff, fill=(235, 235, 235, 200))
    d.rectangle([0, 0, W, 12], fill=(0, 0, 0, 160))                       # progress bar
    d.rectangle([0, 0, int(W * _clamp(progress)), 12], fill=YELLOW + (255,))

    if a < 1:                                                            # a quick zoom-settle on each new beat
        s = 1 + 0.06 * (1 - a)
        cw, ch = W / s, H / s
        img = img.crop((int((W - cw) / 2), int((H - ch) / 2), int((W + cw) / 2), int((H + ch) / 2))).resize(
            (W, H), Image.BILINEAR)
    if b.kind == "twist" and t < 0.3:                                    # a short shake on the twist
        k = (0.3 - t) / 0.3
        img = img.transform(img.size, Image.AFFINE, (1, 0, 14 * k * math.sin(t * 90), 0, 1, 10 * k * math.cos(t * 70)))
    return img.convert("RGB")


def frame(kind: str, on_screen: str, words: Sequence[str] = (), current: int = -1, progress: float = 0.0,
          top_line: str = "", handle: str = "", caption: str = "", icon: str = "", visual: Optional[dict] = None,
          at: float = 2.0) -> Image.Image:
    """One still picture of a beat, `at` seconds in (previews and tests). caption: plain text instead of words."""
    words = list(words) or caption.split()
    b = Beat(kind, on_screen, words, secs=max(at, 1.0), icon=icon or KIND_EMOJI.get(kind, ""), visual=visual)
    b.times = [0.0] * len(words)
    if words and current >= 0:
        b.times = [0.0 if i <= current else at + 1 for i in range(len(words))]
    _prepare(b)
    return _draw(b, at, progress, top_line, handle)


# ---------------------------------------------------------------- sound effects

SR = 44100


def _sfx(kind: str) -> np.ndarray:
    """Short effects made here (no licence questions): a soft whoosh, a pop and a ding."""
    rng = np.random.default_rng(7)
    if kind == "whoosh":
        n = int(SR * 0.38)
        x = rng.standard_normal(n)
        out, y, env = np.zeros(n), 0.0, np.sin(np.linspace(0, np.pi, n)) ** 2
        for i in range(n):                                                # a low-pass that opens and closes
            a = 0.02 + 0.25 * env[i]
            y += a * (x[i] - y)
            out[i] = y
        return out / (np.abs(out).max() or 1) * env
    if kind == "pop":
        n = int(SR * 0.09)
        tt = np.arange(n) / SR
        f = 900 - 5000 * tt
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 45)
    n = int(SR * 0.7)                                                     # ding
    tt = np.arange(n) / SR
    return (np.sin(2 * np.pi * 1568 * tt) + 0.5 * np.sin(2 * np.pi * 2349 * tt)) / 1.5 * np.exp(-tt * 6)


def sfx_track(beats: List[Beat], total: float, out: Path) -> Path:
    """Whoosh on each new beat, pop when an icon or chart appears, ding when a number has counted up - quiet."""
    track = np.zeros(int(SR * (total + 1)))
    fx = {k: _sfx(k) for k in ("whoosh", "pop", "ding")}

    def add(name, at, gain):
        s = fx[name] * gain
        i = int(at * SR)
        j = min(len(track), i + len(s))
        if 0 <= i < len(track):
            track[i:j] += s[:j - i]
    for k, b in enumerate(beats):
        if k:
            add("whoosh", max(0.0, b.start - 0.12), 0.10)
        if b.kind == "disclaimer":
            continue
        if b.counter:
            add("ding", b.start + 1.02, 0.07)
        elif b.visual or (b.icon and k):
            add("pop", b.start + 0.1, 0.09)
    pcm = (np.clip(track, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out


# ---------------------------------------------------------------- the video

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


def build(scenes: Sequence[tuple], out: Path, work: Path, handle: str = "", top_line: str = "",
          fast: bool = False) -> Path:
    """scenes: (kind, on_screen, narration, audio file[, extras]) - extras: {"icon": emoji, "visual": chart}.
    30 frames a second, streamed straight into ffmpeg; the voice and the effects are mixed and normalised."""
    work.mkdir(parents=True, exist_ok=True)
    clips = [tighten(sc[3], work / f"t{i:02d}.wav") for i, sc in enumerate(scenes)]
    secs = [max(1.0, duration(c)) for c in clips]
    beats, at = [], 0.0
    for i, (sc, s) in enumerate(zip(scenes, secs)):
        kind, on_screen, narration = sc[0], sc[1], sc[2]
        extra = sc[4] if len(sc) > 4 and sc[4] else {}
        icon = extra.get("icon") or ""
        icon = icon if has_emoji(icon) else KIND_EMOJI.get(kind, "")
        b = Beat(kind, on_screen, narration.split(), at, s, icon, extra.get("visual"), first=i == 0)
        wt = word_times(b.words, s)
        b.times = [sum(wt[:j]) for j in range(len(wt))]
        _prepare(b)
        beats.append(b)
        at += s
    total = at
    ff = ffmpeg()
    (work / "audio.txt").write_text("\n".join(f"file '{c.resolve()}'" for c in clips))
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", "audio.txt", "-ac", "1", "-ar", "44100", "voice.wav"],
                   cwd=work, capture_output=True, check=True)
    sfx_track(beats, total, work / "sfx.wav")
    subprocess.run([ff, "-y", "-i", "voice.wav", "-i", "sfx.wav", "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-ac", "1", "-ar", "44100", "audio.m4a"], cwd=work, capture_output=True, check=True)
    n = int(math.ceil(total * FPS)) + 2
    enc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
         "-i", "audio.m4a", "-c:v", "libx264", "-preset", "veryfast" if fast else "faster", "-crf", "21",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out.resolve())],
        cwd=work, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    k = 0
    try:
        for f in range(n):
            t = f / FPS
            while k + 1 < len(beats) and beats[k + 1].start <= t:
                k += 1
            b = beats[k]
            img = _draw(b, t - b.start, t / total, top_line if k == 0 or b.kind == "hook" else "", handle)
            enc.stdin.write(img.tobytes())
        enc.stdin.close()
    except BrokenPipeError:
        pass
    err = enc.stderr.read().decode(errors="replace")
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")
    return out


# ---------------------------------------------------------------- stills: story card, cover, carousel

def _centered(d: ImageDraw.ImageDraw, lines: List[str], f, y: int, fill, gap: int = 18, cx: float = W / 2) -> int:
    for line in lines:
        w = d.textlength(line, font=f)
        d.text((cx - w / 2, y), line, font=f, fill=fill)
        y += f.size + gap
    return y


def _big(on_screen: str) -> Tuple[List[str], ImageFont.FreeTypeFont]:
    """A short number ('93%', '₹1.8 lakh crore') is shown huge; other text large and wrapped."""
    text = on_screen.upper().replace("*", "")
    if re.fullmatch(r"[₹+\-−]?\s?[\d.,]+\s?%?", text.strip()):
        return [text.strip()], font(230, weight="black")
    for size in (104, 92, 80, 70):
        f = font(size, weight="black")
        lines = _wrap(text, f, W - 160)
        if len(lines) <= 3:
            return lines, f
    return _wrap(text, f, W - 160)[:4], f


def story_card(headline: str, lines: List[str], source: str, out: Path, label: str = "ABHI ABHI") -> Path:
    """A 1080x1920 Instagram Story card: label, headline, up to 4 lines, the official source - ready in a second."""
    img = _gradient((90, 10, 20), (20, 20, 40)).convert("RGBA")
    _paste(img, _pill(label, font(44), YELLOW, (20, 20, 20)), W / 2, 300)
    hf = font(84, weight="black")
    y = 420
    for ln in _wrap(headline.upper(), hf, W - 160)[:5]:
        _paste(img, _text_img(ln, hf, WHITE, stroke=4), W / 2, y + 50)
        y += 104
    d = ImageDraw.Draw(img)
    bf = font(44, weight="semi")
    for line in lines[:4]:
        y = _centered(d, _wrap("• " + line, bf, W - 180)[:3], bf, y + 40, (235, 235, 235), gap=10)
    _centered(d, ["Source: " + source, FOOTER], font(32, weight="semi"), 1640, (220, 220, 220), gap=12)
    img.convert("RGB").save(out)
    return out


SERIES_COLOURS = {"MYTH vs SACH": ((70, 14, 30), (150, 40, 50)), "MARKET AAJ": ((8, 30, 60), (20, 80, 140)),
                  "RAAT KI REPORT": ((15, 15, 45), (60, 30, 110)), "NEWS SAMJHO": ((12, 30, 40), (30, 90, 60)),
                  "MARKET KI KAHANI": ((25, 18, 40), (90, 50, 110)), "COMPANY KI KUNDLI": ((40, 30, 10), (130, 90, 20)),
                  "PAISA KI PATHSHALA": ((10, 60, 50), (20, 130, 100)),
                  "BREAKING SAMJHO": ((90, 10, 20), (20, 20, 40)), "ZAROORI KHABAR": ((90, 10, 20), (20, 20, 40))}


def cover(series: str, episode: int, hook: str, out: Path, handle: str = "", icon: str = "") -> Path:
    """The Reel's cover for the profile grid: the beat's icon, the series in its colour, the hook in big letters, the
    episode. Everything sits in the middle 1080x1440, which the profile grid shows."""
    top, bottom = SERIES_COLOURS.get(series, ((18, 24, 64), (88, 28, 135)))
    img = _gradient(top, bottom).convert("RGBA")
    pic = emoji(icon, 230) if icon else None
    if pic is not None:
        _paste(img, pic, W / 2, 440)
    _paste(img, _pill(series, font(48), YELLOW, (20, 20, 20)), W / 2, 640)
    lines, f = _big(hook)
    if f.size > 120:                                                      # a bare number: keep it, smaller
        f = font(180, weight="black")
    y = 760
    for ln in lines:
        im = _text_img(ln, f, WHITE, stroke=5)
        _paste(img, im, W / 2, y + im.height / 2 - 14)
        y += int(f.size * 1.15)
    _paste(img, _pill(f"EP {episode}", font(46), (0, 0, 0, 150), WHITE), W / 2, max(y + 70, 1240))
    if handle:
        d = ImageDraw.Draw(img)
        _centered(d, [handle], font(36, weight="semi"), 1560, (230, 230, 230))
    img.convert("RGB").save(out)
    return out


CW, CH = 1080, 1350                                                       # Instagram's 4:5 carousel


def slide(title: str, lines: List[str], idx: int, total: int, out: Path, handle: str = "",
          colours=((8, 30, 60), (20, 80, 140))) -> Path:
    """One carousel slide: title, up to 6 lines, page number, footer."""
    img = _backdrop(colours[0], colours[1]).crop((PAD, PAD, PAD + CW, PAD + CH)).convert("RGBA")
    d = ImageDraw.Draw(img)
    tf, bf = font(72, weight="black"), font(48, weight="semi")
    titles = _wrap(title.upper(), tf, CW - 140)[:3]
    body = [_wrap(("• " + line), bf, CW - 170)[:3] for line in lines[:6]]
    height = len(titles) * (tf.size + 14) + 50 + sum(len(b) * (bf.size + 12) + 26 for b in body)
    y = max(90, (CH - 120 - height) // 2)                                  # the block centred on the slide
    for line in titles:
        _paste(img, _text_img(line, tf, YELLOW, stroke=3), CW / 2, y + tf.size * 0.62)
        y += tf.size + 14
    y += 50
    for parts in body:
        for i, part in enumerate(parts):
            d.text((80, y), ("" if i == 0 else "   ") + part, font=bf, fill=WHITE)
            y += bf.size + 12
        y += 26
    foot = f"{idx}/{total}  •  {FOOTER}" + (f"  •  {handle}" if handle else "")
    ff = font(28, weight="semi")
    d.text(((CW - d.textlength(foot, font=ff)) / 2, CH - 80), foot, font=ff, fill=(220, 220, 220))
    img.convert("RGB").save(out)
    return out
