"""What a Reel talks about. Each Reel is ONE topic, in one of three series:

  MYTH vs SACH   a popular market belief, checked against this project's own pre-registered tests, with the concept
                 behind it explained (morning, every day)
  NEWS SAMJHO    one official NSE announcement from a heavily traded company, explained - with how shares reacted to
                 that TYPE of news in the past, never a call on the share (evening, when there is notable news)
  MARKET KI KAHANI  a settled episode of Indian market history (scams, crashes) told as a story, with its lesson
                 (evening, when there is no notable news)

Only "what did NOT work" findings are used: results where a rule beat the market are left out, so nothing can read as
a performance claim (SEBI, Jan 2025)."""
from __future__ import annotations

import re
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
    "In 14 years of NSE news, companies announcing a buyback did no better than the Nifty in the following week.",
    "Annual profit up 20%+ sounds great, but in 14 years of NSE data those shares did not beat the Nifty the next week - good news is often already expected.",
    "Even after an order win, the best-known 'good news', shares beat the Nifty only 54% of the time over the next week in our 14-year study.",
    "We tested 256 short-term trading rules, chose the best on 2012-19 and tested it once on 2020-26. None passed.",
]


LESSON = dict(LESSONS)

# (myth, what our own tests found, the lesson that explains it) - myths are worded as questions, never as instructions
MYTHS = [
    ("Intraday trading se roz ki pakki kamai?", RESEARCH[0], "Delivery vs intraday"),
    ("80% baar jeetne wali strategy = paisa hi paisa?", RESEARCH[1], "Win rate ka dhokha"),
    ("Trend ke saath options lo, paisa banega?", RESEARCH[2], "Options basics"),
    ("Har mahine crash insurance lena hamesha samajhdari?", RESEARCH[3], "Options basics"),
    ("Quality aur value index hamesha market se aage?", RESEARCH[4], "Backtest ka jaal (overfitting)"),
    ("Achhe numbers wali company = agla multibagger?", RESEARCH[5], "P/E ratio simple bhasha mein"),
    ("Company ke accounts padh ke fraud pakad loge?", RESEARCH[6], "Diversification"),
    ("Screener se nikle stocks market ko hara dete hain?", RESEARCH[10], "Backtest ka jaal (overfitting)"),
    ("52-week low wala share = sasta share?", RESEARCH[11], "P/E ratio simple bhasha mein"),
    ("Buyback ki news = pakka fayda?", RESEARCH[12], "Trading cost ka asar"),
    ("Profit 20% badha, toh share bhi badhega?", RESEARCH[13], "P/E ratio simple bhasha mein"),
    ("Bada order mila, toh share upar hi jayega?", RESEARCH[14], "Win rate ka dhokha"),
    ("Purane data par best rule = future ka best rule?", RESEARCH[15], "Backtest ka jaal (overfitting)"),
    ("F&O = jaldi ameer banne ka shortcut?", LESSON["F&O ka sach"], "Options basics"),
    ("Stop-loss lagaya, toh nuksaan khatam?", LESSON["Stop-loss kya hai"], "Stop-loss kya hai"),
    ("Midcap hamesha zyada return deta hai?", LESSON["Midcap kya hai"], "Diversification"),
    ("Aaj ki top companies dekh ke strategy test kar lo?", LESSON["Survivorship bias"], "Survivorship bias"),
    ("Kam fees se kya hi farak padta hai?", LESSON["Trading cost ka asar"], "Compounding"),
]

