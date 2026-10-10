"""RAAT KI REPORT: the night Reel (21:30) - all of the day's important news together, explained with more clarity.

It gathers what the day already established, with its sources:
- the results found by the 19:15 compilation (RESULTS TODAY),
- every story the breaking watch read from an official document, and every catch-up find,
- trusted news of the 19:15 compilation not covered yet,
- up to 2 important filings the watch only alerted (daily cap, gap or age) - read now from the official document,
plus the market's close and what is due on the next session. With 2+ stories it is one longer Reel (90-120 s); with
one, that story's explainer; with none, a MARKET KI KAHANI story.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import breaking, market

log = logging.getLogger("trader.reel.night")
MAX_STORIES = 5


def _key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())[:30]


def gather(today: date, out_dir: Path, client=None, max_reads: int = 2) -> dict:
    """The night bundle: {stories, close, calendar, history}. Stories carry their source links."""
    saved = market.load_saved(today, out_dir) or {}
    p = out_dir / "breaking.json"
    st = json.loads(p.read_text()) if p.exists() else {}
    day = today.isoformat()
    stories, seen = [], set()

    def add(kind, title, points, why, urls, company="", importance="major"):
        k = _key(company) if kind == "results" and company else _key(title)
        if k in seen or not points:
            return
        seen.add(k)
        stories.append({"kind": kind, "title": title, "points": points[:5], "why": why, "sources": urls[:3],
                        "company": company, "importance": importance})

    for r in saved.get("results", []):
        add("results", f"{r['company']} results ({r['quarter']})", [x["text"] for x in r["points"]], "",
            sorted({u for x in r["points"] for u in x["source_urls"]}), r["company"])
    for c in [x for x in st.get("covered", []) if x.get("date") == day]:
        add("results" if c["kind"] == "results" else "news", c["headline"],
            c["points"] + [f"Background: {x['text']}" for x in c.get("context", [])], c["why_it_matters"], [c["url"]],
            c["company"] if c["kind"] == "results" else "", c.get("importance", "major"))
    for it in saved.get("news", []):
        add("news", it["headline"], [it["facts"]], it["why_it_matters"], it["source_urls"])
    missed = sorted([m for m in st.get("missed", []) if m.get("date") == day], key=breaking.priority)
    for m in missed[:max_reads]:                                           # alerted but never read: read it now
        if (_key(m.get("company") or "") if m.get("kind") == "results" else _key(m.get("title") or "")) in seen:
            continue
        got = breaking.read(m, client, fast=False)
        if got and got["material"]:
            add("results" if m["kind"] == "results" else "news", got["headline"], got["points"],
                got["why_it_matters"], [got["url"]], m["company"] if m["kind"] == "results" else "")
    order = {"major": 0, "notable": 1, "minor": 2}
    stories.sort(key=lambda s: (order.get(s["importance"], 1), s["kind"] != "results"))
    close = []
    if saved.get("broad"):
        n = saved["broad"].get("Nifty 50")
        if n:
            close.append(f"Nifty 50 closed {n['close']:,.0f} ({n['pct']:+.2f}%)")
        if saved.get("sectors"):
            (wn, wp), (bn, bp) = saved["sectors"][0], saved["sectors"][-1]
            close.append(f"best sector {bn} {bp:+.2f}%, weakest {wn} {wp:+.2f}%")
        if saved.get("fii") is not None:
            close.append(f"foreign investors net {saved['fii']:+,.0f} crore, Indian funds {saved.get('dii') or 0:+,.0f} "
                         "crore (provisional)")
    return {"stories": [s for s in stories if s["importance"] != "minor"][:MAX_STORIES], "close": close,
            "calendar": saved.get("calendar", []), "history": saved.get("history", [])[:1]}


def sheet(b: dict) -> str:
    lines = []
    for i, s in enumerate(b["stories"], 1):
        lines.append(f"STORY {i} ({s['kind']}): {s['title']}")
        lines += [f"  - {x}" for x in s["points"]]
        if s["why"]:
            lines.append(f"  Why it matters: {s['why']}")
    if b["close"]:
        lines.append("MARKET CLOSE: " + "; ".join(b["close"]))
    lines += [f"HISTORY: {h}" for h in b["history"]]
    if b["calendar"]:
        lines.append("NEXT SESSION: " + "; ".join(b["calendar"]))
    return "\n".join(lines)


def sources(b: dict) -> List[str]:
    return sorted({market._domain(u) for s in b["stories"] for u in s["sources"]})


def single(b: dict) -> Optional[dict]:
    """With one story: the facts of its explainer (results or news), else None."""
    if len(b["stories"]) != 1:
        return None
    s = b["stories"][0]
    if s["kind"] == "results":
        return {"results": {"company": s["company"] or s["title"], "quarter": "the latest quarter", "official": True,
                            "points": [{"text": x, "source_urls": s["sources"]} for x in s["points"]]},
                "companies": [s["company"]] if s["company"] else []}
    return {"macro": {"headline": s["title"], "facts": " ".join(s["points"]), "why_it_matters": s["why"],
                      "sector": "Whole market", "companies": [], "official": True, "source_urls": s["sources"]},
            "history": []}
