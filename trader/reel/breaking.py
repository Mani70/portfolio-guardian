"""BREAKING SAMJHO: a short Reel within minutes of official, market-moving news - first, but only from the source.

  python -m trader.run reel --slot breaking     # cron every 5 minutes, 08:00-23:55 (job.sh never runs two at once)

What counts (official sources only - speed never beats trust):
- NSE announcements of well-known companies (trading >= ₹200 crore a day): results, and the Addendum 22 event types
  (order won, acquisition, rating change, auditor / CEO resignation, default, buyback, bonus, split, fund raising);
  statements, clarifications and "material" disclosures of these companies go to a quick Claude check first;
- press releases of RBI, SEBI, the Government of India (PIB) and the US Federal Reserve that match market words
  (feeds can be changed in trader.yaml reel.breaking.feeds).

Claude reads the official document itself (web fetch of the NSE PDF / press release) and writes only what it says;
the Reel quotes the source, says what it means, and history for that type of news (Addendum 22) - never a share
price, share move, forecast or call. At most `max_per_day` Reels a day (default 3), 30 minutes apart; anything
beyond that, and anything Claude judges not material, comes as a one-line Telegram alert instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Literal, Optional

import requests
from pydantic import BaseModel, Field

from . import market

log = logging.getLogger("trader.reel.breaking")
FEEDS = {"RBI": "https://www.rbi.org.in/pressreleases_rss.xml",
         "SEBI": "https://www.sebi.gov.in/sebirss.xml",
         "Government of India (PIB)": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
         "US Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml"}
MACRO = re.compile(r"repo rate|monetary policy|policy rate|\bcrr\b|\bslr\b|inflation|\bgdp\b|tariff|customs duty|"
                   r"\bgst\b|h-?1b|\bvisa|green card|\bperm\b|futures|options|\bf&o\b|derivative|margin|\bipo\b|"
                   r"mutual fund|interest rate|federal funds|\bfomc\b|sanction|crude|budget|income tax|"
                   r"foreign portfolio|\bfpi\b|listing|insider|ban\b|penalt", re.I)
CHECK_FIRST = re.compile(r"\bupdates?\b|clarification|material|press release|statement|outcome of board", re.I)
RESULTS = re.compile(r"financial result", re.I)
BIG = 200.0                                                               # ₹ crore traded a day: well-known companies


class Point(BaseModel):
    text: str = Field(description="One fact exactly as the document states it, with numbers and dates")


class Context(BaseModel):
    text: str = Field(description="Background from an established outlet or official site (e.g. the year-ago quarter, "
                                  "the sector picture) - no forecast, no share price or move")
    source_urls: List[str]


class Breaking(BaseModel):
    material: bool = Field(description="True only if this could matter to many investors (a big number, a policy "
                                       "change, a leadership exit, a default, a large deal) - routine filings are False")
    headline: str
    points: List[Point] = Field(description="3-6 facts from the document; for results: revenue, profit and their "
                                            "change from a year earlier, reasons given, dividend")
    why_it_matters: str = Field(description="Which part of the market it concerns and why, as general facts - no "
                                            "forecast, no share price or share move, no buy/sell")
    sector: Literal["IT", "Bank", "Pharma", "Auto", "FMCG", "Metal", "Realty", "Energy", "Oil & Gas", "Whole market",
                    "Other"]
    companies: List[str]
    context: List[Context] = Field(default_factory=list)


READ = """Read this official document and report what it says, for Indian retail investors: {url}

It is: {what}

1. Use the web_fetch tool on that exact URL. Report the facts stated in the document, with their numbers and dates.
2. Then, if it helps a beginner understand it, use web_search (up to 3 searches) for short background from established outlets or official sites - e.g. the same quarter last year, or what the policy changes - with the URLs.
3. Say plainly whether it is material for many investors (a big number, a policy change, a leadership exit, a default, a large deal) or routine.
No forecasts, no share prices or share moves, no stock views."""

STRUCTURE = """Turn these notes on an official document into the fields. Only facts from the notes; no share price, share move, forecast, target or buy/sell view.

