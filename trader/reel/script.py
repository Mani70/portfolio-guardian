"""The Reel's script: one topic as 8-12 short beats, written by Claude in Hinglish from the day's facts, then checked
by rules before it is voiced.

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
DISCLAIMER = "Yeh sirf education hai, investment advice nahi."          # said; the full text is on screen and in the caption
DISCLAIMER_SPOKEN = "यह सिर्फ़ education है, investment advice नहीं।"
DISCLAIMER_SCREEN = "Education only. Not investment advice. Not a SEBI-registered adviser."
CAPTION_DISCLAIMER = ("Education only - not investment advice. Not a SEBI-registered investment adviser or research "
                      "analyst. Past results do not guarantee future returns.")
SERIES = {"myth": "MYTH vs SACH", "news": "NEWS SAMJHO", "story": "MARKET KI KAHANI", "market": "MARKET AAJ",
          "company": "COMPANY KI KUNDLI", "night": "RAAT KI REPORT", "pathshala": "PAISA KI PATHSHALA"}

SYSTEM = """You write one 45-60 second Instagram Reel in Hinglish - Hindi-first, with the English words young Indians use (share, fund, profit, Nifty), written in Roman letters - for people with NO investment background, mostly from Hindi-speaking India.

The Reel is EDUCATION ONLY. Indian law (SEBI) does not allow unregistered people to give investment advice or make performance claims. So you must never:
- tell anyone to buy, sell, hold, enter or exit anything, or give a target price or stop-loss for any share;
- predict what any share, index or the market will do;
- mention the current or recent price of any named company;
- promise or suggest returns ("paisa double", "guaranteed", "multibagger", "pakka profit");
- present research results as a recommendation;
- use any number, date or name that is not in the facts given to you.