# PAISA KI PATHSHALA: a numbered course, beginner to advanced - (title, the point in plain words). Timeless facts
# only: no prices, no tax rates or limits that change with each budget (the lesson says "check the current rule").
PATHSHALA = [
    ("Share kya hota hai", "A share is a small piece of ownership of a company. If a company has 100 crore shares and "
     "you own 100, you own that tiny part of its business - its profits and its risks."),
    ("Stock exchange kya hai", "NSE and BSE are stock exchanges: regulated markets where buyers and sellers of shares "
     "meet through brokers. The exchange matches orders; it does not decide the price."),
    ("SEBI kaun hai", "SEBI (Securities and Exchange Board of India) is the market regulator. It makes the rules for "
     "companies, brokers, mutual funds and advisers, and acts against fraud."),
    ("Demat aur trading account", "A trading account places your buy and sell orders; a demat account holds your shares "
     "electronically, like a bank account for shares. Your shares should always be in your own demat account."),
    ("Share ka price kaise badalta hai", "A share's price moves with demand and supply: more buyers than sellers push it "
     "up, more sellers push it down. Expectations about the company's future profits drive both."),
    ("Market cap", "Market capitalisation is the share price times the number of shares - the value the market puts on "
     "the whole company today. Large, mid and small caps are companies sorted by this size."),
    ("Index: Nifty aur Sensex", "An index tracks a basket of shares. The Nifty 50 follows 50 large NSE companies, the "
     "Sensex 30 large BSE companies. When people say 'market upar gaya', they usually mean the index."),
    ("IPO kya hota hai", "An IPO (initial public offering) is when a company sells shares to the public for the first "
     "time and lists on an exchange. A hyped IPO is not automatically a good business."),
    ("Mutual fund kya hai", "A mutual fund pools money from many investors and a fund manager invests it in shares, "
     "bonds or both. You own units of the fund; its value per unit is the NAV."),
    ("SIP kya hai", "A SIP (systematic investment plan) invests a fixed amount every month. You buy more units when "
     "prices are low and fewer when high, and the habit matters more than timing."),
    ("Index fund vs active fund", "An index fund simply copies an index at a low fee; an active fund pays a manager to "
     "try to beat it at a higher fee. Over long periods, many active funds do not beat their index after fees."),
    ("Expense ratio", "The expense ratio is the yearly fee a fund charges, as a percentage of your money. A difference "
     "of 1% a year sounds small but compounds into a big gap over 20 years."),
    ("ETF kya hai", "An ETF (exchange traded fund) is a fund that trades on the exchange like a share - for example a "
     "Nifty 50 ETF or a gold ETF. You need a demat account to buy one."),
    ("Compounding ka jaadu", "Compounding means returns earning returns. Money growing 12% a year doubles in about 6 "
     "years - the 'rule of 72': 72 divided by the yearly return gives the years to double."),
    ("Inflation", "Inflation is prices rising over time, so the same rupee buys less. Money kept idle loses value; "
     "investing aims to grow faster than inflation."),
    ("Emergency fund pehle", "Before investing in shares, keep 6 months of expenses in a safe, easy-to-withdraw place. "
     "Then a job loss or illness never forces you to sell shares at a bad time."),
    ("Risk aur return", "Higher possible return comes with higher risk of loss. Anyone promising high returns with no "
     "risk is either mistaken or running a scam."),
    ("Asset allocation", "Asset allocation is how you split money between shares, bonds, gold and cash. It decides most "
     "of your ups and downs - more than which single share you pick."),
    ("Diversification", "Diversification means not putting everything in one share or sector. If one company fails, "
     "the rest of your money is still working."),
    ("Bull market, bear market", "A bull market is a long rise, a bear market a long fall (often 20% or more from the "
     "top). Both have come and gone many times; nobody rings a bell at the turn."),
    ("Volatility", "Volatility is how much prices swing. Shares can fall 30-50% in a bad year; investing money you need "
     "soon in shares turns normal swings into real losses."),
    ("Dividend", "A dividend is a share of profit a company pays to its shareholders. On the dividend date the share "
     "price usually falls by about that amount - it is not free money."),
    ("Bonus aur split", "In a bonus or a split you get more shares, but each is worth proportionally less. Your total "
     "value does not change on that day."),
    ("Buyback", "In a buyback a company buys back its own shares, usually at a fixed price. Fewer shares remain, so each "
     "owns a slightly bigger part of the company."),
    ("Revenue vs profit", "Revenue is all the money a company earns from sales; profit is what is left after every cost, "
     "interest and tax. A company can grow revenue and still make a loss."),
    ("EPS aur P/E", "EPS (earnings per share) is yearly profit divided by the number of shares. P/E is the share price "
     "divided by EPS - how many years of today's profit you pay for."),
    ("Balance sheet basics", "A balance sheet lists what a company owns (assets) and owes (liabilities); the difference "
     "is the owners' share (equity). Too much debt is a common reason companies fail."),
    ("Cash flow", "Profit is an accounting number; cash flow is real money coming in and going out. A company that "
     "reports profits but never generates cash deserves a closer look."),
    ("Quarterly results kaise padhein", "Look at revenue and profit against the same quarter last year, the margin "
     "(profit per rupee of sales), debt, and what management said. One quarter is never the whole story."),
    ("Futures kya hai", "A future is a contract to buy or sell at a fixed price on a future date. It needs only a margin "
     "upfront, so gains and losses are many times larger than the money put in."),
    ("Options kya hai", "An option gives the buyer a right, not an obligation, to buy (call) or sell (put) at a set "
     "price by a date. The buyer pays a premium; most options expire worthless."),
    ("F&O ka sach", "SEBI's study of FY22-FY24 found 93% of individual F&O traders lost money. Leverage makes small "
     "moves into big losses."),
    ("Trading ke kharche", "Every trade pays brokerage, STT, exchange fees, GST and stamp duty, plus slippage. Frequent "
     "trading pays these again and again - costs quietly eat returns."),
    ("Tax on gains (basics)", "Profits from shares are taxed as short-term or long-term capital gains depending on how "
     "long you held them. Rates and limits change with budgets - always check the current rule."),
    ("T+1 settlement", "In India, shares you buy reach your demat account one working day after the trade (T+1), and "
     "money from a sale arrives the next working day."),
    ("Circuit limit", "Exchanges set daily price bands (circuits) on many shares. When a share hits its limit, trading "
     "is restricted - a sign of extreme demand or panic, not of value."),
    ("Margin aur pledge", "Margin trading means borrowing to buy more than your money allows. Losses grow just as fast; "
     "a broker can sell your pledged shares if the value falls."),
    ("Nominee kyun zaroori", "Adding a nominee to your demat account, bank and funds makes sure your family can claim "
     "the money easily. It takes a few minutes online."),
    ("Registered adviser kaise check karein", "SEBI-registered investment advisers and research analysts are listed on "
     "SEBI's website with registration numbers. Unregistered 'tips' sellers have no such accountability."),
    ("Scam kaise pehchaane", "Red flags: guaranteed returns, pressure to act fast, Telegram/WhatsApp 'sure-shot' tips, "
     "requests to pay into personal accounts. Complaints can go to SEBI's SCORES portal."),
]

