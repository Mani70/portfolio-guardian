"""What a Reel talks about: one money lesson, one research fact from our own tests, and (when there is one) one piece
of official NSE news explained - with how shares reacted to that TYPE of news in the past, never a call on the share."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

# plain lessons: (title, the point in plain words) - the script turns them into Hinglish
LESSONS = [
    ("Index fund kya hota hai", "An index fund buys all the companies of an index, like the Nifty 50, so you own a small "
     "piece of India's 50 biggest companies in one go, at a very low yearly fee."),
    ("Win rate ka dhokha", "A strategy that wins 8 times out of 10 can still lose money, if the 2 losses are big. What "
     "matters is the average profit after costs, not how often you win."),
    ("Trading cost ka asar", "Every buy and sell costs brokerage, STT, exchange fees, stamp duty and slippage. In our "
     "tests a delivery round trip cost about 0.44% - a small number that eats most short-term profits."),
    ("P/E ratio simple bhasha mein", "P/E means price divided by yearly profit per share. A P/E of 20 means you pay 20 "
     "years of today's profit for the share. Lower is cheaper, but cheap can stay cheap."),
    ("Dividend yield", "Dividend yield is the yearly dividend as a percentage of the share price. When the whole market's "
     "dividend yield is very low, shares are usually expensive."),
    ("Rebalancing", "Rebalancing means bringing a mix of funds back to its target share - selling a little of what grew "
     "too big and buying what fell behind. It quietly sells high and buys low."),
    ("200-day average", "The 200-day average is the average closing price of the last 200 trading days, about 10 "
     "months. Price above it is often called an uptrend, below it a downtrend."),
    ("Stop-loss kya hai", "A stop-loss is a price decided in advance at which you sell to keep a loss small. It does not "
     "make a strategy profitable; it limits damage when you are wrong."),
    ("Diversification", "Diversification means not putting all your money in one thing. Indian shares, US shares, gold "
     "and cash rarely fall together, so a mix falls less than any single part."),
    ("Compounding", "Compounding means returns earning returns. 12% a year doubles money in about 6 years and makes it "
     "about 10 times in 20 years - if you stay invested."),
    ("Survivorship bias", "Testing a strategy only on today's big companies hides all the companies that failed and "
     "disappeared. Results look better than reality. Our tests include failed and delisted companies."),
    ("Backtest ka jaal (overfitting)", "Try enough rules on old data and one will look brilliant by luck. A real test "
     "fixes the rules first, chooses on old years and checks once on years it has never seen."),
    ("Options basics", "An option is a contract on a price. A put pays if the price falls below a level by a date; a call "
     "pays if it rises above. The buyer pays a fee called the premium; the seller collects it and takes the risk."),
    ("F&O ka sach", "SEBI's 2024 study found 93% of individual F&O traders lost money over three years. Leverage makes "
     "small moves into big gains or big losses."),
    ("Liquidity", "Liquidity means how easily a share can be bought or sold without moving its price. Shares that trade "
     "only a few lakh rupees a day can be hard to exit."),
    ("Delivery vs intraday", "Delivery means you keep the shares overnight in your demat account. Intraday means you buy "
     "and sell the same day. Intraday costs less per trade but most intraday traders lose money."),
    ("Midcap kya hai", "Midcaps are medium-sized companies, ranked about 101 to 250 by size. They have grown faster than "
     "the biggest companies over 20 years, but fell harder in crashes - about 73% in 2008."),
    ("Gold portfolio mein kyun", "Gold often rises when shares fall or the rupee weakens. A small part in gold can make "
     "a portfolio's worst fall smaller."),
]

# facts from our own pre-registered research (research/FINDINGS.md) - numbers fixed, wording plain
RESEARCH = [
    "We tested 10+ intraday methods on 2.5 years of 5-minute NSE data. None made money after costs.",
    "A bull put spread on the Nifty won 4 months out of 5 over 14 years - and still lost money after charges.",
    "Over 14 years, buying Nifty options in the direction of the trend won only 1 month in 3.",
    "Monthly crash insurance (a put 5% below the Nifty) cost 1.1-1.7% a year and did not pay for itself.",
    "NSE's quality and value indices looked 2-11% a year better in back-calculated years, but none showed an edge after launch.",
    "A 7-pillar company scoring framework, tested blind on 1,272 companies from 2008, did not find multibaggers better than chance.",
    "The same framework rated Satyam #1 in 2009 on its falsified accounts - numbers in filings cannot reveal fraud.",
    "A rules-based mix of Indian shares, US shares, gold and cash beat the Nifty 50 with smaller falls over 20 years in our test.",
    "Rebalancing back to targets added 3-4% a year over never rebalancing in our 20-year test.",
    "Holding a bit less Nifty when the market's dividend yield was very low added about 0.9% a year in 2006-15, mostly around 2008.",
    "Stock tips from a price-only screen beat the Nifty in 2011-15 but not reliably in 2016-26 - no proven edge.",
    "Weak stocks near their 52-week low did exactly as well as the Nifty over the next month in our 15-year test.",
]


def pick(seq, today: date):
    return seq[today.toordinal() % len(seq)]


def news_item(today: date, store: Optional[Path] = None) -> Optional[dict]:
    """The most notable official NSE announcement of the last day: a classified type (buyback, rating action, auditor
    resignation...) from one of the most traded companies. With its type's past reaction when the study has one."""
    sys.path.insert(0, str(ROOT / "research"))
    from news22 import classify                                         # noqa: E402 - the study's own definitions
    store = store or ROOT / "cache" / "insights"
    p = store / "filings.csv"
    if not p.exists():
        return None
    f = pd.read_csv(p, dtype=str).fillna("")
    f = f[f["date"] >= (today - timedelta(days=1)).isoformat()]
    if f.empty:
        return None
    size = _traded_value(store)
    best = None
    for r in f.itertuples():
        types = classify(r.subject, r.text)
        if not types:
            continue
        v = size.get(r.symbol, 0.0)
        if best is None or v > best["value"]:
            best = {"symbol": r.symbol, "type": types[0], "subject": r.subject, "text": r.text[:300], "date": r.date,
                    "link": r.link, "value": v}
    if best is None or best["value"] < 10:                               # only well-known, heavily traded companies
        return None
    best["history"] = _reaction(best["type"])
    return best


def _traded_value(store: Path) -> Dict[str, float]:
    """Median traded value (₹ crore a day) per symbol from the ideas report's price store."""
    files = sorted(store.glob("px_*.csv"))[-20:]
    if not files:
        return {}
    px = pd.concat([pd.read_csv(x, usecols=["symbol", "value"]) for x in files])
    return (px.groupby("symbol")["value"].median() / 1e7).to_dict()


def _reaction(kind: str) -> Optional[str]:
    """How shares reacted to this type of news in the past (research/news22_results.csv), only if consistent."""
    p = ROOT / "research" / "news22_results.csv"
    if not p.exists():
        return None
    r = pd.read_csv(p)
    r = r[r["type"] == kind]
    if r.empty:
        return None
    x = r.iloc[0]
    if not bool(x.get("consistent", False)):
        return (f"Past {int(x['events'])} such announcements on NSE (2012-2026) showed no reliable pattern in the "
                "share price over the next week.")
    return (f"In {int(x['events'])} such announcements on NSE (2012-2026), shares moved {x['mean5']:+.1f}% versus the "
            f"Nifty over the next 5 trading days on average, and beat the Nifty {x['beat5']:.0f}% of the time - an "
            "average, not a prediction for any one company.")
