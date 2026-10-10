"""News outlets' own RSS feeds, read by our server - the way around sites that block Anthropic's web crawler.

Reuters, ET, Mint, Moneycontrol, The Hindu BusinessLine and others refuse Claude's web search, but publish public RSS
feeds (headline, summary, link, time) for any reader. The server reads them (plus Google News searches, which also
cover Reuters) and hands the recent items to Claude as material with their links; they count as retrieved sources for
the trusted-news rule, so "two different outlets" can be met with these outlets again.

Feed addresses change now and then: `trader.run reel --slot feeds` lists which ones answer; override them in
trader.yaml under reel.news_feeds: {name: url}.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import requests

log = logging.getLogger("trader.reel.feeds")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
                    "Safari/537.36", "Accept": "application/rss+xml, application/xml, text/xml, */*"}
GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"

OUTLET_FEEDS = {
    "Economic Times - Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Economic Times - Top": "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    "Moneycontrol - Latest": "https://www.moneycontrol.com/rss/latestnews.xml",
    "Moneycontrol - Markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Mint - Markets": "https://www.livemint.com/rss/markets",
    "Mint - Companies": "https://www.livemint.com/rss/companies",
    "Business Standard - Markets": "https://www.business-standard.com/rss/markets-106.rss",
    "BusinessLine - Markets": "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "Financial Express - Market": "https://www.financialexpress.com/market/feed/",
    "NDTV Profit": "https://feeds.feedburner.com/ndtvprofit-latest",
    "CNBC-TV18 - Market": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    # Google News searches: every outlet, Reuters included
    "Google News - Indian markets": GNEWS.format(q=quote_plus("Sensex OR Nifty OR RBI OR SEBI when:1d")),
    "Google News - Reuters India": GNEWS.format(q=quote_plus("site:reuters.com India markets when:1d")),
}


def _text(x: Optional[str]) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x or "")).strip()


def _when(x: str) -> Optional[datetime]:
    try:
        from zoneinfo import ZoneInfo
        d = parsedate_to_datetime(x)
        return d.astimezone(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError, IndexError):
        return None


def parse(name: str, xml: bytes) -> List[dict]:
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = _text(it.findtext("title"))
        if not (link and title):
            continue
        src = it.find("source")                                           # Google News names the real outlet
        outlet = _text(src.text) if src is not None and src.text else name.split(" - ")[0]
        site = (src.get("url") if src is not None else "") or link
        out.append({"title": title, "summary": _text(it.findtext("description"))[:400], "link": link,
                    "outlet": outlet, "site": urlparse(site).netloc.lower().removeprefix("www."),
                    "time": _when(it.findtext("pubDate") or "")})
    return out


def collect(now: datetime, hours: int = 24, feeds: Optional[Dict[str, str]] = None, get=None) -> tuple:
    """(items of the last `hours`, newest first, de-duplicated by title; {feed: ok?})."""
    get = get or (lambda u: requests.get(u, headers=UA, timeout=20))
    items, status, seen = [], {}, set()
    for name, url in (feeds or OUTLET_FEEDS).items():
        try:
            r = get(url)
            got = parse(name, r.content) if r.status_code == 200 else []
            status[name] = bool(got)
        except Exception as e:                                             # noqa: BLE001 - one feed down is fine
            log.warning("feed %s: %s", name, e)
            status[name] = False
            continue
        for it in got:
            k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
            if k in seen or (it["time"] and now - it["time"] > timedelta(hours=hours)):
                continue
            seen.add(k)
            items.append(it)
    items.sort(key=lambda x: x["time"] or now, reverse=True)
    return items, status


def material(items: List[dict], limit: int = 120) -> str:
    """The items as text for Claude: one per line, with outlet, time and link."""
    lines = [f"- [{it['outlet']}] {it['time']:%d %b %H:%M} | {it['title']} | {it['summary'][:240]} | {it['link']}"
             if it["time"] else f"- [{it['outlet']}] {it['title']} | {it['summary'][:240]} | {it['link']}"
             for it in items[:limit]]
    return "\n".join(lines)


def corroborated(items: List[dict], since: datetime, pattern) -> int:
    """How many different outlets carried a headline matching `pattern` since `since` (a cheap 'big story' signal)."""
    return len({it["site"] or it["outlet"] for it in items
                if it["time"] and it["time"] >= since and pattern.search(it["title"])})