NOTES:
{notes}"""


# ---------------------------------------------------------------- sources
def nse_new(get, seen: set, today: date, size: dict) -> List[dict]:
    """New announcements of well-known companies worth a look, oldest first."""
    import sys
    sys.path.insert(0, str(market.ROOT / "research"))
    from news22 import classify                                            # the study's own definitions
    rows = get(f"corporate-announcements?index=equities&from_date={today:%d-%m-%Y}&to_date={today:%d-%m-%Y}") or []
    out = []
    for r in rows:
        sid = f"nse:{r.get('seq_id')}"
        if sid in seen or size.get(r.get("symbol"), 0) < BIG:
            continue
        cat, text = r.get("desc") or "", r.get("attchmntText") or ""
        kinds = classify(cat, text)
        kind = ("results" if RESULTS.search(cat + " " + text) else kinds[0] if kinds else
                "check" if CHECK_FIRST.search(cat) else None)
        if kind and r.get("attchmntFile"):
            out.append({"id": sid, "source": "NSE", "kind": kind, "symbol": r.get("symbol"),
                        "company": r.get("sm_name") or r.get("symbol"), "title": f"{cat}: {text[:200]}",
                        "url": r["attchmntFile"], "time": r.get("an_dt"), "value": size.get(r.get("symbol"), 0)})
    return sorted(out, key=lambda x: x["time"] or "")


def feed_new(feeds: dict, seen: set, fetch=None, max_age_hours: int = 12) -> List[dict]:
    """New items of the official press-release feeds that use market words."""
    fetch = fetch or (lambda url: requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}))
    out = []
    for name, url in feeds.items():
        try:
            r = fetch(url)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
        except Exception as e:                                             # noqa: BLE001 - one feed down is fine
            log.warning("feed %s: %s", name, e)
            continue
        for it in root.iter("item"):
            title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
            sid = f"feed:{link or title}"
            if not link or sid in seen or not MACRO.search(title + " " + (it.findtext("description") or "")):
                continue
            out.append({"id": sid, "source": name, "kind": "macro", "symbol": "", "company": name, "title": title,
                        "url": link, "time": it.findtext("pubDate") or "", "value": 0})
    return out


def published(c: dict) -> Optional[datetime]:
    """When the source published it, in Indian time (naive), or None if unknown."""
    t = c.get("time") or ""
    try:
        return datetime.strptime(t, "%d-%b-%Y %H:%M:%S")                    # NSE (already IST)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from zoneinfo import ZoneInfo
        d = parsedate_to_datetime(t)                                        # RSS
        return d.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError, IndexError):
        return None


def fresh(c: dict, now: datetime, max_age_min: int = 120) -> bool:
    """Still worth a Reel: published within max_age_min, or overnight (23:00-07:00) and it is not yet 09:30 - the
    audience was asleep, so it is news to them in the morning."""
    pub = published(c)
    if pub is None:
        return True
    if now - pub <= timedelta(minutes=max_age_min):
        return True
    night = pub.hour >= 23 or pub.hour < 7
    morning = pub.replace(hour=9, minute=30, second=0) + (timedelta(days=1) if pub.hour >= 23 else timedelta())
    return night and now <= morning and now - pub <= timedelta(hours=12)


INSTANT = {"results", "order won", "acquisition", "rating upgrade", "rating downgrade", "auditor resigned",
           "MD/CEO/CFO resigned", "default / insolvency", "buyback", "bonus issue", "stock split", "fund raising"}


def priority(c: dict) -> tuple:
    order = {"results": 0, "macro": 1, "check": 3}
    return (order.get(c.get("kind"), 2), -(c.get("value") or 0))


# ---------------------------------------------------------------- reading the document
def read(c: dict, client=None, fast: bool = True) -> Optional[dict]:
    """Claude reads the official document (web fetch) and returns the facts, or None if it could not."""
    import anthropic
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return None
    client = client or anthropic.Anthropic()
    tools = [{"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2,
              "allowed_domains": market.OFFICIAL + ["nseindia.com"]},
             {"type": "web_search_20260209", "name": "web_search", "max_uses": 3,
              "allowed_domains": market.OFFICIAL + market.OUTLETS}]
    what = f"{c['source']} - {c['company']}: {c['title']}"
    messages = [{"role": "user", "content": READ.format(url=c["url"], what=what)}]
    notes, fetched, retrieved = [], False, set()
    try:
        for _ in range(3):
            resp = market.create(client, fast, model=market.MODEL, max_tokens=16000, messages=messages, tools=tools,
                                 output_config={"effort": "medium"}, betas=["server-side-fallback-2026-07-01"],
                                 fallbacks="default")
            for b in resp.content:
                if b.type == "web_fetch_tool_result" and getattr(b.content, "type", "") == "web_fetch_result":
                    fetched = True
                elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                    retrieved |= {r.url for r in b.content if getattr(r, "url", None)}
                elif b.type == "text":
                    notes.append(b.text)
            if resp.stop_reason != "pause_turn":
                break
            messages = messages[:1] + [{"role": "assistant", "content": resp.content}]
        if not (fetched and notes):
            return None
        parsed = client.beta.messages.parse(
            model=market.MODEL, max_tokens=8000, output_format=Breaking, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": STRUCTURE.format(notes="\n".join(notes)[:40000])}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        b = parsed.parsed_output
    except anthropic.APIError as e:
        log.warning("breaking read failed: %s", e)
        return None
    if b is None:
        return None
    from .script import share_talk
    from .company import BANNED
    pts = [p.text for p in b.points if not share_talk(p.text) and not BANNED.search(p.text)]
    if not market.safe_text(f"{b.headline} {b.why_it_matters}", b.companies) or len(pts) < 2:
        return None
    ctx = [{"text": x.text, "source_urls": market._sourced(x, retrieved)} for x in b.context]
    ctx = [x for x in ctx if x["source_urls"] and not share_talk(x["text"]) and market.safe_text(x["text"], b.companies)]
    return {"material": b.material, "headline": b.headline, "points": pts, "why_it_matters": b.why_it_matters,
            "sector": b.sector, "companies": b.companies, "url": c["url"], "source": c["source"], "context": ctx[:3]}


def facts(c: dict, got: dict, episode: int) -> dict:
    """The script facts for the breaking Reel (reusing the NEWS SAMJHO explainers)."""
    from .content import _reaction
    f = {"date": date.today().isoformat(), "format": "news", "series": "BREAKING SAMJHO", "breaking": True,
         "episode": episode, "companies": got["companies"] + ([c["company"]] if c["symbol"] else [])}
    src = [got["url"]]
    ctx = [{"text": x["text"], "source_urls": x["source_urls"]} for x in got.get("context", [])]
    if c["kind"] == "results":
        f["results"] = {"company": c["company"], "quarter": "the latest quarter", "official": True,
                        "points": [{"text": p, "source_urls": src} for p in got["points"]] + ctx}
    else:
        f["macro"] = {"headline": got["headline"],
                      "facts": " ".join(got["points"] + [f"Background: {x['text']}" for x in ctx]), "official": True,
                      "why_it_matters": got["why_it_matters"], "sector": got["sector"], "companies": got["companies"],
                      "source_urls": src}
        hist = _reaction(c["kind"]) if c["kind"] not in ("macro", "check") else None
        f["history"] = [hist] if hist else []
    return f


# ---------------------------------------------------------------- the catch-up search
class SweepItem(market.NewsItem):
    importance: Literal["major", "notable", "minor"] = Field(description="For Indian investors as a whole")


class SweepList(BaseModel):
    items: List[SweepItem]


SWEEP = """It is {now} in India. Find the most important NEWS of the last 3 hours that can move the Indian stock market: government or regulator decisions (India or abroad), central banks, US policy that concerns Indian sectors (visas, tariffs, trade), crude oil, and big events at large Indian listed companies.