# settled history only: court convictions, regulator orders, official data. Numbers here are the only numbers used.
STORIES = [
    ("Harshad Mehta, 1992", "The Sensex rose from about 1,200 in mid-1991 to about 4,500 in April 1992 - almost 4 times. Harshad Mehta was "
     "found to have pulled bank money into shares through bank receipts (BRs) that were not backed by real securities. "
     "Journalist Sucheta Dalal exposed it in April 1992; the market crashed and the scam was estimated at about "
     "₹4,000-5,000 crore. Mehta was convicted in some of the cases and died in 2001 while others were still on.", "A rally built on borrowed or illegal money "
     "ends badly; rules and regulators got stricter after it."),
    ("Satyam, January 2009", "On 7 January 2009 Satyam's chairman Ramalinga Raju wrote that the company's cash and "
     "bank balance of about ₹5,000 crore did not exist. The share fell about 78% that day. Raju and others were "
     "convicted in 2015. In our own test, a 7-pillar company scoring framework rated Satyam #1 in 2009 - on the "
     "falsified numbers.", "Numbers in filings cannot reveal fraud; diversification limits the damage."),
    ("Ketan Parekh, 2000-01", "In 2000-01 a group of stocks called the 'K-10' rose many times over. Ketan Parekh was "
     "found to have used circular trading and money from a co-operative bank to push prices. When it broke in 2001 "
     "the stocks crashed and the bank failed. SEBI later barred him from the market.", "A share that only goes up "
     "with no business reason is a warning sign, not an opportunity."),
    ("2008 ka crash", "In January 2008 the Sensex was near 21,000. By October 2008 it was below 8,000 - a fall of "
     "more than 60% in ten months, during the global financial crisis. Midcaps fell about 73%. The Sensex was back "
     "above 20,000 by late 2010.", "Crashes happen; a mix of assets and patience matter more than timing."),
    ("COVID crash, March 2020", "The Nifty 50 fell from about 12,400 in January 2020 to about 7,600 on 23 March 2020, "
     "a fall of almost 40% in two months. By the end of 2020 it was at a new high above 13,000.", "Panic selling at "
     "the bottom locks in losses; nobody can time the bottom."),
    ("NSEL, 2013", "In July 2013 the National Spot Exchange (NSEL) stopped paying. About ₹5,600 crore of investors' "
     "money was stuck. The 'paired contracts' traded there had been sold as safe, fixed-return products.", "A "
     "'fixed return' in a market product is a red flag; check who regulates it."),
    ("Karvy, 2019", "In November 2019 SEBI found that Karvy Stock Broking had pledged its clients' shares, worth more "
     "than ₹2,000 crore, to raise loans for itself. After this, the rules changed so that a broker can no longer "
     "pledge clients' shares without the client's own approval.", "Check your demat statement; your shares should be "
     "in your name."),
    ("IL&FS, 2018", "In September 2018 IL&FS, a big lender rated AAA until shortly before, defaulted. Its group debt "
     "was over ₹90,000 crore. Some debt mutual funds that held its bonds saw their value fall.", "A top credit "
     "rating is an opinion, not a guarantee; even 'safe' debt can default."),
    ("Franklin Templeton, April 2020", "In April 2020 Franklin Templeton closed six of its debt mutual fund schemes, "
     "holding about ₹25,000 crore, because it could not sell their bonds fast enough. Investors waited months to "
     "years for their money, paid back in parts.", "Higher yield usually means higher risk, also in debt funds."),
    ("Yes Bank, March 2020", "In March 2020 the RBI took control of Yes Bank and limited withdrawals to ₹50,000 per "
     "account for about two weeks. About ₹8,400 crore of its AT1 bonds were written down to zero.", "Complex "
     "high-interest products can lose everything; know what you own."),
    ("SEBI ka F&O study, 2024", "SEBI studied individual F&O traders for three years, FY22 to FY24: 93% of them lost "
     "money, with total losses of about ₹1.8 lakh crore. Only about 1% made more than ₹1 lakh a year after costs.",
     "Leverage magnifies mistakes; most people lose."),
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
    size, known = _traded_value(store), _consistent_types()
    best = None
    for r in f.itertuples():
        if ROUTINE.search(f"{r.subject} {r.text}"):                     # e.g. shares allotted to employees (ESOPs)
            continue
        types = classify(r.subject, r.text)
        if not types:
            continue
        v = size.get(r.symbol, 0.0)
        rank = (types[0] in known, v)                                   # a type with a real track record first
        if v >= 10 and (best is None or rank > best["rank"]):           # only well-known, heavily traded companies
            best = {"symbol": r.symbol, "type": types[0], "subject": r.subject, "text": r.text[:300], "date": r.date,
                    "link": r.link, "value": v, "rank": rank}
    if best is None:
        return None
    best.pop("rank")
    best["history"] = _reaction(best["type"])
    return best


ROUTINE = re.compile(r"\b(esops?|esos|esps|employee stock|stock options? (?:scheme|plan)|employees? stock)\b", re.I)


def _consistent_types() -> set:
    p = ROOT / "research" / "news22_results.csv"
    if not p.exists():
        return set()
    r = pd.read_csv(p)
    return set(r.loc[r["consistent"].astype(bool), "type"])


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
