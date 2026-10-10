"""The weekday market wrap: what the market did today, the day's trusted news, what history says, what is due next.

One compilation feeds two things: a plain-language daily brief for the owner on Telegram (with source links) and the
MARKET AAJ Reel. Rules (research Addendum 23 and SEBI):
- index and sector moves are reported as market commentary; a company is named only with its news, never with a
  price or move of the last 30 days (SEBI's 30-day price-data rule for education);
- news is used only if it is an official NSE announcement, or found on a fixed list of official sites and
  established outlets with one official source or two different outlets among the pages actually retrieved;
- "what next" is only history from our own studies (Addenda 22-23), as an average, or "no reliable pattern" -
  never a forecast.

Data: NSE's allIndices (closing values, breadth, P/E), FII/DII provisional flows and the board-meeting calendar.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import urlparse

import pandas as pd
import requests
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("trader.reel.market")
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
                      "Safari/537.36", "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/"}
API = "https://www.nseindia.com/api/"

BROAD = {"NIFTY 50": "Nifty 50", "NIFTY BANK": "Nifty Bank", "NIFTY MIDCAP 150": "Nifty Midcap 150",
         "NIFTY SMALLCAP 250": "Nifty Smallcap 250"}
SECTORS = {"NIFTY IT": "IT", "NIFTY PHARMA": "Pharma", "NIFTY AUTO": "Auto", "NIFTY FMCG": "FMCG",
           "NIFTY METAL": "Metal", "NIFTY REALTY": "Realty", "NIFTY PSU BANK": "PSU Bank", "NIFTY ENERGY": "Energy",
           "NIFTY PRIVATE BANK": "Private Bank", "NIFTY MEDIA": "Media", "NIFTY OIL & GAS": "Oil & Gas",
           "NIFTY CONSUMER DURABLES": "Consumer Durables", "NIFTY HEALTHCARE INDEX": "Healthcare"}
STUDIED = {"NIFTY 50": "Nifty 50", "NIFTY BANK": "Nifty Bank", "NIFTY IT": "Nifty IT", "NIFTY PHARMA": "Nifty Pharma",
           "NIFTY AUTO": "Nifty Auto", "NIFTY FMCG": "Nifty FMCG", "NIFTY METAL": "Nifty Metal",
           "NIFTY REALTY": "Nifty Realty", "NIFTY PSU BANK": "Nifty PSU Bank", "NIFTY ENERGY": "Nifty Energy",
           "NIFTY MIDCAP 150": "Nifty Midcap 150", "NIFTY SMALLCAP 250": "Nifty Smallcap 250"}

# trusted sources (Addendum 23): official sites, then established business news outlets
OFFICIAL = ["rbi.org.in", "sebi.gov.in", "pib.gov.in", "nseindia.com", "bseindia.com", "finmin.nic.in", "mospi.gov.in",
            "dol.gov", "uscis.gov", "state.gov", "whitehouse.gov", "federalregister.gov", "federalreserve.gov",
            "treasury.gov", "ustr.gov", "commerce.gov", "bls.gov"]
OUTLETS = ["reuters.com", "business-standard.com", "livemint.com", "economictimes.indiatimes.com",
           "thehindubusinessline.com", "financialexpress.com", "moneycontrol.com", "cnbctv18.com", "ndtvprofit.com",
           "bloomberg.com", "apnews.com", "thehindu.com", "indianexpress.com", "timesofindia.indiatimes.com"]
MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------- NSE data
def nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEAD)
    try:
        s.get("https://www.nseindia.com/", timeout=20)                    # cookies for the API
    except requests.RequestException:
        pass
    return s


def _get(s, path: str):
    for _ in range(3):
        try:
            r = s.get(API + path, timeout=30)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
    return None


def _num(x) -> Optional[float]:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def snapshot(get) -> Optional[dict]:
    """Today's closing picture from NSE: {date, indices: {NAME: {...}}, fii, dii}. None if NSE did not answer."""
    j = get("allIndices")
    if not j or "data" not in j:
        return None
    ts = datetime.strptime(j["timestamp"][:11], "%d-%b-%Y").date()
    idx = {}
    for d in j["data"]:
        idx[d["index"]] = {"close": _num(d.get("last")), "pct": _num(d.get("percentChange")),
                           "adv": _num(d.get("advances")), "dec": _num(d.get("declines")), "pe": _num(d.get("pe")),
                           "dy": _num(d.get("dy")), "pct30": _num(d.get("perChange30d")),
                           "pct365": _num(d.get("perChange365d")), "high52": _num(d.get("yearHigh")),
                           "low52": _num(d.get("yearLow"))}
    out = {"date": ts, "indices": idx, "fii": None, "dii": None}
    for f in get("fiidiiTradeReact") or []:
        try:
            if datetime.strptime(f["date"], "%d-%b-%Y").date() == ts:
                out["fii" if "FII" in f["category"] else "dii"] = _num(f["netValue"])
        except (KeyError, ValueError):
            continue
    return out


