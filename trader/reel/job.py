"""The daily Reels: facts -> script (checked) -> voice -> video -> Telegram for the owner to review and post.

  python -m trader.run reel                   # morning (cron 07:40): MYTH vs SACH
  python -m trader.run reel --slot market     # weekdays after the close (cron 19:15, again 21:00 if NSE was late):
                                              # the daily market brief on Telegram + the MARKET AAJ Reel
  python -m trader.run reel --slot evening    # evening (cron 21:30, if reel.evening is on): NEWS SAMJHO on the day's
                                              # top trusted news, else MARKET KI KAHANI
Each slot is sent once a day (--force makes another; --topic myth:N / story:N picks the topic, e.g. for the
first Reels: python -m trader.run reel --force --topic myth:13).
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import company, content, market, numbers, render, script as S, voice

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "cache" / "reel"
log = logging.getLogger("trader.reel")


def market_facts(day: dict) -> dict:
    """The compiled market day as the script's facts: a fact sheet for Claude and plain lines for the template."""
    b = day["broad"]
    lines = [f"{n} closed {v['close']:,.0f}, {v['pct']:+.2f}% today" for n, v in b.items()]
    adv, dec = day["breadth"]
    if adv is not None:
        lines.append(f"Nifty 500 mein {int(adv)} shares upar, {int(dec)} neeche")
    if day["sectors"]:
        (wn, wp), (bn, bp) = day["sectors"][0], day["sectors"][-1]
        lines.append(f"Sabse aage {bn} sector {bp:+.2f}%, sabse peeche {wn} {wp:+.2f}%")
    sheet = ["SCOREBOARD: " + "; ".join(lines)]
    vix, vixp = day["vix"]
    if vix is not None:
        sheet.append(f"INDIA VIX (fear gauge: expected swings of the Nifty): {vix:.2f}, {vixp:+.2f}% today")
    if day["fii"] is not None:
        sheet.append(f"FLOWS (provisional, ₹ crore): foreign investors (FII) net {day['fii']:+,.0f}; Indian funds "
                     f"(DII) net {day['dii']:+,.0f}" if day["dii"] is not None else f"FII net {day['fii']:+,.0f}")
    sheet.append("SECTORS today: " + ", ".join(f"{n} {p:+.2f}%" for n, p in day["sectors"][::-1]))
    for it in day["news"]:
        sheet.append(f"NEWS ({'official source' if it['official'] else 'two outlets'}): {it['headline']}. {it['facts']} "
                     f"Why it matters: {it['why_it_matters']}"
                     + (f" The {it['sector']} index moved {it['sector_today']:+.2f}% today." if it.get("sector_today")
                        is not None else ""))
    f = day.get("filing")
    if f:
        sheet.append(f"NSE FILING: {f['symbol']} - {f['subject']}." + (f" History for this type: {f['history']}"
                                                                        if f.get("history") else ""))
    for r in day.get("results", []):
        sheet.append(f"RESULTS TODAY - {r['company']} ({r['quarter']}): " + "; ".join(p["text"] for p in r["points"]))
    sheet += [f"WHY IT MOVED (as reported by trusted outlets): {d['text']}" for d in day.get("drivers", [])]
    sheet += [f"GLOBAL CUE: {c['what']}: {c['value']}" for c in day.get("cues", [])]
    sheet += [f"CHART (index level; describes, never predicts): {c}" for c in day.get("chart", [])]
    if day.get("options"):
        sheet.append(f"OPTIONS DATA: {day['options']}")
    if day.get("chart"):
        sheet.append(f"CHART HONESTY (say it): {market.CHART_HONESTY}")
    if day.get("poll_reveal"):
        sheet.append(f"YESTERDAY'S VIEWER POLL (reveal it early): {day['poll_reveal']}")
    sheet += [f"HISTORY: {h}" for h in day["history"]]
    if day["calendar"]:
        sheet.append("NEXT SESSION: " + "; ".join(day["calendar"]))
    companies = sorted({c for it in day["news"] for c in it.get("companies", [])} |
                       {r["company"] for r in day.get("results", [])})
    return {"format": "market", "market_text": "\n".join(sheet), "market_lines": lines, "companies": companies,
            "next": "Kal phir market ka hisaab, isi time"}


