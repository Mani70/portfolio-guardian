"""The daily Reels: facts -> script (checked) -> voice -> video -> Telegram for the owner to review and post.

  python -m trader.run reel                   # morning (cron 07:40): MYTH vs SACH
  python -m trader.run reel --slot evening    # evening (cron 18:40, if reel.evening is on): NEWS SAMJHO when there
                                              # is notable official news, else MARKET KI KAHANI
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

from . import content, numbers, render, script as S, voice

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "cache" / "reel"
log = logging.getLogger("trader.reel")


def facts_for(today: date, slot: str, episodes: dict, store: Optional[Path] = None, topic: str = "") -> dict:
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
    news = content.news_item(today, store) if slot == "evening" else None
    if slot == "morning":
        myth, truth, lesson = content.pick(content.MYTHS, today)
        f.update(format="myth", myth=myth, truth=truth, lesson=(lesson, content.LESSON[lesson]),
                 next=content.pick(content.MYTHS, today + timedelta(days=1))[0])
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
         slot: str = "morning", episodes: Optional[dict] = None, topic: str = "") -> dict:
    facts = facts_for(today, slot, episodes or {}, store, topic)
    sc, source = S.write(facts, client)
    work = out_dir / f"work_{today:%Y%m%d}_{slot}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    deva = speak == "devanagari"                  # the voice reads Hindi words in Devanagari; captions stay Roman
    items = [(s.kind, s.on_screen, s.narration, (s.spoken if deva else "") or s.narration) for s in sc.scenes]
    items.append(("disclaimer", S.DISCLAIMER_SCREEN, S.DISCLAIMER, S.DISCLAIMER_SPOKEN if deva else S.DISCLAIMER))
    said = [numbers.speakable(x[3], hindi_words=deva) for x in items]   # numbers as words: the voice never guesses
    scenes, voices = [], set()
    for i, (kind, on_screen, narration, _) in enumerate(items):
        audio, vsrc = voice.speak(said[i], work / f"a{i:02d}.mp3", voice=voice_id, model=voice_model,
                                  prev=said[i - 1] if i else None, nxt=said[i + 1] if i + 1 < len(said) else None)
        voices.add(vsrc)
        scenes.append((kind, on_screen, narration, audio))
    top = f"{S.SERIES[facts['format']]}  •  EP {facts['episode']}"
    video = render.build(scenes, out_dir / f"reel_{today:%Y%m%d}_{slot}.mp4", work, handle, top)
    tags = " ".join("#" + h.lstrip("#").replace(" ", "") for h in sc.hashtags[:8])
    caption = f"{top}\n\n{sc.caption}\n\n{S.CAPTION_DISCLAIMER}\n\n{tags}"
    if facts.get("news"):
        caption += f"\n\nSource: NSE announcement, {facts['news']['symbol']} ({facts['news']['date']})"
    return {"video": video, "caption": caption, "script": sc, "script_source": source, "format": facts["format"],
            "voice": ", ".join(sorted(voices)), "news": facts.get("news"), "question": sc.scenes[-1].narration}


def run(notify, send_video, today: Optional[date] = None, out_dir: Path = OUT, client=None, store=None,
        handle: str = "", voice_model: Optional[str] = None, voice_id: Optional[str] = None, speak: str = "roman",
        force: bool = False, slot: str = "morning", topic: str = "") -> str:
    today = today or date.today()
    state_p = out_dir / "state.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {}
    sent = st.get("sent_" + slot, st.get("sent") if slot == "morning" else None)
    if sent == today.isoformat() and not force:
        return f"reel ({slot}): already sent today"
    episodes = st.get("episodes", {})
    r = make(today, out_dir, client, store, handle, voice_model, voice_id, speak, slot, episodes, topic)
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
    st.pop("sent", None)
    state_p.write_text(json.dumps(st))
    for old in sorted(out_dir.glob("work_*"))[:-6]:                         # keep the last few days' working files
        shutil.rmtree(old, ignore_errors=True)
    return f"reel ({slot}): sent {r['format']} ({r['script_source']}; voice {r['voice']})"