def next_session(today: date, holidays) -> date:
    from trader.holidays import is_session
    d = today + timedelta(days=1)
    while not is_session(d, holidays):
        d += timedelta(days=1)
    return d


def calendar(get, day: date, store: Path, n: int = 4) -> List[str]:
    """What is due on the next session: results of well-known companies (board meetings), and the weekly expiry."""
    out = []
    try:
        size = _traded_value(store)
        ev = [e for e in (get("event-calendar") or [])
              if datetime.strptime(e["date"], "%d-%b-%Y").date() == day and "result" in e.get("purpose", "").lower()]
        ev.sort(key=lambda e: -size.get(e["symbol"], 0.0))
        names = [e["company"].replace(" Limited", "").replace(" Ltd", "") for e in ev if size.get(e["symbol"], 0) >= 50][:n]
        if names:
            out.append("Quarterly results due: " + ", ".join(names))
    except (KeyError, ValueError, TypeError):
        pass
    if day.weekday() == 1:
        out.append("Nifty weekly options expiry (Tuesday)")
    return out


def _traded_value(store: Path) -> Dict[str, float]:
    from .content import _traded_value as tv
    return tv(store)


# ---------------------------------------------------------------- history (Addenda 22-23)
def history_line(name: str, move: float) -> Optional[str]:
    """What the index did after days like today, from research/market23_results.csv - only a consistent cell is
    quoted as a pattern; otherwise 'no reliable pattern'."""
    p = ROOT / "research" / "market23_results.csv"
    study = STUDIED.get(name)
    if not p.exists() or study is None or move is None:
        return None
    import sys
    sys.path.insert(0, str(ROOT / "research"))
    from market23 import bucket                                          # the study's own buckets
    r = pd.read_csv(p)
    b = bucket(move)
    r = r[(r["index"] == study) & (r["bucket"] == b)]
    hit = r[r["consistent"].astype(bool)].sort_values("horizon")
    if hit.empty:
        days = int(r["days"].max()) if len(r) else 0
        return (f"{study} moved {b} today. In {days} such days since 2012, what came next showed no reliable "
                "pattern (Addendum 23).")
    x = hit.iloc[0]
    nxt = "next day" if int(x["horizon"]) == 1 else "next 5 sessions"
    return (f"{study} moved {b} today. In {int(x['days'])} such days since 2012, over the {nxt} it rose "
            f"{x['rose_pct']:.0f}% of the time, against {x['rose_all_pct']:.0f}% on an ordinary day - an average, "
            "not a prediction (Addendum 23).")


# ---------------------------------------------------------------- trusted news (web search, rule in code)
class NewsItem(BaseModel):
    headline: str = Field(description="One plain-English line")
    facts: str = Field(description="What happened: 2-3 sentences, only facts stated in the sources, with dates")
    why_it_matters: str = Field(description="Which part of the Indian market it concerns and why, as general facts "
                                            "(no forecast, no stock call)")
    sector: Literal["IT", "Bank", "Pharma", "Auto", "FMCG", "Metal", "Realty", "Energy", "Oil & Gas", "Whole market",
                    "Other"]
    companies: List[str] = Field(description="Indian listed companies named in the news, if any")
    source_urls: List[str] = Field(description="URLs of the pages these facts come from (from the search results)")


class Driver(BaseModel):
    text: str = Field(description="Why an index or sector moved today, as reported - index/sector level, no company "
                                  "named with a price or move, no forecast")
    sector: Literal["IT", "Bank", "Pharma", "Auto", "FMCG", "Metal", "Realty", "Energy", "Oil & Gas", "Whole market",
                    "Other"]
    source_urls: List[str]