def facts_for(today: date, slot: str, episodes: dict, store: Optional[Path] = None, topic: str = "",
              day: Optional[dict] = None, out_dir: Path = OUT) -> dict:
    """What today's Reel in this slot is about (one topic) and its series' episode number. topic 'myth:N' or
    'story:N' picks one by its number in content.MYTHS / content.STORIES instead (e.g. for the first Reels)."""
    f = {"date": today.isoformat()}
    if topic:
        kind, _, n = topic.partition(":")
        items = {"myth": content.MYTHS, "story": content.STORIES}.get(kind)
        if items is None or not n.isdigit() or int(n) >= len(items):
            raise ValueError(f"topic must be myth:0-{len(content.MYTHS) - 1} or story:0-{len(content.STORIES) - 1}")
        if kind == "myth":
            myth, truth, lesson = items[int(n)]
            f.update(format="myth", myth=myth, truth=truth, lesson=(lesson, content.LESSON[lesson]),
                     next=content.pick(content.MYTHS, today + timedelta(days=1))[0])
        else:
            f.update(format="story", story=items[int(n)], next="Market ki ek aur sachchi kahani")
        f["episode"] = episodes.get(f["format"], 0) + 1
        return f
    if slot == "company":
        f.update(format="company", company_text=company.fact_sheet(day), companies=[day["name"], day["symbol"]],
                 company_lines=[day["business"]] + [x["text"] for k in ("how_it_earns", "numbers", "history")
                                                    for x in day[k]],
                 next=company.pick(episodes.get("company", 0) + 1)[1])
        f["episode"] = episodes.get("company", 0) + 1
        return f
    if slot == "market":
        f.update(market_facts(day))
        f["episode"] = episodes.get("market", 0) + 1
        return f
    saved = market.load_saved(today, out_dir) if slot == "evening" else None
    results = (saved or {}).get("results") or []
    macro = (saved or {}).get("news") or []
    news = content.news_item(today, store) if slot == "evening" and not macro else None
    if slot == "morning":
        myth, truth, lesson = content.pick(content.MYTHS, today)
        f.update(format="myth", myth=myth, truth=truth, lesson=(lesson, content.LESSON[lesson]),
                 next=content.pick(content.MYTHS, today + timedelta(days=1))[0])
    elif results:                                                           # results day: the biggest company's results
        f.update(format="news", results=results[0], companies=[results[0]["company"]])
    elif macro:                                                             # the day's top trusted news, explained
        it = macro[0]
        f.update(format="news", macro=it, history=[h for h in saved.get("history", []) if it["sector"] in h])
    elif news:
        f.update(format="news", news=news)
    else:
        n = episodes.get("story", 0)
        f.update(format="story", story=content.STORIES[n % len(content.STORIES)],
                 next="Market ki ek aur sachchi kahani")
    f["episode"] = episodes.get(f["format"], 0) + 1
    return f


def make(today: date, out_dir: Path = OUT, client=None, store: Optional[Path] = None, handle: str = "",
         voice_model: Optional[str] = None, voice_id: Optional[str] = None, speak: str = "roman",
         slot: str = "morning", episodes: Optional[dict] = None, topic: str = "", day: Optional[dict] = None,
         facts_override: Optional[dict] = None) -> dict:
    facts = facts_override or facts_for(today, slot, episodes or {}, store, topic, day, out_dir)
    sc, source = S.write(facts, client)
    if facts.get("breaking"):
        slot = f"breaking{facts['episode']}"
    work = out_dir / f"work_{today:%Y%m%d}_{slot}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    deva = speak == "devanagari"                  # the voice reads Hindi words in Devanagari; captions stay Roman
    items = [(s.kind, s.on_screen, s.narration, (s.spoken if deva else "") or s.narration) for s in sc.scenes]
    items.append(("disclaimer", S.DISCLAIMER_SCREEN, S.DISCLAIMER, S.DISCLAIMER_SPOKEN if deva else S.DISCLAIMER))
    said = [numbers.speakable(x[3], hindi_words=deva) for x in items]   # numbers as words: the voice never guesses
    from concurrent.futures import ThreadPoolExecutor

    def one(i):                                                            # every beat is voiced at the same time
        return voice.speak(said[i], work / f"a{i:02d}.mp3", voice=voice_id, model=voice_model,
                           prev=said[i - 1] if i else None, nxt=said[i + 1] if i + 1 < len(said) else None)
    with ThreadPoolExecutor(4) as ex:
        spoken = list(ex.map(one, range(len(items))))
    scenes = [(kind, on_screen, narration, spoken[i][0]) for i, (kind, on_screen, narration, _) in enumerate(items)]
    voices = {v for _, v in spoken}
    top = f"{facts.get('series') or S.SERIES[facts['format']]}  •  EP {facts['episode']}"
    video = render.build(scenes, out_dir / f"reel_{today:%Y%m%d}_{slot}.mp4", work, handle, top,
                         fast=bool(facts.get("breaking")))
    tags = " ".join("#" + h.lstrip("#").replace(" ", "") for h in sc.hashtags[:8])
    caption = f"{top}\n\n{sc.caption}\n\n{S.CAPTION_DISCLAIMER}\n\n{tags}"
    if facts.get("news"):
        caption += f"\n\nSource: NSE announcement, {facts['news']['symbol']} ({facts['news']['date']})"
    if facts.get("format") == "company":
        caption += "\n\nSources: " + ", ".join(sorted({market._domain(u) for u in company.sources(day)})[:6])
    if facts.get("results"):
        caption += "\n\nSources: " + ", ".join(sorted({market._domain(u) for x in facts["results"]["points"]
                                                     for u in x["source_urls"]}))
    if facts.get("macro"):
        caption += "\n\nSources: " + ", ".join(sorted({market._domain(u) for u in facts["macro"]["source_urls"]}))
    return {"video": video, "caption": caption, "script": sc, "script_source": source, "format": facts["format"],
            "voice": ", ".join(sorted(voices)), "news": facts.get("news"), "question": sc.scenes[-1].narration}


