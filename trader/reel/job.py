"""The daily Reel job: facts -> script (checked) -> voice -> video -> Telegram for the owner to review and post.

  python -m trader.run reel            # once a day (cron), skips if today's Reel was already sent
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Optional

from . import content, render, script as S, voice

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "cache" / "reel"
log = logging.getLogger("trader.reel")


def make(today: date, out_dir: Path = OUT, client=None, store: Optional[Path] = None, handle: str = "",
         voice_model: Optional[str] = None, voice_id: Optional[str] = None, speak: str = "roman") -> dict:
    facts = {"date": today.isoformat(), "lesson": content.pick(content.LESSONS, today),
             "research": content.pick(content.RESEARCH, today), "news": content.news_item(today, store)}
    sc, source = S.write(facts, client)
    work = out_dir / f"work_{today:%Y%m%d}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    scenes, voices = [], set()
    deva = speak == "devanagari"                  # the voice reads Hindi words in Devanagari; captions stay Roman
    items = [(s.kind, s.on_screen, s.narration, (s.spoken if deva else "") or s.narration) for s in sc.scenes]
    items.append(("disclaimer", "Education only", S.DISCLAIMER, S.DISCLAIMER_SPOKEN if deva else S.DISCLAIMER))
    said = [x[3] for x in items]
    for i, (kind, on_screen, narration, text) in enumerate(items):
        audio, vsrc = voice.speak(text, work / f"a{i:02d}.mp3", voice=voice_id, model=voice_model,
                                  prev=said[i - 1] if i else None, nxt=said[i + 1] if i + 1 < len(said) else None)
        voices.add(vsrc)
        scenes.append((kind, on_screen, narration, audio))
    video = render.build(scenes, out_dir / f"reel_{today:%Y%m%d}.mp4", work, handle)
    tags = " ".join("#" + h.lstrip("#").replace(" ", "") for h in sc.hashtags[:12])
    caption = f"{sc.caption}\n\n{S.CAPTION_DISCLAIMER}\n\n{tags}"
    if facts["news"]:
        caption += f"\n\nSource: NSE announcement, {facts['news']['symbol']} ({facts['news']['date']})"
    return {"video": video, "caption": caption, "script": sc, "script_source": source,
            "voice": ", ".join(sorted(voices)), "news": facts["news"]}


def run(notify, send_video, today: Optional[date] = None, out_dir: Path = OUT, client=None, store=None,
        handle: str = "", voice_model: Optional[str] = None, voice_id: Optional[str] = None, speak: str = "roman") -> str:
    today = today or date.today()
    state_p = out_dir / "state.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {}
    if st.get("sent") == today.isoformat():
        return "reel: already sent today"
    r = make(today, out_dir, client, store, handle, voice_model, voice_id, speak)
    ok = send_video(r["video"], f"🎬 Today's Reel ({today:%a %d %b}) - review before posting")
    notes = [f"📝 Instagram caption (copy-paste):\n\n{r['caption']}",
             "Before you post: watch it once; check the news source link on nseindia.com if a company is named.\n"
             f"Script: {r['script_source']}. Voice: {r['voice']}."]
    if not ok:
        notes.insert(0, f"The video could not be sent on Telegram; it is on the server at {r['video']}")
    for n in notes:
        notify(n)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_p.write_text(json.dumps({"sent": today.isoformat(), "video": str(r["video"])}))
    for old in sorted(out_dir.glob("work_*"))[:-3]:                         # keep the last few days' working files
        shutil.rmtree(old, ignore_errors=True)
    return f"reel: sent ({r['script_source']}; voice {r['voice']})"