class Cue(BaseModel):
    what: str = Field(description="e.g. 'US S&P 500 (last close)', 'Brent crude', 'Rupee vs US dollar', 'Asian markets'")
    value: str = Field(description="Level and/or change exactly as the source states it, with its date or time")
    source_urls: List[str]


class NewsList(BaseModel):
    items: List[NewsItem]
    drivers: List[Driver] = Field(default_factory=list)
    cues: List[Cue] = Field(default_factory=list)


SEARCH_PROMPT = """Today is {day} (India). Today's closing figures from NSE:
{context}

Research three things, using only the search results:
1. The 3-5 most important NEWS EVENTS of the last 24 hours for people who invest in Indian shares: government or regulator decisions (RBI, SEBI, Indian government, tax, budget), global events that concern Indian sectors (US Federal Reserve, US policy on visas, tariffs or trade, crude oil), and big corporate events of large Indian listed companies.
2. WHY the market and its biggest-moving sectors moved today, as reported by the outlets (the reasons they give).
3. GLOBAL CUES: the last close of the main US indices, Asian markets today, Brent crude, and the rupee against the US dollar - with their dates.

For each point give the facts with their dates and the URLs of the pages you used. Prefer official sources (government, regulator, exchange) where they exist. Skip rumours, opinions, stock tips, price targets and predictions."""

STRUCTURE_PROMPT = """Turn these research notes into news items, drivers (why the market and sectors moved today, as reported) and global cues. Keep only things dated within the last 24 hours of {day}. Use only facts and URLs that appear in the notes; do not add anything. No forecasts, no price targets, no stock calls; in drivers, never name a company together with its share price or move. If something has no URL in the notes, leave it out.

NOTES:
{notes}"""


