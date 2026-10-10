"""COMPANY KI KUNDLI: a weekly case study of one well-known listed company - what it does, how it earns, its size,
history and the risks it reports - told as a story. Education, never a view on the share.

Rules (SEBI): no share price, market value, valuation (P/E, cheap/expensive), target or buy/sell view; every fact
comes from a trusted page the search retrieved (the market wrap's list: official sites, exchanges, established
outlets); company results are facts, not price data.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from . import market

log = logging.getLogger("trader.reel.company")

# well-known companies most viewers use or know, in a fixed rotation
COMPANIES = [("RELIANCE", "Reliance Industries"), ("TCS", "Tata Consultancy Services"), ("HDFCBANK", "HDFC Bank"),
             ("ASIANPAINT", "Asian Paints"), ("MARUTI", "Maruti Suzuki"), ("ITC", "ITC"), ("TITAN", "Titan Company"),
             ("BHARTIARTL", "Bharti Airtel"), ("HINDUNILVR", "Hindustan Unilever"), ("LT", "Larsen & Toubro"),
             ("DMART", "Avenue Supermarts (DMart)"), ("INFY", "Infosys"), ("SBIN", "State Bank of India"),
             ("PIDILITIND", "Pidilite Industries"), ("NESTLEIND", "Nestle India"), ("EICHERMOT", "Eicher Motors"),
             ("SUNPHARMA", "Sun Pharmaceutical"), ("ULTRACEMCO", "UltraTech Cement"), ("BRITANNIA", "Britannia"),
             ("ICICIBANK", "ICICI Bank"), ("TATASTEEL", "Tata Steel"), ("NTPC", "NTPC"), ("M&M", "Mahindra & Mahindra"),
             ("BAJFINANCE", "Bajaj Finance"), ("HCLTECH", "HCL Technologies"), ("COALINDIA", "Coal India")]

BANNED = re.compile(r"share price|stock price|price target|\btarget\b|market (?:cap|value)|valuation|\bp/?e\b|"
                    r"undervalued|overvalued|\bcheap\b|\bexpensive\b|\bsasta\b|\bmehnga\b|multibagger|"
                    r"\b(?:buy|sell|hold|accumulate)\b|kharid(?:o|iye|na chahiye)|bech(?:o|iye|na chahiye)", re.I)


class Fact(BaseModel):
    text: str = Field(description="One fact in plain English, with its period or date; no share price or valuation")
    source_urls: List[str]


class CompanyFacts(BaseModel):
    business: str = Field(description="What the company does, in two plain sentences")
    how_it_earns: List[Fact] = Field(description="Its main businesses / revenue sources, with their share if stated")
    numbers: List[Fact] = Field(description="Size: latest annual revenue, profit, debt, employees, customers, "
                                            "stores, market share - as reported, with the year")
    history: List[Fact] = Field(description="Founding and 2-4 turning points")
    risks: List[Fact] = Field(description="Risks the company itself or the outlets report")


PROMPT = """Research the Indian listed company {name} (NSE: {symbol}) for a beginner-friendly case study. Find: what it does, how it earns money (main businesses and their share of revenue), its size (latest annual revenue and profit, debt, employees, customers, market share - with the year), its founding and 2-4 turning points, and the main risks it reports in its annual report or that outlets report.

Use only the search results; prefer the company's filings on nseindia.com / bseindia.com and established outlets. Give each fact with its year and the URL of the page you used. Do NOT include the share price, market value, valuation (P/E), analyst targets or any buy/sell view."""

STRUCTURE = """Turn these research notes into the case-study fields. Use only facts and URLs from the notes; leave out anything without a URL, and leave out any share price, market value, valuation, target or buy/sell view.

NOTES:
{notes}"""


def pick(episodes: int) -> tuple:
    return COMPANIES[episodes % len(COMPANIES)]


def research(symbol: str, name: str, client=None, max_searches: int = 8,
             feed_items: Optional[List[dict]] = None) -> tuple[Optional[dict], str]:
    """(sourced facts, note). Each fact needs a trusted page the search retrieved and must pass the ban list."""
    import anthropic
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return None, "no ANTHROPIC_API_KEY"
    client = client or anthropic.Anthropic()
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches,
            "allowed_domains": market.OFFICIAL + market.OUTLETS}
    from datetime import datetime
    items = market.outlet_items(datetime.now(), hours=24 * 60, query=f'"{name}"') if feed_items is None else feed_items
    messages = [{"role": "user", "content": market.with_feeds(PROMPT.format(name=name, symbol=symbol), items)}]
    retrieved, notes = {it["link"] for it in items}, []
    try:
        for _ in range(4):
            resp = market.create(client, False, model=market.MODEL, max_tokens=16000, messages=messages,
                                 tools=[tool], output_config={"effort": "medium"},
                                 betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            for b in resp.content:
                if b.type == "web_search_tool_result" and isinstance(b.content, list):
                    retrieved |= {r.url for r in b.content if getattr(r, "url", None)}
                elif b.type == "text":
                    notes.append(b.text)
            if resp.stop_reason != "pause_turn":
                break
            messages = messages[:1] + [{"role": "assistant", "content": resp.content}]
        if not notes or not retrieved:
            return None, "nothing found"
        from .feeds import material
        parsed = client.beta.messages.parse(
            model=market.MODEL, max_tokens=16000, output_format=CompanyFacts, output_config={"effort": "low"},
            messages=[{"role": "user", "content": STRUCTURE.format(
                notes="\n".join(notes)[:60000] + ("\n\nOUTLET FEED ITEMS:\n" + material(items, 60) if items else ""))}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        cf = parsed.parsed_output
    except anthropic.APIError as e:
        log.warning("company research failed: %s", e)
        return None, f"research failed ({type(e).__name__})"
    if cf is None:
        return None, "no structured facts"

    def keep(facts: List[Fact]) -> List[dict]:
        out = []
        for f in facts:
            urls = market._sourced(f, retrieved)
            if urls and not BANNED.search(f.text):
                out.append({"text": f.text, "source_urls": urls})
        return out

    got = {"symbol": symbol, "name": name, "business": "" if BANNED.search(cf.business) else cf.business,
           "how_it_earns": keep(cf.how_it_earns), "numbers": keep(cf.numbers), "history": keep(cf.history),
           "risks": keep(cf.risks)}
    n = sum(len(got[k]) for k in ("how_it_earns", "numbers", "history", "risks"))
    total = sum(len(getattr(cf, k)) for k in ("how_it_earns", "numbers", "history", "risks"))
    if n < 4:
        return None, f"only {n} sourced facts"
    return got, f"{n} of {total} facts passed the source and SEBI checks"


def fact_sheet(c: dict) -> str:
    lines = [f"COMPANY: {c['name']} (NSE: {c['symbol']})", f"BUSINESS: {c['business']}"]
    for k, label in (("how_it_earns", "HOW IT EARNS"), ("numbers", "SIZE"), ("history", "HISTORY"),
                     ("risks", "RISKS IT REPORTS")):
        lines += [f"{label}: {f['text']}" for f in c[k]]
    return "\n".join(lines)


def sources(c: dict) -> List[str]:
    seen = []
    for k in ("how_it_earns", "numbers", "history", "risks"):
        for f in c[k]:
            for u in f["source_urls"]:
                if u not in seen:
                    seen.append(u)
    return seen
