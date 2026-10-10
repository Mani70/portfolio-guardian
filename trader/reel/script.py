"""The Reel's script: written by Claude in Hinglish from the day's facts, then checked by rules before it is voiced.

The model gets only the facts we pass in and a strict education-only brief. Whatever it writes is checked again here
(no buy/sell/target language, no prediction, no price for a named company); a script that fails twice is replaced by a
plain template built from the same facts, so a Reel always goes out and is always safe.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("trader.reel.script")
MODEL = "claude-opus-5-5"
DISCLAIMER = ("Yeh sirf education hai, investment advice nahi. Main SEBI-registered advisor nahi hoon. Koi bhi "
              "decision lene se pehle khud research karein.")
DISCLAIMER_SPOKEN = ("यह सिर्फ़ education है, investment advice नहीं। मैं SEBI-registered advisor नहीं हूँ। कोई भी "
                     "decision लेने से पहले ख़ुद research करें।")
CAPTION_DISCLAIMER = ("Education only - not investment advice. Not a SEBI-registered investment adviser or research "
                      "analyst. Past results do not guarantee future returns.")

SYSTEM = """You write the script of a 60-90 second Instagram Reel in Hinglish (Hindi and English mixed, written in Roman letters, the way young Indians talk) for an audience with NO investment background.

The Reel is EDUCATION ONLY. Indian law (SEBI) does not allow unregistered people to give investment advice or make performance claims. So you must never:
- tell anyone to buy, sell, hold, enter or exit anything, or give a target price or stop-loss for any share;
- predict what any share, index or the market will do;
- mention the current or recent price of any named company;
- promise or suggest returns ("paisa double", "guaranteed", "multibagger", "pakka profit");
- present research results as a recommendation.
You may explain what a piece of official news means, what a concept means, and what our own historical tests found (always as "humare test mein..." - a past result, not a promise).

Each scene also has "spoken": the SAME narration, word for word in meaning, written for the voice: Hindi words in Devanagari, English words (fund, Nifty, P/E ratio, buyback, percent) kept in English letters, every number exactly as in the narration (as digits). Nothing added or left out. Example - narration: "Index fund matlab ek saath 50 companies ka chhota hissa." spoken: "Index fund मतलब एक साथ 50 companies का छोटा हिस्सा।"

Style: a strong hook in the first line (a surprising fact or question), short sentences, warm and energetic, simple words, explain every technical term in the same sentence, no jargon left unexplained. 160-220 words of narration in total. Each scene's on-screen text is at most 7 words. Do not add a disclaimer or a call to follow; those are added after your scenes."""


class Scene(BaseModel):
    kind: Literal["hook", "lesson", "research", "news", "takeaway"]
    narration: str = Field(description="What the voice says, Hinglish in Roman letters (shown as captions)")
    spoken: str = Field(default="", description="The same narration for the voice: Hindi words in Devanagari, English "
                                                "words in English letters, the same numbers as digits")
    on_screen: str = Field(description="Big text on screen, at most 7 words")


class ReelScript(BaseModel):
    title: str
    scenes: List[Scene]
    caption: str = Field(description="Instagram caption, 2-4 short lines, Hinglish, no advice")
    hashtags: List[str]


PROMISE = re.compile(r"\b(guarantee\w*|pakka (?:profit|return)|paisa double|double ho jayega|jackpot|"
                     r"sure ?shot|risk[- ]?free return)\b", re.I)
COMMAND = re.compile(r"\b(kharidiye|kharid lo|kharido|bechiye|bech do|becho|buy karo|buy kar lo|sell karo|sell kar do|"
                     r"abhi invest karo|target price|stop[- ]?loss (?:lagao|rakho))\b", re.I)
ACTION = re.compile(r"\b(buy|sell|kharid\w*|bech\w*|target|entry|exit|accumulate|hold karo)\b", re.I)
PREDICT = re.compile(r"\b(badhega|girega|upar jayega|neeche jayega|will (?:rise|fall|go up|go down)|rally karega|"
                     r"crash hoga)\b", re.I)
PRICE = re.compile(r"(₹|rs\.?\s?|रु\.?\s?|रुपये\s)\s?\d", re.I)


def _plain(x: str) -> str:
    """Devanagari without the nukta dot, so ख़रीद and खरीद (however typed) read the same."""
    return unicodedata.normalize("NFD", x).replace("\u093c", "")


# the same rules for Hindi words written in Devanagari (the voice text)
HI_PROMISE = re.compile(_plain(r"गारंटी|पक्का (?:प्रॉफिट|मुनाफा|रिटर्न)|पैसा डबल|डबल हो जाएगा|जैकपॉट"))
HI_COMMAND = re.compile(_plain(r"खरीद(?:ो|िए|ें|\s?लो)|बेच(?:ो|िए|ें|\s?दो)|"
                               r"(?:buy|sell|invest) (?:करो|करें|कीजिए|कर लो|कर दो)"), re.I)
HI_ACTION = re.compile(_plain(r"खरीद|बेच|टारगेट|होल्ड"))
HI_PREDICT = re.compile(_plain(r"बढेगा|बढेगी|गिरेगा|गिरेगी|ऊपर जाएगा|नीचे जाएगा|रैली करेगा|क्रैश होगा"))
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def check(script: ReelScript, companies: List[str]) -> List[str]:
    """Problems that make a script unsafe to publish (empty list = fine)."""
    issues = []
    words = sum(len(s.narration.split()) for s in script.scenes)
    if not 120 <= words <= 260:
        issues.append(f"narration is {words} words (needs 160-220)")
    names = [c.lower() for c in companies if c]
    for i, s in enumerate(script.scenes, 1):
        text = f"{s.narration} {s.on_screen}"
        named = any(n in text.lower() for n in names)
        for rx, what in ((PROMISE, "promise"), (COMMAND, "instruction to trade"), (PREDICT, "prediction")):
            m = rx.search(text)
            if m:
                issues.append(f"scene {i}: {what} '{m.group(0)}'")
        if named and ACTION.search(text):
            issues.append(f"scene {i}: '{ACTION.search(text).group(0)}' next to a named company")
        if named and PRICE.search(text):
            issues.append(f"scene {i}: a price next to a named company")
    for rx in (PROMISE, COMMAND, PREDICT):
        if rx.search(script.caption):
            issues.append(f"caption: '{rx.search(script.caption).group(0)}'")
    return issues