Make it ENTERTAINING - this is what keeps people watching and makes them come back:
- Beat 1 is the hook, at most 10 words, and it must put something at stake for the VIEWER: their money at risk ("Aapka paisa yahan chupchaap kat raha hai"), a half-known belief they probably hold ("Sab sochte hain FD sabse safe hai..."), or one shocking number from the facts. Specific beats vague. No greeting, no "aaj hum baat karenge", no slow intro. It must not promise anything the Reel does not deliver.
- Our viewers fear LOSING money more than they want to get rich: "kaise nuksaan se bachein", "yeh galti mat karna", "scam kaise pehchane" pull harder than "ameer kaise banein". Never fear-monger: every worry you raise, the Reel answers.
- Tell it like a story with tension: set up the belief or the situation, build curiosity, then the reveal. Around the middle (beat 4-6) put a re-hook - a "lekin twist yeh hai..." / "ab asli baat suniye..." turn - so people who were about to scroll stay. Short punchy sentences, rhetorical questions, desi analogies (chai, cricket, shaadi, EMI, Bollywood-style drama - but no film dialogues or song lyrics), a little humour. Never insult anyone, never fear-monger.
- Explain every technical term in the same sentence, in plain words.
- 8 to 12 beats. Each beat is ONE or TWO short sentences (6-18 words) - the picture changes every beat. 100-130 words in total (the voice speaks about 2.2 words a second, so this is 45-60 seconds).
- Each beat's on_screen text is at most 6 words: the punchline, keyword or number of that beat (not a copy of the narration). Put *stars* around the ONE word that matters most (it is shown in colour). A beat whose on_screen is only a number ("93%", "₹546 crore") shows that number counting up - use it for the most striking number.
- Each beat has an "icon": ONE emoji from this list that pictures the beat (vary them; no icon twice in a row): {icons}
- When a beat states a number or a finding, set its "source" to where it comes from, in at most 5 words with the year if known (e.g. "SEBI study, 2024", "NSE data, 10 Oct 2026", "RBI policy, Oct 2026"); it is shown small on screen. Only sources named in the facts.
- The last beat (kind "question") does three things in at most 3 short sentences: ONE easy question to answer in the comments (about the viewer's own experience, e.g. "Aapne kabhi ... kiya hai? Comment mein batao"); ONE send line naming who to send it to, fitted to the topic ("Papa ko bhejo jo sirf FD pe bharosa karte hain", "us dost ko bhejo jo roz F&O khelta hai"); and tomorrow's topic in a few words if one is given ("Kal: ...").
- Never name a politician or a political party, and never take a side on a policy; say what a rule or a policy changes for the viewer's money.
- Do not add a disclaimer or a "follow karo" line; those are added after your beats.

Each beat also has "spoken": the SAME words for the voice - Hindi words in Devanagari, English words (fund, Nifty, P/E ratio, buyback, percent) kept in English letters, every number exactly as in the narration, as digits. Nothing added or left out. Example - narration: "Index fund matlab ek saath 50 companies ka chhota hissa." spoken: "Index fund मतलब एक साथ 50 companies का छोटा हिस्सा।"
"""

from .render import emoji_list  # noqa: E402 - the icons we have pictures for
SYSTEM = SYSTEM.replace("{icons}", " ".join(emoji_list()))


class Scene(BaseModel):
    kind: Literal["hook", "myth", "truth", "proof", "explain", "news", "history", "story", "twist", "takeaway",
                  "question", "market", "sector", "flows", "watch"]
    narration: str = Field(description="What the voice says: 1-2 short sentences, Hinglish in Roman letters (shown as captions)")
    spoken: str = Field(default="", description="The same narration for the voice: Hindi words in Devanagari, English "
                                                "words in English letters, the same numbers as digits")
    on_screen: str = Field(description="Big text on screen for this beat, at most 6 words")
    icon: str = Field(default="", description="One emoji from the list in the instructions that pictures this beat")
    source: str = Field(default="", description="Where this beat's number comes from, at most 5 words, or empty")
    character: str = Field(default="", description="A cast member shown on this beat (only if a cast is given), or "
                                                   "empty")
    expression: str = Field(default="", description="The character's expression: smile, curious, worried, shocked, "
                                                    "relieved or proud")


class ReelScript(BaseModel):
    title: str
    scenes: List[Scene] = Field(description="The beats, in order")
    caption: str = Field(description="Instagram caption, 3-5 short lines, Hinglish, no advice: line 1 is what people "
                                     "would type in search, in Hinglish (e.g. 'Intraday trading kya hai? Share market "
                                     "Hindi mein'), line 2 the most surprising fact, then the comment question, then a "
                                     "share line naming who would find it useful ('📤 Bhejo us dost ko jo ...'), then "
                                     "'🔖 Save karo'")
    hashtags: List[str] = Field(description="At most 5 specific hashtags, Hinglish or English")


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
PCT = re.compile(r"\d\s?%|\d\s?percent|\d\s?pratishat", re.I)
# a company's business numbers (results) may be said; its share price or share move may not (SEBI 30-day rule)
METRIC = re.compile(r"\b(revenue|sales|profit|munafa|munafe|kamai|income|aay|margin|ebitda|order|orders|dividend|loss|"
                    r"ghata|ghate|deposits?|loans?|customers?|subscribers?|volumes?|growth|crore|lakh)\b", re.I)
SHARE = re.compile(r"\b(share|shares|stock|stocks|scrip|bhav|price|prices|daam|target|market ?cap|valuation)\b|"
                   r"\b(upar|neeche) band\b|\bclosed (?:up|down|at)\b", re.I)


def share_talk(text: str, strict: bool = True) -> str:
    """'' if every sentence with a number is about the business; otherwise what is wrong. A price next to a company
    is always wrong; a % only in the strict formats (market wrap, news and results explainers)."""
    for sent in re.split(r"(?<=[.!?।])\s+", text):
        price, pct = PRICE.search(sent), strict and PCT.search(sent)
        if not (price or pct):
            continue
        if SHARE.search(sent) or not METRIC.search(sent):
            return "a price" if price else "a move (%)"
    return ""


def check(script: ReelScript, companies: List[str], strict: bool = False, company_mode: bool = False) -> List[str]:
    """Problems that make a script unsafe to publish (empty list = fine)."""
    issues = []
    words = sum(len(s.narration.split()) for s in script.scenes)
    if not 85 <= words <= 150:
        issues.append(f"narration is {words} words (needs 100-130)")
    if not 6 <= len(script.scenes) <= 14:
        issues.append(f"{len(script.scenes)} beats (needs 8-12)")
    if script.scenes and script.scenes[-1].kind != "question":
        issues.append("the last beat must be the comment question")
    names = [c.lower() for c in companies if c]
    for i, s in enumerate(script.scenes, 1):
        text = f"{s.narration} {s.on_screen}"
        named = any(n in text.lower() for n in names)
        for rx, what in ((PROMISE, "promise"), (COMMAND, "instruction to trade"), (PREDICT, "prediction")):
            m = rx.search(text)
            if m:
                issues.append(f"scene {i}: {what} '{m.group(0)}'")
        if company_mode:
            from .company import BANNED
            m = BANNED.search(text)
            if m:
                issues.append(f"scene {i}: '{m.group(0)}' - the case study is about the business, never the share")
            continue
        if named and ACTION.search(text):
            issues.append(f"scene {i}: '{ACTION.search(text).group(0)}' next to a named company")
        if named:
            bad = share_talk(text, strict)
            if bad:
                issues.append(f"scene {i}: {bad} next to a named company")
    for rx in (PROMISE, COMMAND, PREDICT):
        if rx.search(script.caption):
            issues.append(f"caption: '{rx.search(script.caption).group(0)}'")
    return issues


def _words(script: ReelScript) -> int:
    return sum(len(s.narration.split()) for s in script.scenes)


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


MARKET_RULES = """This is the weekday MARKET AAJ wrap (55-70 seconds, 10-13 beats, 120-150 words): what the market did today, the day's trusted news explained, what history says, and what is due on the next session.
- Index and sector moves (Nifty, Bank, IT ...) may be said with their numbers.
- A company may be named only with its news or its RESULTS TODAY numbers (revenue, profit and their change are business numbers and may be said) - NEVER with its share price or its share move (SEBI rule). Keep company names out of any beat about index or sector moves.
- "What next" may only be the HISTORY lines given (always "an average, not a prediction") or "history shows no reliable pattern". Never say what will happen tomorrow.
- Explain FII/DII, VIX and any term the first time it appears, in plain words.
- "Why it moved" may only use the WHY IT MOVED lines (say "reports ke mutabik"); global cues only from GLOBAL CUE lines.
- If YESTERDAY'S VIEWER POLL is given, reveal it in beat 2 or 3 ("kal ke poll ka jawab...").
- CHART lines are index-level chart reading: describe them simply (what a 200-day average or RSI is) and say the CHART HONESTY line in your own words. Never turn a level into a forecast ("yahan se upar jayega" is forbidden).
- OPTIONS DATA: explain put-call ratio in one plain sentence; it is a mood number, not a signal.
- The LAST beat (kind "question") asks a learning question about today (e.g. "FII ka matlab ab samjhe? Comment mein apne shabdon mein batao"), a send line, and "Kal ka anumaan Story poll mein do". Never ask viewers to predict in the comments; we never predict either.
- Hook: the single most striking fact of the day (a big move, a big news) - no greeting."""


COMPANY_RULES = """This is COMPANY KI KUNDLI: a 55-65 second story of one well-known Indian company (10-12 beats, 120-145 words) - what it does, how it earns money, how big it is, its turning points, the risks it reports - so that a beginner understands the business.
- Never mention its share price, market value, valuation (P/E, cheap/expensive), a target, or any view on buying or selling the share. This is about the business, not the stock.
- Use only the facts given, with their years. Revenue, profit and growth numbers may be said.
- Hook: a surprising fact about the business (e.g. how many people use it). Explain every term (revenue, profit, debt) the first time.
- The last beat (kind "question") asks viewers which of its products or services they use - comment mein batao - and teases next week's company if given."""


NIGHT_RULES = """This is RAAT KI REPORT, the night Reel: ALL of today's important news together, explained with more clarity than the quick breaking Reels (75-100 seconds, 12-16 beats, 160-210 words).
- Hook: the day's single biggest story in at most 10 words.
- Then each story in 2-3 beats: what happened (with its numbers), what it means for a beginner, and history only if given. Biggest story first.
- One beat for the market close if given; one beat "kal kya dekhna hai" from NEXT SESSION if given.
- Explain every term (revenue, profit, repo rate, FII...) the first time.
- A company may be named with its news and its business numbers - NEVER with its share price or share move; no forecast; no buy/sell.
- The last beat (kind "question") asks which story mattered most to the viewer - comment mein batao."""


def _brief(facts: dict) -> str:
    f = facts["format"]
    parts = [f"Today's date: {facts['date']}. Series: {SERIES[f]}, episode {facts.get('episode', 1)}."]
    if f == "myth":
        parts += [f"MYTH (a popular belief - set it up, then bust it): {facts['myth']}",
                  f"WHAT OUR OWN PRE-REGISTERED TESTS FOUND (the reveal; say it as a past result): {facts['truth']}",
                  f"CONCEPT TO EXPLAIN SIMPLY: {facts['lesson'][0]} - {facts['lesson'][1]}",
                  "Beat kinds to use: hook, myth, twist, truth, proof, explain, takeaway, question."]
    elif f == "news" and facts.get("results"):
        r = facts["results"]
        parts += [f"RESULTS SAMJHO: {r['company']} announced its results for {r['quarter']} today "
                  f"({'company filing' if r['official'] else 'two established outlets'}). Explain them like a story for a "
                  "beginner: what revenue and profit mean, what changed from a year ago and why (as the company said). "
                  "Business numbers only - NEVER the share price, a share move, a target or whether to buy/sell; no "
                  "forecast of your own (what management said may be quoted as theirs).",
                  "RESULT FACTS (use only these):"] + [f"- {p['text']}" for p in r["points"]]
        parts.append("Beat kinds to use: hook, news, explain, history, takeaway, question.")
    elif f == "news" and facts.get("macro"):
        m = facts["macro"]
        parts += [f"NEWS ({'official source' if m['official'] else 'two established outlets'}; explain what it means "
                  f"for a beginner, no forecast, no stock call, never a company's share price or move): {m['headline']}. "
                  f"{m['facts']}", f"WHY IT MATTERS (general facts): {m['why_it_matters']}"]
        if m.get("sector_today") is not None:
            parts.append(f"The {m['sector']} index moved {m['sector_today']:+.2f}% today (an index, may be said).")
        parts += [f"HISTORY (quote only as an average, not a prediction): {h}" for h in facts.get("history", [])]
        parts.append("Beat kinds to use: hook, news, explain, history, takeaway, question.")
    elif f == "news":
        n = facts["news"]
        parts += ["NEWS (official NSE announcement; explain what it means for a beginner, do not mention any price, do "
                  f"not predict): {n['symbol']} - {n['subject']}. {n['text']}"]
        if n.get("history"):
            parts.append(f"HISTORY for this type of news (may be quoted as an average, not a prediction): {n['history']}")
        parts.append("Beat kinds to use: hook, news, explain, history, takeaway, question.")
    elif f == "pathshala":
        t, point = facts["lesson"]
        parts += [f"PAISA KI PATHSHALA - a {facts['total']}-day course from zero to confident investor. Today is DIN "
                  f"{facts['day_no']} of {facts['total']}: '{t}'.",
                  f"THE LESSON (explain only this, simply): {point}",
                  "Make it stick: one desi example or analogy (chai, cricket, ghar ka budget...), one common mistake "
                  "people make about it, and a one-line recap. Say 'Din {n}' early so viewers follow the course. No "
                  "numbers beyond the lesson's own; no advice.".replace("{n}", str(facts["day_no"])),
                  "Beat kinds to use: hook, explain, story, twist, takeaway, question."]
    elif f == "night":
        parts += [NIGHT_RULES, "TODAY'S STORIES AND FACTS (use only these):", facts["night_text"],
                  "Beat kinds to use: hook, news, explain, history, market, watch, takeaway, question."]
    elif f == "company":
        parts += [COMPANY_RULES, "FACTS (use only these; every number as given):", facts["company_text"],
                  "Beat kinds to use: hook, story, explain, sector, history, takeaway, question."]
    elif f == "market":
        parts += [MARKET_RULES, "TODAY'S MARKET FACTS (use only these):", facts["market_text"],
                  "Beat kinds to use: hook, market, sector, flows, news, explain, history, watch, question."]
    else:
        t, story, lesson = facts["story"]
        parts += [f"TRUE STORY from Indian market history - {t}: {story}", f"ITS LESSON: {lesson}",
                  "Tell it like a thriller: the rise, the secret, the fall, the lesson. Use only these facts.",
                  "Beat kinds to use: hook, story, twist, takeaway, question."]
    if facts.get("breaking"):
        parts.append("BREAKING: this goes out soon after the official source - quality first: 45-60 seconds, 9-12 "
                     "beats, 100-130 words. Beat 1 says the news in at most 10 words ('Abhi abhi...'). Beat 2 names "
                     "the source. Explain clearly what it means for a beginner, use the background if given, quote "
                     "history only if given. End with a comment question. Never a share price, share move, forecast "
                     "or buy/sell.")
    if facts.get("next"):
        parts.append(f"TOMORROW'S TOPIC (tease it in the last beat): {facts['next']}")
    if facts.get("cast"):
        from .cast import prompt_block
        parts.append(prompt_block())
    if facts.get("recent_hooks"):
        parts.append("RECENT HOOKS of our last Reels - open differently (another structure, not just other words): "
                     + " | ".join(facts["recent_hooks"]))
    return "\n".join(parts)


def write(facts: dict, client=None) -> tuple[ReelScript, str]:
    """(script, source): source is 'claude' or 'template' (no key, refusal, error or failed checks)."""
    companies = (([facts["news"]["symbol"]] if facts.get("news") else []) + list(facts.get("companies", []))
                 + list((facts.get("macro") or {}).get("companies", [])))
    strict = facts.get("format") in ("market", "night") or bool(facts.get("macro")) or bool(facts.get("results"))
    company_mode = facts.get("format") == "company"
    if not os.getenv("ANTHROPIC_API_KEY") and client is None:
        return template(facts), "template (no ANTHROPIC_API_KEY)"
    import anthropic
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": _brief(facts)}]
    for attempt in range(2):
        try:
            kw = dict(model=MODEL, max_tokens=16000, system=SYSTEM, messages=messages, output_format=ReelScript,
                      output_config={"effort": "low" if facts.get("breaking") else "medium"}, fallbacks="default")
            resp = None
            if facts.get("breaking"):                                      # breaking news: fast mode if available
                try:
                    resp = client.beta.messages.parse(speed="fast", betas=["server-side-fallback-2026-07-01",
                                                                            "fast-mode-2026-02-01"], **kw)
                except (anthropic.RateLimitError, anthropic.BadRequestError) as e:
                    log.warning("fast mode unavailable (%s)", type(e).__name__)
            resp = resp or client.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], **kw)
        except anthropic.APIConnectionError as e:
            log.warning("Claude unreachable: %s", e)
            return template(facts), "template (Claude unreachable)"
        except anthropic.APIStatusError as e:
            log.warning("Claude error %s: %s", e.status_code, e.message)
            return template(facts), f"template (Claude error {e.status_code})"
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return template(facts), "template (Claude declined)"
        script = resp.parsed_output
        issues = check(script, companies, strict, company_mode)
        if company_mode:
            from .company import BANNED
            if BANNED.search(script.caption):
                issues.append(f"caption: '{BANNED.search(script.caption).group(0)}'")
        if facts.get("format") in ("market", "company"):                               # the wrap is a little longer
            issues = [i for i in issues if not i.startswith("narration is") or not 100 <= _words(script) <= 170]
        if facts.get("format") == "night":                                # the night Reel is longer
            issues = [i for i in issues if not (i.startswith("narration is") and 140 <= _words(script) <= 240)
                      and not (i.endswith("beats (needs 8-12)") and len(script.scenes) <= 20)]
        if facts.get("breaking"):                                         # the breaking Reel is shorter
            issues = [i for i in issues if not i.startswith("narration is") or not 70 <= _words(script) <= 150]
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
    f = facts["format"]
    nxt = f" Kal: {facts['next']}" if facts.get("next") else ""
    if f == "myth":
        t, point = facts["lesson"]
        beats = [Scene(kind="hook", narration=facts["myth"], on_screen=facts["myth"][:40]),
                 Scene(kind="truth", narration="Humare test ka sach: " + facts["truth"], on_screen="Humare test ka sach"),
                 Scene(kind="explain", narration=f"{t}: {point}", on_screen=t[:40])]
    elif f == "news" and facts.get("results"):
        r = facts["results"]
        beats = [Scene(kind="news", narration=f"{r['company']} ke {r['quarter']} ke results aaye.", on_screen="Results")]
        beats += [Scene(kind="explain", narration=p["text"], on_screen="Results") for p in r["points"][:4]]
    elif f == "news" and facts.get("macro"):
        m = facts["macro"]
        beats = [Scene(kind="news", narration=f"{m['headline']}. {m['facts']}", on_screen=m["sector"] + " news"),
                 Scene(kind="explain", narration=m["why_it_matters"], on_screen="Iska matlab")]
    elif f == "news":
        n = facts["news"]
        beats = [Scene(kind="news", narration=f"Aaj NSE par {n['symbol']} ne announce kiya: {n['subject']}.",
                       on_screen=f"{n['symbol']}: news")]
        if n.get("history"):
            beats.append(Scene(kind="history", narration=n["history"], on_screen="Itihaas kya kehta hai"))
    elif f == "pathshala":
        t, point = facts["lesson"]
        beats = [Scene(kind="hook", narration=f"Paisa ki pathshala, din {facts['day_no']}: {t}.", on_screen=t[:40]),
                 Scene(kind="explain", narration=point, on_screen=t[:40])]
    elif f == "night":
        beats = [Scene(kind="news", narration=f"Aaj ki badi khabar: {t}.", on_screen=t[:40])
                 for t in facts["night_lines"][:6]]
    elif f == "company":
        beats = [Scene(kind="story", narration=line[:200], on_screen=line[:40]) for line in facts["company_lines"][:6]]
    elif f == "market":
        beats = [Scene(kind="market", narration=line, on_screen=line[:40]) for line in facts["market_lines"][:6]]
        beats.append(Scene(kind="question", narration="Aaj ki kaunsi baat nayi lagi? Comment mein batao. Us dost ko "
                                                      "bhejo jo market seekhna chahta hai. Kal ka anumaan Story poll "
                                                      "mein do.", on_screen="Comment mein batao"))
    else:
        t, story, lesson = facts["story"]
        beats = [Scene(kind="story", narration=story, on_screen=t[:40]),
                 Scene(kind="takeaway", narration="Seekh: " + lesson, on_screen="Seekh")]
    if beats[-1].kind != "question":
        beats.append(Scene(kind="question", narration="Aapka kya experience hai? Comment mein batao. Us dost ko bhejo "
                                                      "jo yeh galti kar sakta hai." + nxt,
                           on_screen="Comment mein batao"))
    title = SERIES[f]
    return ReelScript(title=title, scenes=beats, caption=f"{title} | aaj ka market lesson\n{beats[-1].narration}\n"
                      "📤 Bhejo us dost ko jo market seekhna chahta hai\n🔖 Save karo",
                      hashtags=["stockmarketindia", "investing", "nifty50", "financialeducation",
                                                      "hinglish"])
