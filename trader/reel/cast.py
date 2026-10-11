"""Our recurring cast: flat 2D drawings of everyday Indians, plus SACHI, the channel's narrator - drawn in code, so
there are no model releases, no stock-photo rules and nothing that looks like a real (or deepfake) person.

Why (reports/Short form finance audience growth.md): faces draw attention and comments, and the faceless accounts that
grew built a recurring character; photo-real AI people and AI presenters carry labelling rules and look like the
deepfake tip scams NSE warns about. A drawn, clearly fictional cast gives the Reels a "who" without that risk. The
bio says Sachi is an animated narrator with an AI voice.

Each character has six expressions (smile, curious, worried, shocked, relieved, proud) and a talking mouth.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw

S = 3                                                                     # drawn 3x larger, then scaled down (smooth)
BW, BH = 480, 560                                                         # the bust's size on screen


@dataclass(frozen=True)
class Person:
    name: str
    who: str                       # for the script: who this is
    skin: Tuple[int, int, int]
    hair: Tuple[int, int, int]
    hair_style: str                # bun, long, short, receding, side
    top: Tuple[int, int, int]      # clothes
    collar: str = "round"          # round, v, kurta, saree
    glasses: bool = False
    bindi: bool = False
    moustache: bool = False
    stubble: bool = False
    age: str = "young"             # young, middle


CAST: Dict[str, Person] = {
    "Sachi": Person("Sachi", "our narrator: a friendly young woman who explains money simply (beat 1 and the last beat)",
                    (214, 160, 120), (40, 28, 26), "bun", (16, 150, 136), "kurta", glasses=True),
    "Priya": Person("Priya", "24, just got her first salary, curious, wants to start investing",
                    (226, 176, 136), (52, 34, 30), "long", (236, 98, 140), "round"),
    "Rahul": Person("Rahul", "27, works in an office, tempted by quick F&O / intraday money",
                    (196, 140, 100), (30, 26, 24), "short", (52, 102, 196), "v", stubble=True),
    "Sharma ji": Person("Sharma ji", "Papa, 55, trusts only FDs and gold, careful with money",
                        (204, 150, 110), (190, 190, 190), "receding", (240, 236, 226), "kurta", moustache=True,
                        age="middle"),
    "Sunita": Person("Sunita", "Mummy, 50, runs the household budget, saves in small amounts",
                     (210, 154, 112), (36, 28, 26), "bun_low", (170, 30, 70), "saree", bindi=True, age="middle"),
    "Raju": Person("Raju", "40, runs a kirana shop, thinks about business, cash and loans",
                   (176, 120, 84), (26, 22, 20), "side", (236, 140, 40), "kurta", moustache=True),
}
EXPRESSIONS = ("smile", "curious", "worried", "shocked", "relieved", "proud")
INK = (40, 26, 24)


def _shade(c, k: float):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _e(d: ImageDraw.ImageDraw, box, **kw):
    d.ellipse([v * S for v in box], **kw)


def _poly(d, pts, **kw):
    d.polygon([(x * S, y * S) for x, y in pts], **kw)


def _line(d, pts, w, **kw):
    d.line([(x * S, y * S) for x, y in pts], width=int(w * S), joint="curve", **kw)
    for x, y in (pts[0], pts[-1]):                                       # round caps
        r = w * S / 2
        d.ellipse([x * S - r, y * S - r, x * S + r, y * S + r], fill=kw.get("fill"))


def _arc(d, box, start, end, w, fill):
    d.arc([v * S for v in box], start, end, fill=fill, width=int(w * S))


@lru_cache(maxsize=256)
def bust(name: str, expression: str = "smile", talking: bool = False, blink: bool = False) -> Image.Image:
    """The character from the chest up, transparent background, BW x BH."""
    p = CAST.get(name) or CAST["Sachi"]
    ex = expression if expression in EXPRESSIONS else "smile"
    img = Image.new("RGBA", (BW * S, BH * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, hy = BW / 2, 235                                                  # head centre

    # back hair (long hair falls behind the shoulders)
    if p.hair_style == "long":
        _e(d, (cx - 128, hy - 140, cx + 128, hy + 200), fill=p.hair)
    if p.hair_style == "bun_low":                                         # the bun behind the head
        _e(d, (cx + 62, hy - 70, cx + 142, hy + 10), fill=p.hair)
    # body
    _e(d, (cx - 200, 400, cx + 200, 730), fill=p.top)
    if p.collar == "saree":                                               # the pallu across the shoulder
        _poly(d, [(cx + 40, 400), (cx + 150, 415), (cx - 60, 530), (cx - 130, 490)], fill=_shade(p.top, 0.78))
        _poly(d, [(cx - 120, 425), (cx - 60, 530), (cx - 70, 440)], fill=(230, 180, 60))
    # neck
    d.rounded_rectangle([(cx - 34) * S, (hy + 90) * S, (cx + 34) * S, 420 * S], radius=14 * S, fill=p.skin)
    _e(d, (cx - 36, hy + 112, cx + 36, hy + 150), fill=_shade(p.skin, 0.88))     # shadow under the chin
    if p.collar == "v":
        _poly(d, [(cx - 50, 402), (cx, 462), (cx + 50, 402)], fill=p.skin)
    elif p.collar == "kurta":
        _poly(d, [(cx - 46, 406), (cx, 422), (cx + 46, 406), (cx + 30, 414), (cx, 440), (cx - 30, 414)],
              fill=_shade(p.top, 0.82))
        _line(d, [(cx, 422), (cx, 490)], 4, fill=_shade(p.top, 0.7))
        for by in (444, 470):
            _e(d, (cx - 5, by - 5, cx + 5, by + 5), fill=_shade(p.top, 0.6))
    else:
        _e(d, (cx - 52, 390, cx + 52, 432), fill=p.skin)
    # ears and head
    for side in (-1, 1):
        _e(d, (cx + side * 112 - 18, hy - 10, cx + side * 112 + 18, hy + 40), fill=_shade(p.skin, 0.94))
    _e(d, (cx - 112, hy - 130, cx + 112, hy + 135), fill=p.skin)
    # hair on top
    hs = p.hair_style
    if hs in ("bun", "bun_low", "long"):
        _poly(d, [(cx - 116, hy - 10), (cx - 108, hy - 95), (cx - 60, hy - 138), (cx + 10, hy - 148),
                  (cx + 80, hy - 128), (cx + 116, hy - 70), (cx + 118, hy - 5), (cx + 92, hy - 60),
                  (cx + 30, hy - 92), (cx - 8, hy - 70), (cx - 60, hy - 86), (cx - 98, hy - 40)], fill=p.hair)
        _line(d, [(cx - 8, hy - 140), (cx - 8, hy - 76)], 3, fill=_shade(p.hair, 1.6))   # the parting
        if hs == "bun":
            _e(d, (cx - 58, hy - 205, cx + 58, hy - 100), fill=p.hair)
    elif hs == "short":
        _poly(d, [(cx - 116, hy - 20), (cx - 112, hy - 100), (cx - 70, hy - 145), (cx - 20, hy - 160),
                  (cx + 30, hy - 158), (cx + 80, hy - 140), (cx + 116, hy - 95), (cx + 116, hy - 25),
                  (cx + 96, hy - 70), (cx + 40, hy - 92), (cx - 20, hy - 88), (cx - 80, hy - 78),
                  (cx - 102, hy - 40)], fill=p.hair)
    elif hs == "side":
        _poly(d, [(cx - 116, hy - 25), (cx - 110, hy - 105), (cx - 50, hy - 148), (cx + 40, hy - 150),
                  (cx + 104, hy - 115), (cx + 118, hy - 40), (cx + 100, hy - 80), (cx + 20, hy - 108),
                  (cx - 70, hy - 98), (cx - 100, hy - 55)], fill=p.hair)
    elif hs == "receding":                                               # grey at the sides, thin on top
        for side in (-1, 1):
            _poly(d, [(cx + side * 113, hy - 2), (cx + side * 114, hy - 60), (cx + side * 96, hy - 100),
                      (cx + side * 90, hy - 62), (cx + side * 100, hy - 20)], fill=p.hair)
        d.chord([(cx - 104) * S, (hy - 140) * S, (cx + 104) * S, (hy - 40) * S], 200, 340, fill=_shade(p.skin, 0.95))
    if p.stubble:
        _e(d, (cx - 92, hy + 30, cx + 92, hy + 140), fill=_shade(p.skin, 0.86))
        _e(d, (cx - 84, hy + 10, cx + 84, hy + 110), fill=p.skin)

    # face
    ey, ex_ = hy + 5, 46                                                  # eye line, eye spacing
    look = {"curious": 7}.get(ex, 0)
    brow_y = ey - 42
    # eyebrows
    bw = 6 if p.age == "young" else 8
    for side in (-1, 1):
        x0, x1 = cx + side * (ex_ - 24), cx + side * (ex_ + 26)
        if ex == "worried":
            pts = [(x0, brow_y - 10), (x1, brow_y + 4)]
        elif ex == "shocked":
            pts = [(x0, brow_y - 16), (x1, brow_y - 14)]
        elif ex == "curious" and side == 1:
            pts = [(x0, brow_y - 16), (x1, brow_y - 22)]
        elif ex == "proud":
            pts = [(x0, brow_y - 4), (x1, brow_y - 8)]
        else:
            pts = [(x0, brow_y), (x1, brow_y - 4)]
        _line(d, pts, bw, fill=_shade(p.hair, 0.9) if p.hair[0] > 120 else p.hair)
    # eyes
    for side in (-1, 1):
        ex0 = cx + side * ex_
        if blink or ex == "relieved":
            _arc(d, (ex0 - 16, ey - 10, ex0 + 16, ey + 12), 20, 160, 5, INK)
        elif ex == "proud":
            _arc(d, (ex0 - 16, ey - 6, ex0 + 16, ey + 18), 200, 340, 5, INK)
        elif ex == "shocked":
            _e(d, (ex0 - 18, ey - 20, ex0 + 18, ey + 20), fill=(255, 255, 255))
            _e(d, (ex0 - 7, ey - 7, ex0 + 7, ey + 7), fill=INK)
        else:
            _e(d, (ex0 - 11 + look, ey - 14, ex0 + 11 + look, ey + 14), fill=INK)
            _e(d, (ex0 - 4 + look, ey - 9, ex0 + 3 + look, ey - 2), fill=(255, 255, 255))
    if p.glasses:
        for side in (-1, 1):
            ex0 = cx + side * ex_
            d.rounded_rectangle([(ex0 - 34) * S, (ey - 26) * S, (ex0 + 34) * S, (ey + 26) * S], radius=16 * S,
                                outline=(30, 30, 40), width=5 * S)
        _line(d, [(cx - 12, ey - 6), (cx + 12, ey - 6)], 4, fill=(30, 30, 40))
    # nose and cheeks
    _arc(d, (cx - 12, ey + 18, cx + 12, ey + 48), 30, 150, 4, _shade(p.skin, 0.72))
    blush = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    for side in (-1, 1):
        _e(bd, (cx + side * 70 - 22, ey + 34, cx + side * 70 + 22, ey + 56), fill=(240, 110, 110, 70))
    img.alpha_composite(blush)
    d = ImageDraw.Draw(img)
    if p.bindi:
        _e(d, (cx - 8, brow_y - 8, cx + 8, brow_y + 8), fill=(200, 20, 40))
    if p.moustache:
        _poly(d, [(cx - 46, ey + 64), (cx - 10, ey + 52), (cx, ey + 58), (cx + 10, ey + 52), (cx + 46, ey + 64),
                  (cx + 20, ey + 70), (cx, ey + 64), (cx - 20, ey + 70)], fill=p.hair if p.hair[0] < 120 else
              (150, 150, 150))
    # mouth
    my = ey + 82
    lips = (150, 60, 60)
    if talking:
        _e(d, (cx - 20, my - 10, cx + 20, my + 22), fill=(110, 30, 40))
        _e(d, (cx - 12, my + 8, cx + 12, my + 20), fill=(230, 110, 110))
    elif ex == "shocked":
        _e(d, (cx - 16, my - 8, cx + 16, my + 30), fill=(110, 30, 40))
    elif ex == "worried":
        _arc(d, (cx - 26, my + 2, cx + 26, my + 30), 200, 340, 5, lips)
    elif ex == "curious":
        _line(d, [(cx - 18, my + 6), (cx + 16, my + 2)], 5, fill=lips)
    elif ex in ("proud", "smile"):
        d.chord([(cx - 34) * S, (my - 22) * S, (cx + 34) * S, (my + 22) * S], 0, 180, fill=(110, 30, 40))
        d.chord([(cx - 26) * S, (my - 4) * S, (cx + 26) * S, (my + 14) * S], 0, 180, fill=(255, 255, 255))
    else:                                                                # relieved
        _arc(d, (cx - 24, my - 16, cx + 24, my + 14), 20, 160, 5, lips)
    if ex == "worried":                                                   # a sweat drop
        _poly(d, [(cx + 118, hy - 70), (cx + 108, hy - 40), (cx + 128, hy - 40)], fill=(120, 190, 255))
        _e(d, (cx + 106, hy - 52, cx + 130, hy - 28), fill=(120, 190, 255))
    return img.resize((BW, BH), Image.LANCZOS)


@lru_cache(maxsize=256)
def badge(name: str, expression: str = "smile", talking: bool = False, blink: bool = False,
          size: int = 400) -> Image.Image:
    """The bust inside a round badge (a soft disc with a white ring), the way avatars are shown."""
    b = bust(name, expression, talking, blink)
    d = size
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    disc = Image.new("RGBA", (d * 2, d * 2), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    dd.ellipse([0, 0, d * 2 - 1, d * 2 - 1], fill=(255, 255, 255, 255))
    dd.ellipse([12, 12, d * 2 - 13, d * 2 - 13], fill=(250, 236, 214, 255))
    disc = disc.resize((d, d), Image.LANCZOS)
    out.alpha_composite(disc)
    scale = d * 0.84 / BW
    face = b.resize((int(BW * scale), int(BH * scale)), Image.LANCZOS)
    layer = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    layer.alpha_composite(face, ((d - face.width) // 2, int(d * 0.02)))
    mask = Image.new("L", (d * 2, d * 2), 0)
    ImageDraw.Draw(mask).ellipse([12, 12, d * 2 - 13, d * 2 - 13], fill=255)
    mask = mask.resize((d, d), Image.LANCZOS)
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", (d, d), 0), mask))
    out.alpha_composite(layer)
    return out


def card(name: str) -> str:
    p = CAST[name]
    return f"{p.name} - {p.who}"


def prompt_block() -> str:
    """What the script writer is told about the cast (only in weeks with characters)."""
    people = "\n".join(f"- {card(n)}" for n in CAST)
    return f"""OUR CAST (drawn cartoon characters, clearly fictional): use them to make beats human.
{people}
- Beat 1 and the last beat: character "Sachi" (our narrator) with a fitting expression.
- Scenario beats ("Priya ki pehli salary aayi...", "Rahul ne socha..."): put that character on the beat; 2-4 such beats per Reel, at most 3 different people. Their names may be said in the narration.
- expression is one of: {", ".join(EXPRESSIONS)} - it must match what the beat says (no shocked face on a calm line).
- A character is NEVER shown as a winner, never next to a return or profit figure, never as a scam victim who is mocked, and never as a real person. Beats with charts or a single big number get no character.
- Beats without a character leave "character" empty and use their icon."""


def pick(name: str) -> Optional[str]:
    """The cast member called `name` (case and 'ji' forgiven), or None."""
    n = (name or "").strip().lower()
    for k in CAST:
        if n and (n == k.lower() or n.split()[0] == k.lower().split()[0]):
            return k
    return None