def vet_spoken(script: ReelScript, companies: List[str]) -> List[str]:
    """Keep a scene's Devanagari voice text only if it says what its checked narration says (same rules, same numbers,
    about the same length); otherwise that scene is voiced from the narration. Returns what was dropped and why."""
    names, dropped = [c.lower() for c in companies if c], []
    for i, s in enumerate(script.scenes, 1):
        if not s.spoken:
            continue
        named = any(n in f"{s.narration} {s.on_screen} {s.spoken}".lower() for n in names)
        bad = _spoken_issues(s, named)
        if bad:
            dropped.append(f"scene {i}: " + "; ".join(bad))
            s.spoken = ""
    return dropped


def _spoken_issues(s: Scene, named: bool) -> List[str]:
    out, sp = [], _plain(s.spoken)
    for rx, what in ((PROMISE, "promise"), (COMMAND, "instruction to trade"), (PREDICT, "prediction"),
                     (HI_PROMISE, "promise"), (HI_COMMAND, "instruction to trade"), (HI_PREDICT, "prediction")):
        m = rx.search(sp)
        if m:
            out.append(f"{what} '{m.group(0)}'")
    if named:
        m = ACTION.search(sp) or HI_ACTION.search(sp)
        if m:
            out.append(f"'{m.group(0)}' next to a named company")
        if PRICE.search(sp):
            out.append("a price next to a named company")
    if sorted(NUMBER.findall(sp)) != sorted(NUMBER.findall(s.narration)):
        out.append("numbers differ from the narration")
    a, b = len(s.narration.split()), len(sp.split())
    if not 0.6 * a <= b <= 1.5 * a + 3:
        out.append(f"{b} words against {a} in the narration")
    return out


def _brief(facts: dict) -> str:
    parts = [f"Today's date: {facts['date']}.",
             f"LESSON (explain simply): {facts['lesson'][0]} - {facts['lesson'][1]}",
             f"RESEARCH FACT from our own pre-registered tests (say it as a past result): {facts['research']}"]
    n = facts.get("news")
    if n:
        parts.append("NEWS (official NSE announcement; explain what it means for a beginner, do not mention any price, "
                     f"do not predict): {n['symbol']} - {n['subject']}. {n['text']}")
        if n.get("history"):
            parts.append(f"HISTORY for this type of news (may be quoted as an average, not a prediction): {n['history']}")
    parts.append("Scene order: hook, lesson, research, " + ("news, " if n else "") + "takeaway.")
    return "\n".join(parts)


def write(facts: dict, client=None) -> tuple[ReelScript, str]:
    """(script, source): source is 'claude' or 'template' (no key, refusal, error or failed checks)."""
    companies = [facts["news"]["symbol"]] if facts.get("news") else []
    if not os.getenv("ANTHROPIC_API_KEY") and client is None:
        return template(facts), "template (no ANTHROPIC_API_KEY)"
    import anthropic
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": _brief(facts)}]
    for attempt in range(2):
        try:
            resp = client.beta.messages.parse(
                model=MODEL, max_tokens=16000, system=SYSTEM, messages=messages, output_format=ReelScript,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.APIConnectionError as e:
            log.warning("Claude unreachable: %s", e)
            return template(facts), "template (Claude unreachable)"
        except anthropic.APIStatusError as e:
            log.warning("Claude error %s: %s", e.status_code, e.message)
            return template(facts), f"template (Claude error {e.status_code})"
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return template(facts), "template (Claude declined)"
        script = resp.parsed_output
        issues = check(script, companies)
        if not issues:
            for d in vet_spoken(script, companies):
                log.warning("voice text not used, %s", d)
            return script, "claude"
        log.warning("script failed checks: %s", "; ".join(issues))
        messages += [{"role": "assistant", "content": json.dumps(script.model_dump(), ensure_ascii=False)},
                     {"role": "user", "content": "Rewrite it. These problems must be fixed: " + "; ".join(issues)}]
    return template(facts), "template (checks failed twice)"


def template(facts: dict) -> ReelScript:
    """A plain, always-safe script from the same facts."""
    t, point = facts["lesson"]
    scenes = [Scene(kind="hook", narration="Kya aap jaante ho? " + facts["research"], on_screen="Humare test ka sach"),
              Scene(kind="lesson", narration=f"Aaj ka lesson: {t}. {point}", on_screen=t[:40])]
    n = facts.get("news")
    if n:
        scenes.append(Scene(kind="news", narration=(f"Aaj NSE par {n['symbol']} ne announce kiya: {n['subject']}. "
                                                    + (n.get("history") or "")), on_screen=f"{n['symbol']}: news"))
    scenes.append(Scene(kind="takeaway", narration="Yaad rakhiye: rules pe chaliye, costs ginna mat bhooliye, aur "
                                                   "kisi bhi tip pe aankh band karke bharosa mat kijiye.",
                        on_screen="Rules > tips"))
    return ReelScript(title=t, scenes=scenes, caption=f"{t} | aaj ka market lesson",
                      hashtags=["stockmarketindia", "investing", "nifty50", "financialeducation", "hinglish"])