def run(notify, send_video, today: Optional[date] = None, out_dir: Path = OUT, client=None, store=None,
        handle: str = "", voice_model: Optional[str] = None, voice_id: Optional[str] = None, speak: str = "roman",
        force: bool = False, slot: str = "morning", topic: str = "", holidays=(), search: bool = True) -> str:
    today = today or date.today()
    state_p = out_dir / "state.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {}
    sent = st.get("sent_" + slot, st.get("sent") if slot == "morning" else None)
    if sent == today.isoformat() and not force:
        return f"reel ({slot}): already sent today"
    episodes = st.get("episodes", {})
    day = None
    if slot == "company":
        sym, name = company.pick(episodes.get("company", 0))
        if topic.startswith("company:"):
            sym = topic.split(":", 1)[1].upper()
            name = dict(company.COMPANIES).get(sym, sym)
        day, note = company.research(sym, name, client)
        if day is None:
            notify(f"Company case study skipped this week ({name}): {note}.")
            return f"reel (company): skipped - {note}"
        topic = ""
    if slot == "market" and not topic:
        day = market.compile_day(today, store or content.ROOT / "cache" / "insights", holidays, client, search=search,
                                 hist_path=out_dir / "index_hist.csv", polls=st.get("polls", []))
        if day is None:
            return "reel (market): no market session today, or NSE's closing data is not out yet"
        market.save(day, out_dir)
        for part in market.parts(market.brief(day)):
            notify(part)
    r = make(today, out_dir, client, store, handle, voice_model, voice_id, speak, slot, episodes, topic, day)
    ok = send_video(r["video"], f"🎬 {slot.title()} Reel ({today:%a %d %b}) - review before posting")
    notes = [f"📝 Instagram caption (copy-paste):\n\n{r['caption']}",
             "Before you post: watch it once; check the news source on nseindia.com if a company is named.\n"
             f"After posting, pin a comment with the question so people answer: \"{r['question']}\"\n"
             f"Script: {r['script_source']}. Voice: {r['voice']}."]
    if not ok:
        notes.insert(0, f"The video could not be sent on Telegram; it is on the server at {r['video']}")
    for n in notes:
        notify(n)
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes[r["format"]] = episodes.get(r["format"], 0) + 1
    st.update({"sent_" + slot: today.isoformat(), "episodes": episodes, "video_" + slot: str(r["video"])})
    if slot == "market" and day is not None:
        st["polls"] = day["polls"][-40:]
    st.pop("sent", None)
    state_p.write_text(json.dumps(st))
    for old in sorted(out_dir.glob("work_*"))[:-6]:                         # keep the last few days' working files
        shutil.rmtree(old, ignore_errors=True)
    return f"reel ({slot}): sent {r['format']} ({r['script_source']}; voice {r['voice']})"
