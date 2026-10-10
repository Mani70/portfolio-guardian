"""Extras that drive engagement around each Reel: the daily carousel, the Story poll card and the posting checklist.

Why (trader/reel/RESEARCH.md): Instagram ranks on watch time, sends and likes per reach; carousels get swiped and
saved; Story polls are the cheapest interaction; a consistent grid of branded covers turns profile visits into follows.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from . import render

# music to search in Instagram's library, per series (instrumental only, 10-15% volume)
MUSIC = {"MYTH vs SACH": "lofi / quirky", "PAISA KI PATHSHALA": "study / lofi", "MARKET AAJ": "news / corporate", "RAAT KI REPORT": "calm / ambient",
         "NEWS SAMJHO": "news / corporate", "MARKET KI KAHANI": "suspense / thriller",
         "COMPANY KI KUNDLI": "documentary / inspiring", "BREAKING SAMJHO": "news / urgent",
         "ZAROORI KHABAR": "news / urgent"}


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}%"


def carousel(day: dict, out_dir: Path, handle: str = "") -> List[Path]:
    """'Aaj ka market in N slides' from the 19:15 compilation: scoreboard, sectors, money flows, news, results,
    chart reading, next session + the poll. Facts only, like the brief."""
    d = datetime.fromisoformat(day["date"])
    slides = []
    b = day["broad"]
    lines = [f"{n}: {v['close']:,.0f} ({_pct(v['pct'])})" for n, v in b.items()]
    adv, dec = day["breadth"]
    if adv is not None:
        lines.append(f"Nifty 500: {int(adv)} shares up, {int(dec)} down")
    slides.append((f"Aaj ka market - {d:%d %b}", lines + ["Swipe karo →"]))
    if day["sectors"]:
        s = day["sectors"]
        slides.append(("Sectors: kaun aage, kaun peeche", [f"Aage: {n} {_pct(p)}" for n, p in s[::-1][:3]] +
                       [f"Peeche: {n} {_pct(p)}" for n, p in s[:3]]))
    money = []
    if day.get("fii") is not None:
        money.append(f"Videshi investors (FII): {'kharida' if day['fii'] >= 0 else 'becha'} "
                     f"₹{abs(day['fii']):,.0f} crore (net)")
    if day.get("dii") is not None:
        money.append(f"Indian funds (DII): {'kharida' if day['dii'] >= 0 else 'becha'} ₹{abs(day['dii']):,.0f} crore")
    vix, vixp = day["vix"]
    if vix is not None:
        money.append(f"India VIX (darr ka meter): {vix:.1f} ({_pct(vixp)}) - zyada = zyada ghabrahat")
    if money:
        slides.append(("Paisa kisne lagaya?", money + ["(provisional data, NSE)"]))
    news = [f"{it['headline']}" for it in day.get("news", [])[:3]]
    if news:
        slides.append(("Aaj ki khabar (trusted sources)", news))
    res = [f"{r['company']}: " + "; ".join(p["text"] for p in r["points"][:2]) for r in day.get("results", [])[:3]]
    if res:
        slides.append(("Results aaj", res + ["Business numbers - share ki baat nahi"]))
    if day.get("chart"):
        slides.append(("Chart kya keh raha hai", day["chart"][:4] + ["Chart batata hai kya hua, kya hoga nahi"]))
    tail = list(day.get("calendar", [])) + ["Poll: kal Nifty UP ya DOWN? Comment karo 👇",
                                            "🔖 Save karo  •  📤 Dost ko bhejo"]
    slides.append(("Kal kya dekhna hai", tail))
    out = []
    for i, (title, ls) in enumerate(slides, 1):
        out.append(render.slide(title, ls, i, len(slides), out_dir / f"slide_{d:%Y%m%d}_{i}.png", handle))
    return out


def poll_card(next_session: str, out_dir: Path) -> Path:
    nd = datetime.fromisoformat(next_session)
    return render.story_card(f"Kal ({nd:%a}) Nifty: UP ya DOWN?",
                             ["Neeche poll sticker lagao 👇", "Jawab kal shaam MARKET AAJ mein"], "NSE closing data",
                             out_dir / f"poll_{nd:%Y%m%d}.png", "AAPKA ANUMAAN")


def checklist(series: str, question: str, cover: Path) -> str:
    return ("✅ Posting checklist (2 minutes):\n"
            f"1. Cover: use the image above ({cover.name}) - Edit cover → Add from camera roll\n"
            f"2. Music: search '{MUSIC.get(series, 'calm instrumental')}', instrumental, volume 10-15%\n"
            "3. Paste the caption; turn on the AI label\n"
            f"4. After posting, comment and pin: \"{question}\"\n"
            "5. Reply to the first comments for 10 minutes")