def _domain(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _matches(host: str, domains: List[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def trusted(items: List[NewsItem], retrieved: set) -> List[dict]:
    """The Addendum 23 rule, in code: a source counts only if the search actually retrieved it and it is on the
    list; an item stays if it has one official source or two different outlets."""
    out = []
    for it in items:
        urls = [u for u in it.source_urls if u in retrieved]
        hosts = {_domain(u) for u in urls}
        official = [u for u in urls if _matches(_domain(u), OFFICIAL)]
        outlets = {h for h in hosts if _matches(h, OUTLETS)}
        if official or len(outlets) >= 2:
            d = it.model_dump()
            d["source_urls"] = urls
            d["official"] = bool(official)
            out.append(d)
    return out


def _sourced(x, retrieved: set) -> List[str]:
    return [u for u in x.source_urls if u in retrieved and _matches(_domain(u), OFFICIAL + OUTLETS)]


def safe_text(text: str, companies: List[str]) -> bool:
    """SEBI rules on a sentence: no prediction, instruction or promise; no named company with a % or a price."""
    from .script import COMMAND, PCT, PREDICT, PRICE, PROMISE
    if any(rx.search(text) for rx in (PREDICT, COMMAND, PROMISE)) or re.search(
            r"\b(will|may|could|likely to|expected to) (rise|fall|gain|drop|rally|recover|decline)", text, re.I):
        return False
    named = any(c and c.lower() in text.lower() for c in companies)
    return not (named and (PCT.search(text) or PRICE.search(text)))


def web_news(day: date, client=None, max_searches: int = 10, context: str = "") -> dict:
    """{items, drivers, cues, note}. Claude searches only the trusted sites; the trust rule (items: one official
    source or two outlets; drivers and cues: one trusted page) and the SEBI sentence check are applied in code."""
    import os

    import anthropic
    empty = {"items": [], "drivers": [], "cues": []}
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return {**empty, "note": "no ANTHROPIC_API_KEY"}
    client = client or anthropic.Anthropic()
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches,
            "allowed_domains": OFFICIAL + OUTLETS, "user_location": {"type": "approximate", "country": "IN"}}
    messages = [{"role": "user", "content": SEARCH_PROMPT.format(day=day.strftime("%A %d %B %Y"),
                                                                 context=context or "(not available)")}]
    retrieved, notes = set(), []
    try:
        for _ in range(4):                                                 # resume if the server pauses a long turn
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=16000, messages=messages, tools=[tool], output_config={"effort": "medium"},
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
            return {**empty, "note": "no news found"}
        parsed = client.beta.messages.parse(
            model=MODEL, max_tokens=16000, output_format=NewsList, output_config={"effort": "low"},
            messages=[{"role": "user", "content": STRUCTURE_PROMPT.format(day=day.isoformat(),
                                                                          notes="\n".join(notes)[:60000])}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        out = parsed.parsed_output or NewsList(items=[])
    except anthropic.APIError as e:
        log.warning("news search failed: %s", e)
        return {**empty, "note": f"news search failed ({type(e).__name__})"}
    kept = [k for k in trusted(out.items, retrieved)
            if safe_text(f"{k['headline']} {k['facts']} {k['why_it_matters']}", [])][:3]
    names = sorted({c for it in out.items for c in it.companies})
    drivers = [{"text": d.text, "sector": d.sector, "source_urls": _sourced(d, retrieved)} for d in out.drivers
               if _sourced(d, retrieved) and safe_text(d.text, names)][:4]
    cues = [{"what": c.what, "value": c.value, "source_urls": _sourced(c, retrieved)} for c in out.cues
            if _sourced(c, retrieved) and safe_text(f"{c.what} {c.value}", [])][:5]
    note = (f"{len(kept)} of {len(out.items)} news items, {len(drivers)} of {len(out.drivers)} reasons and "
            f"{len(cues)} of {len(out.cues)} global cues passed the trust and SEBI checks")
    return {"items": kept, "drivers": drivers, "cues": cues, "note": note}


# ---------------------------------------------------------------- the day
def compile_day(today: date, store: Path, holidays=(), client=None, get=None, search: bool = True) -> Optional[dict]:
    """Everything the brief and the Reel use, or None when today was not a session or NSE's data is not out yet."""
    from trader.holidays import is_session
    if not is_session(today, holidays):
        return None
    if get is None:
        s = nse_session()
        get = lambda path: _get(s, path)                                  # noqa: E731
    snap = snapshot(get)
    if snap is None or snap["date"] != today:
        return None
    ix = snap["indices"]
    broad = {BROAD[k]: ix[k] for k in BROAD if k in ix and ix[k]["pct"] is not None}
    sectors = sorted(((SECTORS[k], ix[k]["pct"], k) for k in SECTORS if k in ix and ix[k]["pct"] is not None),
                     key=lambda x: x[1])
    n500 = ix.get("NIFTY 500", {})
    vix = ix.get("INDIA VIX", {})
    nifty = ix.get("NIFTY 50", {})
    from . import content
    filing = content.news_item(today, store)
    context = "; ".join(f"{BROAD.get(k, SECTORS.get(k, k))} {ix[k]['pct']:+.2f}%" for k in list(BROAD) + list(SECTORS)
                        if k in ix and ix[k]["pct"] is not None)
    web = web_news(today, client, context=context) if search else {"items": [], "drivers": [], "cues": [],
                                                                   "note": "search off"}
    news, note = web["items"], web["note"]
    # history: Nifty 50, plus the sector each news item concerns, plus the day's biggest sector move
    hist = [history_line("NIFTY 50", nifty.get("pct"))]
    picks = {k for (_, _, k) in (sectors[:1] + sectors[-1:])}
    for it in news:
        k = next((k for k, v in SECTORS.items() if v == it["sector"]), None)
        if k:
            picks.add(k)
    hist += [history_line(k, ix[k]["pct"]) for k in sorted(picks) if k in ix]
    day = {"date": today.isoformat(), "broad": broad, "sectors": [(n, p) for n, p, _ in sectors],
           "breadth": (n500.get("adv"), n500.get("dec")), "vix": (vix.get("close"), vix.get("pct")),
           "fii": snap["fii"], "dii": snap["dii"], "pe": nifty.get("pe"), "dy": nifty.get("dy"),
           "nifty_30d": nifty.get("pct30"), "nifty_1y": nifty.get("pct365"),
           "news": news, "news_note": note, "drivers": web["drivers"], "cues": web["cues"], "filing": filing, "history": [h for h in hist if h],
           "next_session": next_session(today, holidays).isoformat(),
           "calendar": calendar(get, next_session(today, holidays), store)}
    for it in news:                                                        # the sector's own move today, as context
        k = next((k for k, v in SECTORS.items() if v == it["sector"]), None)
        it["sector_today"] = ix[k]["pct"] if k in ix else None
    return day


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}%"


def _cr(x) -> str:
    if x is None:
        return "not published yet"
    return f"{'bought' if x >= 0 else 'sold'} ₹{abs(x):,.0f} crore net"


def brief(day: dict) -> str:
    """The owner's daily market brief (Telegram): plain words, every news item with its sources."""
    d = datetime.fromisoformat(day["date"]).strftime("%a %d %b %Y")
    b = day["broad"]
    lines = [f"📊 DAILY MARKET BRIEF - {d}", "(information and education only - not advice)", "",
             "SCOREBOARD (closing)"]
    for name, v in b.items():
        lines.append(f"• {name}: {v['close']:,.2f} ({_pct(v['pct'])})")
    adv, dec = day["breadth"]
    if adv is not None:
        lines.append(f"• Breadth (Nifty 500): {int(adv)} shares up, {int(dec)} down")
    vix, vixp = day["vix"]
    if vix is not None:
        lines.append(f"• India VIX (expected swings - higher = more nervous): {vix:.2f} ({_pct(vixp)})")
    lines.append(f"• Foreign investors (FII): {_cr(day['fii'])}; Indian funds (DII): {_cr(day['dii'])} (provisional)")
    if day.get("pe"):
        lines.append(f"• Nifty 50 valuation: P/E {day['pe']:.1f}, dividend yield {day['dy']:.2f}% | "
                     f"last 30 days {_pct(day['nifty_30d'])}, last year {_pct(day['nifty_1y'])}")
    s = day["sectors"]
    if s:
        lines += ["", "SECTORS", "• Best: " + ", ".join(f"{n} {_pct(p)}" for n, p in s[::-1][:3]),
                  "• Worst: " + ", ".join(f"{n} {_pct(p)}" for n, p in s[:3])]
    if day.get("drivers"):
        lines += ["", "WHY IT MOVED (as reported)"]
        for d in day["drivers"]:
            lines.append(f"• {d['text']} ({', '.join(sorted({_domain(u) for u in d['source_urls']}))})")
    if day.get("cues"):
        lines += ["", "GLOBAL CUES"]
        for c in day["cues"]:
            lines.append(f"• {c['what']}: {c['value']} ({_domain(c['source_urls'][0])})")
    lines += ["", "NEWS (trusted sources only)"]
    if not day["news"] and not day["filing"]:
        lines.append(f"• Nothing passed the trust rule today ({day['news_note']}).")
    for it in day["news"]:
        tag = "official source" if it["official"] else "2+ outlets"
        sect = f" | {it['sector']} index today {_pct(it.get('sector_today'))}" if it.get("sector_today") is not None else ""
        lines += [f"• {it['headline']} [{tag}]", f"  {it['facts']}", f"  Why it matters: {it['why_it_matters']}{sect}"]
        lines += [f"  {u}" for u in it["source_urls"][:3]]
    f = day.get("filing")
    if f:
        lines += [f"• NSE filing - {f['symbol']}: {f['subject']} ({f['type']})"]
        if f.get("history"):
            lines.append(f"  History for this type: {f['history']}")
    if day["history"]:
        lines += ["", "WHAT HISTORY SAYS (our own studies; never a forecast)"] + [f"• {h}" for h in day["history"]]
    if day["calendar"]:
        nd = datetime.fromisoformat(day["next_session"]).strftime("%a %d %b")
        lines += ["", f"NEXT SESSION ({nd})"] + [f"• {c}" for c in day["calendar"]]
    return "\n".join(lines)


def parts(text: str, limit: int = 3800) -> List[str]:
    """The brief in Telegram-sized messages, split between sections."""
    out, cur = [], ""
    for sec in text.split("\n\n"):
        if cur and len(cur) + len(sec) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{sec}" if cur else sec[:limit]
    return out + ([cur] if cur else [])


def save(day: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"market_{day['date'].replace('-', '')}.json"
    p.write_text(json.dumps(day, ensure_ascii=False, indent=1))
    return p


def load_saved(today: date, out_dir: Path) -> Optional[dict]:
    p = out_dir / f"market_{today:%Y%m%d}.json"
    return json.loads(p.read_text()) if p.exists() else None