Already covered today - do NOT repeat these: {covered}

For each NEW event: the facts with their dates and the URLs of the pages you used, and how important it is for Indian investors as a whole (major / notable / minor). Use only the search results; no rumours, opinions, forecasts, share prices or stock calls."""

SWEEP_STRUCTURE = """Turn these notes into news items. Only events of the last 3 hours before {now} that are NOT in this covered list: {covered}. Use only facts and URLs from the notes; no forecasts, share prices or stock calls.

NOTES:
{notes}"""


def sweep(now: datetime, covered: List[str], client=None) -> List[dict]:
    """Trusted, important news of the last 3 hours that the watch has not covered (rule of Addendum 23 in code)."""
    import anthropic
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return []
    client = client or anthropic.Anthropic()
    listed = "; ".join(covered[-30:]) or "(nothing yet)"
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6,
            "allowed_domains": market.OFFICIAL + market.OUTLETS, "user_location": {"type": "approximate", "country": "IN"}}
    messages = [{"role": "user", "content": SWEEP.format(now=f"{now:%A %d %B %Y, %H:%M}", covered=listed)}]
    retrieved, notes = set(), []
    try:
        for _ in range(3):
            resp = market.create(client, False, model=market.MODEL, max_tokens=16000, messages=messages, tools=[tool],
                                 output_config={"effort": "medium"}, betas=["server-side-fallback-2026-07-01"],
                                 fallbacks="default")
            for b in resp.content:
                if b.type == "web_search_tool_result" and isinstance(b.content, list):
                    retrieved |= {r.url for r in b.content if getattr(r, "url", None)}
                elif b.type == "text":
                    notes.append(b.text)
            if resp.stop_reason != "pause_turn":
                break
            messages = messages[:1] + [{"role": "assistant", "content": resp.content}]
        if not notes or not retrieved:
            return []
        parsed = client.beta.messages.parse(
            model=market.MODEL, max_tokens=16000, output_format=SweepList, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": SWEEP_STRUCTURE.format(now=f"{now:%d %b %Y %H:%M}", covered=listed,
                                                                         notes="\n".join(notes)[:60000])}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        items = parsed.parsed_output.items if parsed.parsed_output else []
    except anthropic.APIError as e:
        log.warning("catch-up search failed: %s", e)
        return []
    kept = []
    for i in items:
        t = market.trusted([i], retrieved)
        if t:
            kept.append({**t[0], "importance": i.importance})
    return [k for k in kept if market.safe_text(f"{k['headline']} {k['facts']} {k['why_it_matters']}", k["companies"])]


def sweep_facts(it: dict, episode: int, today: str) -> dict:
    return {"date": today, "format": "news", "series": "ZAROORI KHABAR", "breaking": True, "episode": episode,
            "companies": it["companies"], "macro": {**it}, "history": []}


# ---------------------------------------------------------------- the watch
def watch(state: dict, now: datetime, get, size: dict, feeds: dict, max_per_day: int = 3, gap_min: int = 30,
          client=None, fetch=None, learn_only: bool = False, on_pick=None, on_read=None,
          max_age_min: int = 120) -> tuple[Optional[dict], List[str], dict]:
    """(facts for one Reel or None, alert lines, new state). learn_only: just remember what is already out.
    on_pick(c): called the moment an inherently material item is picked (the instant Story card); on_read(c, got):
    when Claude has read it and judged it material (the facts card)."""
    today = now.date().isoformat()
    seen = set(state.get("seen", []))
    made = [t for t in state.get("made", []) if t.startswith(today)]
    cands = nse_new(get, seen, now.date(), size) + (feed_new(feeds, seen, fetch) if feeds else [])
    alerts, chosen, reads = [], None, 0
    covered, missed = list(state.get("covered", [])), list(state.get("missed", []))
    for c in sorted(cands, key=priority):
        seen.add(c["id"])
        if learn_only:
            continue
        room = len(made) < max_per_day and (not made or now - datetime.fromisoformat(made[-1]) >=
                                            timedelta(minutes=gap_min))
        if chosen is None and room and reads < 3 and fresh(c, now, max_age_min):   # at most 3 reads a run
            reads += 1
            if on_pick and c["kind"] in INSTANT:
                on_pick(c)
            got = read(c, client)
            if got and got["material"]:
                covered.append({"date": today, "kind": c["kind"], "company": c["company"], "time": c.get("time"),
                                **{k: got.get(k) for k in ("headline", "points", "why_it_matters", "sector", "url",
                                                           "source")}, "context": got.get("context") or []})
                if on_read:
                    on_read(c, got)
                chosen = facts(c, got, state.get("episode", 0) + 1)
                chosen["published"] = c.get("time") or ""
                made.append(now.isoformat(timespec="minutes"))
                alerts.append(f"🚨 {c['source']} - {got['headline']}\n{c['url']}")
                continue
            if got is None and c["kind"] == "check":
                continue                                                   # unread routine filing: stay quiet
        if c["kind"] != "check":
            alerts.append(f"📰 {c['source']} - {c['company']}: {c['title'][:160]}\n{c['url']}")
            missed.append({"date": today, **{k: c.get(k) for k in ("id", "kind", "company", "title", "url", "source",
                                                                     "time", "symbol", "value")}})
    keep = {today, (now.date() - timedelta(days=1)).isoformat()}
    new = {"seen": sorted(seen)[-3000:], "made": made, "episode": state.get("episode", 0) + (1 if chosen else 0),
           "covered": [x for x in covered if x["date"] in keep][-60:],
           "missed": [x for x in missed if x["date"] in keep][-60:], "swept": state.get("swept", "")}
    return chosen, alerts, new


def run(notify, send_video, make, now: Optional[datetime] = None, out_dir: Optional[Path] = None, client=None,
        get=None, size: Optional[dict] = None, feeds: Optional[dict] = None, max_per_day: int = 3,
        fetch=None, send_photo=None, max_age_min: int = 120, sweep_hours: int = 2, gap_min: int = 30) -> str:
    now = now or datetime.now()
    started = datetime.now()
    out_dir = out_dir or market.ROOT / "cache" / "reel"
    p = out_dir / "breaking.json"
    state = json.loads(p.read_text()) if p.exists() else {}
    if get is None:
        s = market.nse_session()
        get = lambda path: market._get(s, path)                           # noqa: E731
    if size is None:
        size = market._traded_value(market.ROOT / "cache" / "insights")
    first_run = not state
    feeds = FEEDS if feeds is None else feeds
    if not first_run and now.minute % 5:                                   # NSE every minute, feeds every 5 minutes
        feeds = {}
    from .render import story_card

    def card(c, headline, lines, label):
        if send_photo:
            out_dir.mkdir(parents=True, exist_ok=True)
            f = story_card(headline, lines, c["source"], out_dir / f"story_{now:%H%M%S}_{label[:5]}.png", label)
            send_photo(f, "📲 Instagram STORY card - post it now; the Reel follows in a few minutes.\n" + c["url"])

    def on_pick(c):
        what = "results" if c["kind"] == "results" else c["kind"]
        card(c, f"{c['company'].replace(' Limited', '').replace(' Ltd', '')}: {what}",
             ["Official NSE filing, just now", "Details aur matlab - Reel mein, kuch minute mein"], "ABHI ABHI")

    def on_read(c, got):
        card(c, got["headline"], got["points"][:3], "KYA HUA")

    chosen, alerts, state = watch(state, now, get, size, feeds, max_per_day, gap_min, client=client, fetch=fetch,
                                  learn_only=first_run, on_pick=on_pick, on_read=on_read, max_age_min=max_age_min)
    last = datetime.fromisoformat(state["swept"]) if state.get("swept") else None
    if (chosen is None and not first_run and sweep_hours and 8 <= now.hour < 22
            and (last is None or now - last >= timedelta(hours=sweep_hours))):
        state["swept"] = now.isoformat(timespec="minutes")
        today = now.date().isoformat()
        done = [x["headline"] for x in state.get("covered", []) if x["date"] == today]
        found = sweep(now, done, client)
        made = [t for t in state.get("made", []) if t.startswith(today)]
        room = len(made) < max_per_day and (not made or now - datetime.fromisoformat(made[-1]) >=
                                            timedelta(minutes=gap_min))
        for it in found:
            state.setdefault("covered", []).append(
                {"date": today, "kind": "sweep", "company": ", ".join(it["companies"]), "time": now.strftime("%H:%M"),
                 "headline": it["headline"], "points": [it["facts"]], "why_it_matters": it["why_it_matters"],
                 "sector": it["sector"], "url": it["source_urls"][0], "source": market._domain(it["source_urls"][0]),
                 "context": [], "importance": it["importance"]})
            if chosen is None and room and it["importance"] == "major":
                chosen = sweep_facts(it, state.get("episode", 0) + 1, today)
                chosen["published"] = "in the last 3 hours"
                state["episode"] = state.get("episode", 0) + 1
                state.setdefault("made", []).append(now.isoformat(timespec="minutes"))
                card({"source": market._domain(it["source_urls"][0]), "url": it["source_urls"][0]},
                     it["headline"], [it["why_it_matters"]], "ZAROORI")
            else:
                alerts.append(f"🗞️ Found on the catch-up search ({it['importance']}): {it['headline']}\n"
                              f"{it['source_urls'][0]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))
    if first_run:                                                          # the first run only learns what is old
        return f"breaking: started watching ({len(state['seen'])} items already published today)"
    if chosen is None:
        for a in alerts[:5]:
            notify(a)
        return f"breaking: nothing made ({len(alerts)} alerts)"
    try:
        r = make(chosen)
    except Exception as e:                                                 # noqa: BLE001 - report once, not every minute
        log.exception("breaking Reel failed")
        notify(f"⚠️ Breaking Reel could not be made ({type(e).__name__}); the Story card and source link above are "
               "still good to post.")
        return f"breaking: Reel failed ({type(e).__name__})"
    took = (datetime.now() - started).seconds
    ok = send_video(r["video"], f"🚨 BREAKING SAMJHO - post now (source published {chosen.get('published') or '?'}; "
                                f"Reel ready in {took // 60}m{took % 60:02d}s)")
    notify(f"📝 Caption (copy-paste):\n\n{r['caption']}")
    notify("Breaking Reel: watch it once, open the source link, then post. "
           f"Script: {r['script_source']}. Voice: {r['voice']}." + ("" if ok else f" Video on the server: {r['video']}"))
    for a in alerts[1:5]:
        notify(a)
    return f"breaking: sent ({chosen.get('results', chosen.get('macro', {})).get('headline', 'results')})"
