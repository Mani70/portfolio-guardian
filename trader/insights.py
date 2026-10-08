"""Daily swing insights - INFORMATION ONLY: the bot never trades these (the autopilot trades the rulebook alone).

  python -m trader.run insights            # after ~19:00 IST on a trading day (scheduled: deploy/oci/crontab)
  python -m trader.run insights --dry-run  # print instead of Telegram

Every evening it screens every NSE stock (EQ series) on the day's official data and sends, with the reasons:
  BUY ideas  - up to 5 liquid stocks in a confirmed uptrend with top momentum, near their 52-week high, not
               over-extended, in a market whose own trend is up, with no red-flag filing;
  AVOID/EXIT - up to 5 liquid stocks in a confirmed downtrend with the weakest momentum (and any of YOUR holdings
               that show the same weakness);
  or, when nothing qualifies, how many stocks each rule removed, so "no ideas today" is explained.
Each idea lists the facts behind it, the official filings of the last 30 days, the risks and the level that would
invalidate it. Past ideas are recorded and their later returns against the Nifty ETF are reported (track record).

These rules are a professional-style screen, NOT a tested trading signal (research/FINDINGS.md: the monthly stock
momentum rule failed its survivorship-free test). Data: NSE bhavcopy (prices, volume, value), NSE full bhavcopy
(delivery %), NSE PR files (splits/bonuses), NSE corporate filings feeds; nothing from social media.
"""
from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger("trader.insights")

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "cache" / "insights"
SESSIONS = 400                      # rolling window kept on disk (a 52-week high needs 252, the 200-day average 200)

# the screen's rules - fixed here, shown in every report's footer (not tuned to recent results)
RULES = {
    "min_value_cr": 10.0,           # median daily traded value over 20 sessions, Rs crore (liquid enough to trade)
    "min_price": 50.0,
    "min_history": 260,             # sessions of history
    "buy_momentum_pct": 80.0,       # momentum percentile among the liquid stocks
    "buy_near_high": 0.90,          # close >= 90% of the 52-week high
    "buy_max_extension": 0.20,      # close <= 20% above the 50-day average (not over-extended)
    "buy_max_atr_pct": 5.0,         # average true range <= 5% of the price
    "buy_max_day_move": 8.0,        # today's move <= +8% (no chasing a spike)
    "avoid_momentum_pct": 20.0,
    "avoid_near_low": 1.10,         # close <= 110% of the 52-week low
    "top_n": 5,
}
# research/FINDINGS.md Addendum 15: the rules above, unchanged, on every session of 2011-2026 (survivorship-free)
TESTED = ("20-year test (2011-2026): BUY ideas beat the Nifty 50 by +1.9% per 20 sessions after costs in 2011-15 "
          "but only +0.75% in 2016-26, not statistically reliable - no proven edge. AVOID ideas did the same as the "
          "Nifty 50 (no proven warning value).")

ARCH = "https://nsearchives.nseindia.com"
FEED_ANN = f"{ARCH}/content/RSS/Online_announcements.xml"          # every filing of the day (~1,000)
FEED_BM = f"{ARCH}/content/RSS/Board_Meetings.xml"                 # board meetings announced, with their date
EQUITY_LIST = f"{ARCH}/content/equities/EQUITY_L.csv"              # NSE symbols and company names
NOISE = ("declaration of nav", "certificate under sebi (depositories", "newspaper publication", "isin for debt",
         "structural digital database", "compliance report", "record date updates", "redemption/payment of interest",
         "portfolio", "integrated filing", "trading window")
RED = (("insolvency", "insolvency proceedings"), ("actions initiated/taken or orders passed", "regulatory action/order"),
       ("default", "default disclosed"), ("fraud", "fraud reported"), ("forensic", "forensic audit"),
       ("suspension", "suspension"), ("show cause", "show-cause notice"), ("downgrad", "credit rating downgrade"))
# these remove a stock from BUY ideas for 30 days; the others are shown as cautions to read (a regulator's order can
# be a small tax demand on a large company)
BLOCKING = {"insolvency proceedings", "auditor resigned", "default disclosed", "fraud reported", "forensic audit",
            "suspension"}


# ------------------------------------------------------------------ data
def _research():
    sys.path.insert(0, str(ROOT / "research"))
    import download_history as DH                                   # noqa: E402 - proven NSE parsers
    import download_nse as DN                                       # noqa: E402
    return DH, DN


def update_store(store: Path = STORE, today: Optional[date] = None, sessions: int = SESSIONS) -> int:
    """Fetch the official NSE files for every missing trading day of the rolling window (newest first). Returns
    how many days were added. The first run fetches ~400 days (about 20 minutes); later runs one day."""
    DH, DN = _research()
    today = today or date.today()
    store.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in store.glob("px_*.csv")}
    f = DN.Fetcher(0.4)
    added, d, seen, misses = 0, today, 0, 0
    while seen < sessions and misses < 15 and d > today - timedelta(days=int(sessions * 1.6) + 30):
        if d.weekday() < 5:
            key = f"px_{d:%Y%m%d}"
            if key in have:
                seen, misses = seen + 1, 0
            else:
                rows = None
                for url in DH.urls_equities(d):
                    blob = f.get(url)
                    if blob:
                        rows = DH.reduce_equities(DN.unzip_csv(blob), d)
                        break
                if rows:
                    pd.DataFrame(rows).to_csv(store / f"{key}.csv", index=False)
                    blob = f.get(DN.urls_delivery(d)[0])
                    if blob:
                        dl = DN.parse_delivery(blob.decode("utf-8", "replace"), d, None)
                        if dl:
                            pd.DataFrame(dl).to_csv(store / f"dl_{d:%Y%m%d}.csv", index=False)
                    blob = f.get(DH.url_pr(d))
                    ca = DH.parse_bc(blob, d) if blob else []
                    if ca:
                        pd.DataFrame(ca).to_csv(store / f"ca_{d:%Y%m%d}.csv", index=False)
                    added, seen, misses = added + 1, seen + 1, 0
                else:
                    misses += 1                                      # holiday (or not published yet)
        d -= timedelta(days=1)
    for p in sorted(store.glob("px_*.csv"))[:-sessions]:            # keep the window only
        for stem in ("px", "dl", "ca"):
            q = store / p.name.replace("px_", f"{stem}_")
            if q.exists():
                q.unlink()
    return added


def load_panel(store: Path = STORE) -> Dict[str, pd.DataFrame]:
    """Wide matrices (dates x symbols): open, high, low, close, volume, value, deliv; prices adjusted for the
    window's splits, bonuses and demergers (NSE PR files: the bhavcopy's previous close is NOT adjusted)."""
    px = [pd.read_csv(p) for p in sorted(store.glob("px_*.csv"))]
    if not px:
        raise RuntimeError("no NSE price files in the insights store")
    raw = pd.concat(px, ignore_index=True)
    raw = raw[raw["series"] == "EQ"]
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.drop_duplicates(["date", "symbol"], keep="last")
    panel = {k: raw.pivot(index="date", columns="symbol", values=k).astype(float)
             for k in ("open", "high", "low", "close", "volume", "value")}
    dl = [pd.read_csv(p) for p in sorted(store.glob("dl_*.csv"))]
    if dl:
        d = pd.concat(dl, ignore_index=True).drop_duplicates(["date", "symbol"], keep="last")
        d["date"] = pd.to_datetime(d["date"])
        panel["deliv"] = d.pivot(index="date", columns="symbol", values="deliv_pct").reindex_like(panel["close"])
    else:
        panel["deliv"] = pd.DataFrame(np.nan, index=panel["close"].index, columns=panel["close"].columns)
    ca = [pd.read_csv(p, dtype=str) for p in sorted(store.glob("ca_*.csv"))]
    if ca:
        adjust(panel, pd.concat(ca, ignore_index=True))
    return panel


def adjust(panel: Dict[str, pd.DataFrame], ca: pd.DataFrame) -> int:
    """Multiply prices before each ex-date by its factor (volumes divided). Returns how many were applied."""
    sys.path.insert(0, str(ROOT / "research"))
    from history20 import purpose_factor                              # noqa: E402 - every NSE wording, tested
    ca = ca[ca["series"] == "EQ"].drop_duplicates(["symbol", "ex_date", "purpose"])
    close = panel["close"]
    n = 0
    for r in ca.itertuples():
        f = purpose_factor(r.purpose)
        if f is None or r.symbol not in close.columns:
            continue
        ex = pd.Timestamp(r.ex_date)
        col = close[r.symbol]
        after = col.index[(col.index >= ex) & col.notna()]
        if len(after) == 0 or after[0] <= col.first_valid_index():
            continue
        i = after[0]
        if isinstance(f, float) and math.isnan(f):                   # demerger: the opening gap
            prev = col.loc[:i].dropna().iloc[-2]
            o = panel["open"].at[i, r.symbol]
            f = o / prev if o and prev and 0.05 < o / prev < 1 else None
            if f is None:
                continue
        before = col.index < i
        for k in ("open", "high", "low", "close"):
            panel[k].loc[before, r.symbol] *= f
        panel["volume"].loc[before, r.symbol] /= f
        n += 1
    return n


# ------------------------------------------------------------------ official filings (NSE feeds)
def _norm(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9 ]", " ", str(name).lower().replace("&", " and "))
    return " ".join(w for w in s.split() if w not in ("limited", "ltd", "the"))


def red_flag(subject: str, text: str) -> Optional[str]:
    t = f"{subject} {text}".lower()
    if "auditor" in t and "resign" in t:
        return "auditor resigned"
    if "pledge" in t and not any(w in t for w in ("release", "revocation", "revoke")):
        return "shares pledged"
    return next((why for key, why in RED if key in t), None)


def parse_feed(xml_text: str, names: Dict[str, str], meetings: bool = False) -> List[dict]:
    """Items of an NSE RSS feed for listed stocks: date, symbol, subject, text, link, red flag (or meeting date)."""
    import xml.etree.ElementTree as ET
    out = []
    for it in ET.fromstring(xml_text).findall("./channel/item"):
        sym = names.get(_norm(it.findtext("title") or ""))
        if not sym:
            continue                                                 # mutual funds, debt-only issuers, InvITs
        desc = " ".join((it.findtext("description") or "").split())
        try:
            when = datetime.strptime((it.findtext("pubDate") or "").strip()[:11], "%d-%b-%Y").date()
        except ValueError:
            continue
        if meetings:
            meet = desc.split("Meeting Date:")[-1].strip()[:11] if "Meeting Date:" in desc else ""
            try:
                md = datetime.strptime(meet, "%d-%b-%Y").date().isoformat()
            except ValueError:
                continue
            out.append(dict(date=when.isoformat(), symbol=sym, subject="Board meeting", text=f"on {md}",
                            link=it.findtext("link") or "", red="", meeting=md))
            continue
        subject = desc.split("|SUBJECT:")[-1].strip() if "|SUBJECT:" in desc else desc[:80]
        if any(n in subject.lower() for n in NOISE):
            continue
        text = desc.split("|SUBJECT:")[0].strip()
        out.append(dict(date=when.isoformat(), symbol=sym, subject=subject, text=text[:300],
                        link=it.findtext("link") or "", red=red_flag(subject, text) or "", meeting=""))
    return out


def update_filings(store: Path, fetcher, today: date) -> int:
    """Today's feeds into the store's filings.csv (the feed holds one day; the store keeps 45). Raises when the
    feeds can't be read (the report then says filings were NOT checked)."""
    names_p = store / "equity_list.csv"
    if not names_p.exists() or (datetime.now().timestamp() - names_p.stat().st_mtime) > 7 * 86400:
        blob = fetcher.get(EQUITY_LIST)
        if blob:
            names_p.write_bytes(blob)
    lst = pd.read_csv(names_p, encoding="latin-1")
    lst.columns = [c.strip().upper() for c in lst.columns]
    names = {_norm(n): str(sym).strip() for sym, n in zip(lst["SYMBOL"], lst["NAME OF COMPANY"])}
    ann, bm = fetcher.get(FEED_ANN), fetcher.get(FEED_BM)
    if not ann:
        raise RuntimeError("NSE announcements feed unavailable")
    rows = parse_feed(ann.decode("utf-8", "replace"), names) + \
        (parse_feed(bm.decode("utf-8", "replace"), names, meetings=True) if bm else [])
    p = store / "filings.csv"
    old = pd.read_csv(p, dtype=str).fillna("") if p.exists() else pd.DataFrame()
    new = pd.concat([old, pd.DataFrame(rows, dtype=str)], ignore_index=True)
    if len(new):
        new = new.drop_duplicates(["symbol", "subject", "link", "meeting"], keep="last")
        new = new[new["date"] >= (today - timedelta(days=45)).isoformat()]
        new.to_csv(p, index=False)
    state = store / "filings_ok.txt"
    state.write_text(today.isoformat())
    return len(rows)


def filings_window(store: Path, today: date, days: int = 30) -> str:
    """'30 days', or 'since 8 Oct' while the store holds less (the NSE feed only has the current day)."""
    p = store / "filings.csv"
    if not p.exists():
        return f"{days} days"
    first = pd.read_csv(p, usecols=["date"], dtype=str)["date"].min()
    return f"since {pd.Timestamp(first):%d %b}" if first and first > (today - timedelta(days=days)).isoformat() \
        else f"{days} days"


def filings_index(store: Path, today: date, days: int = 30, ahead_days: int = 14) -> Dict[str, List[dict]]:
    """symbol -> newest-first filings of the last `days` days and board meetings in the next `ahead_days`."""
    p = store / "filings.csv"
    if not p.exists():
        return {}
    f = pd.read_csv(p, dtype=str).fillna("")
    since = (today - timedelta(days=days)).isoformat()
    out: Dict[str, List[dict]] = {}
    for r in f.sort_values("date", ascending=False).itertuples():
        if r.meeting:
            if today.isoformat() <= r.meeting <= (today + timedelta(days=ahead_days)).isoformat():
                out.setdefault(r.symbol, []).append(
                    {"date": r.meeting, "title": f"Board meeting on {r.meeting} (results or other decisions)",
                     "ahead": True})
            continue
        if r.date >= since:
            title = r.subject + (f" - {r.red}" if r.red else "")
            out.setdefault(r.symbol, []).append({"date": r.date, "title": title, "red": r.red in BLOCKING,
                                                 "caution": bool(r.red) and r.red not in BLOCKING, "link": r.link})
    return out


# ------------------------------------------------------------------ facts and rules
def facts(panel: Dict[str, pd.DataFrame], bench: str = "NIFTYBEES") -> pd.DataFrame:
    """One row per stock with a price on the last session: every number the rules and the reasons use."""
    c, h, lo = panel["close"], panel["high"], panel["low"]
    d = c.index[-1]
    last = c.iloc[-1]
    alive = last.notna()
    n_hist = c.notna().sum()
    sma50, sma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
    sma200_prev = c.rolling(200).mean().iloc[-21] if len(c) > 221 else pd.Series(np.nan, index=c.columns)
    ret = lambda k: (last / c.iloc[-1 - k] - 1) * 100 if len(c) > k else pd.Series(np.nan, index=c.columns)  # noqa: E731
    r63, r126, r252 = ret(63), ret(126), ret(252)
    vol = np.log(c).diff().iloc[-252:].std() * math.sqrt(252) * 100
    hi52, lo52 = c.iloc[-252:].max(), c.iloc[-252:].min()
    pc = c.shift(1)
    tr = pd.concat([(h - lo), (h - pc).abs(), (lo - pc).abs()]).groupby(level=0).max()
    atr = tr.rolling(14).mean().iloc[-1]
    value20 = panel["value"].iloc[-20:].median() / 1e7
    vol_ratio = panel["volume"].iloc[-1] / panel["volume"].iloc[-21:-1].mean()
    deliv, deliv20 = panel["deliv"].iloc[-1], panel["deliv"].iloc[-21:-1].mean()
    day = (last / pc.iloc[-1] - 1) * 100
    b = c[bench] if bench in c.columns else None
    b63 = float((b.iloc[-1] / b.iloc[-64] - 1) * 100) if b is not None and len(b) > 64 else np.nan
    df = pd.DataFrame({
        "close": last, "day_pct": day, "sma50": sma50, "sma200": sma200, "sma200_prev": sma200_prev,
        "r63": r63, "r126": r126, "r252": r252, "vol": vol, "hi52": hi52, "lo52": lo52,
        "atr_pct": atr / last * 100, "value_cr": value20, "vol_ratio": vol_ratio, "deliv": deliv,
        "deliv20": deliv20, "n_hist": n_hist, "rs63": r63 - b63})[alive]
    df["liquid"] = (df["value_cr"] >= RULES["min_value_cr"]) & (df["close"] >= RULES["min_price"]) & \
                   (df["n_hist"] >= RULES["min_history"])
    liq = df[df["liquid"]]
    # NSE's momentum-index method: 6- and 12-month returns per unit of volatility, z-scored, averaged
    z = lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else s * 0             # noqa: E731
    score = (z(liq["r126"] / liq["vol"]) + z(liq["r252"] / liq["vol"])) / 2
    df["mom_pct"] = score.rank(pct=True) * 100
    df.attrs["date"] = d
    df.attrs["n_liquid"] = int(df["liquid"].sum())
    return df


def market(panel: Dict[str, pd.DataFrame], bench: str = "NIFTYBEES") -> dict:
    c = panel["close"].get(bench)
    if c is None or c.dropna().size < 200:
        return {"ok": None, "why": "Nifty ETF history too short for the market filter"}
    c = c.dropna()
    px, avg = float(c.iloc[-1]), float(c.iloc[-200:].mean())
    return {"ok": px > avg, "px": px, "avg": avg,
            "why": f"Nifty ETF ₹{px:,.2f} is {'above' if px > avg else 'BELOW'} its 200-day average ₹{avg:,.2f}"}


@dataclass
class Report:
    date: pd.Timestamp
    market: dict
    buys: pd.DataFrame
    avoids: pd.DataFrame
    funnel_buy: List[tuple]
    funnel_avoid: List[tuple]
    universe: int
    liquid: int
    holdings_weak: pd.DataFrame = field(default_factory=pd.DataFrame)
    watch: pd.DataFrame = field(default_factory=pd.DataFrame)       # would be BUY ideas but for the market filter


def _ranked(cand: pd.DataFrame) -> pd.DataFrame:
    """Momentum 40%, relative strength 20%, nearness to the 52-week high 20%, delivery build-up 10%, trend 10%."""
    cand = cand.copy()
    rk = lambda s: s.rank(pct=True).fillna(0.5)                      # noqa: E731
    cand["score"] = (0.4 * rk(cand["mom_pct"]) + 0.2 * rk(cand["rs63"]) + 0.2 * rk(cand["close"] / cand["hi52"])
                     + 0.1 * rk(cand["deliv"] - cand["deliv20"]) + 0.1 * rk(cand["sma50"] / cand["sma200"]))
    return cand.sort_values("score", ascending=False)


def screen(df: pd.DataFrame, mkt: dict, flags: Optional[Dict[str, List[dict]]], holdings: Iterable[str] = ()) -> Report:
    """Apply the rules; record how many stocks survive each one (the explanation when nothing qualifies)."""
    R = RULES
    red = {s for s, items in (flags or {}).items() if any(i.get("red") for i in items)}
    steps_b = [("traded today (EQ)", pd.Series(True, index=df.index)),
               (f"liquid (≥ ₹{R['min_value_cr']:.0f} cr a day, price ≥ ₹{R['min_price']:.0f}, 1 year of history)",
                df["liquid"]),
               ("uptrend (above the 50-day, 50-day above the 200-day, 200-day rising)",
                (df["close"] > df["sma50"]) & (df["sma50"] > df["sma200"]) & (df["sma200"] > df["sma200_prev"])),
               (f"momentum in the top {100 - R['buy_momentum_pct']:.0f}% of liquid stocks",
                df["mom_pct"] >= R["buy_momentum_pct"]),
               (f"within {100 - R['buy_near_high'] * 100:.0f}% of the 52-week high", df["close"] >= R["buy_near_high"] * df["hi52"]),
               (f"not over-extended (≤ {R['buy_max_extension']:.0%} above the 50-day, today ≤ +{R['buy_max_day_move']:.0f}%)",
                (df["close"] <= (1 + R["buy_max_extension"]) * df["sma50"]) & (df["day_pct"] <= R["buy_max_day_move"])),
               (f"volatility acceptable (average daily range ≤ {R['buy_max_atr_pct']:.0f}%)", df["atr_pct"] <= R["buy_max_atr_pct"]),
               ("no red-flag filing in 30 days" if flags is not None else "red-flag filings: NOT checked (feed unavailable)",
                pd.Series(~df.index.isin(list(red)), index=df.index))]
    keep = pd.Series(True, index=df.index)
    funnel_b = []
    for name, cond in steps_b:
        keep &= cond.fillna(False)
        funnel_b.append((name, int(keep.sum())))
    cand = df[keep].copy()
    watch = cand.iloc[0:0]
    if mkt.get("ok") is False:
        funnel_b.append(("market filter: " + mkt["why"], 0))
        watch, cand = cand, cand.iloc[0:0]
    if len(cand):
        cand = _ranked(cand).head(R["top_n"])
    if len(watch):
        watch = _ranked(watch).head(R["top_n"])
    steps_a = [("liquid", df["liquid"]),
               ("downtrend (below the 200-day, 200-day falling)",
                (df["close"] < df["sma200"]) & (df["sma200"] < df["sma200_prev"])),
               (f"momentum in the bottom {R['avoid_momentum_pct']:.0f}%", df["mom_pct"] <= R["avoid_momentum_pct"]),
               (f"within {R['avoid_near_low'] * 100 - 100:.0f}% of the 52-week low", df["close"] <= R["avoid_near_low"] * df["lo52"])]
    keep = pd.Series(True, index=df.index)
    funnel_a = []
    for name, cond in steps_a:
        keep &= cond.fillna(False)
        funnel_a.append((name, int(keep.sum())))
    weak = df[keep].sort_values("mom_pct").head(R["top_n"])
    mine = [s for s in holdings if s in df.index]
    hw = df.loc[mine]
    hw = hw[((hw["close"] < hw["sma200"]).astype(int) + (hw["mom_pct"] <= R["avoid_momentum_pct"]).astype(int)
             + hw.index.isin(list(red)).astype(int)) >= 2] if len(hw) else hw
    return Report(df.attrs.get("date"), mkt, cand, weak, funnel_b, funnel_a, len(df), int(df["liquid"].sum()), hw,
                  watch)


# ------------------------------------------------------------------ text
def _rs(x: float) -> str:
    return f"₹{x:,.2f}" if x < 1000 else f"₹{x:,.0f}"


def why_buy(s: str, r: pd.Series, n_liquid: int, filings: Optional[List[dict]], watch: bool = False,
            window: str = "30 days") -> str:
    lines = [f"{'👀' if watch else '🟢'} {s}  {_rs(r.close)} ({r.day_pct:+.1f}% today)"]
    lines.append(f"• Trend: above its 50-day ({_rs(r.sma50)}) and 200-day ({_rs(r.sma200)}) averages; the 200-day "
                 f"is rising ({(r.sma200 / r.sma200_prev - 1) * 100:+.1f}% in 4 weeks).")
    lines.append(f"• Momentum: top {_pct(100 - r.mom_pct)}% of {n_liquid} liquid stocks (6 months {r.r126:+.0f}%, "
                 f"12 months {r.r252:+.0f}%, volatility {r.vol:.0f}% a year).")
    lines.append(f"• Strength: {(1 - r.close / r.hi52) * 100:.1f}% below its 52-week high ({_rs(r.hi52)}); "
                 f"{r.rs63:+.0f} points vs the Nifty ETF over 3 months.")
    if not math.isnan(r.deliv) and not math.isnan(r.deliv20):
        lines.append(f"• Buyers: delivery {r.deliv:.0f}% of volume today vs {r.deliv20:.0f}% average"
                     + (" (more shares taken home: accumulation)." if r.deliv > r.deliv20 + 5 else "."))
    lines.append(f"• Liquidity: ₹{r.value_cr:,.0f} cr traded a day; volume today {r.vol_ratio:.1f}x its 20-day average.")
    lines += _filings(filings, window)
    stop = max(r.sma50, r.close - 2 * r.atr_pct / 100 * r.close)
    lines.append(f"• Risks: moves {r.atr_pct:.1f}% a day on average; the idea is wrong below {_rs(stop)} (the 50-day "
                 "average or 2 average days' range, whichever is higher)." + _event_risk(filings or []))
    return "\n".join(lines)


def why_avoid(s: str, r: pd.Series, n_liquid: int, filings: Optional[List[dict]], held: bool = False,
              window: str = "30 days") -> str:
    lines = [f"🔴 {s}  {_rs(r.close)} ({r.day_pct:+.1f}% today)" + ("  ← YOU HOLD THIS" if held else "")]
    lines.append(f"• Trend: below its 200-day average ({_rs(r.sma200)}), which is falling "
                 f"({(r.sma200 / r.sma200_prev - 1) * 100:+.1f}% in 4 weeks); 50-day {_rs(r.sma50)}.")
    lines.append(f"• Momentum: weakest {_pct(r.mom_pct)}% of {n_liquid} liquid stocks (6 months {r.r126:+.0f}%, "
                 f"12 months {r.r252:+.0f}%).")
    lines.append(f"• Weakness: {(r.close / r.lo52 - 1) * 100:.1f}% above its 52-week low ({_rs(r.lo52)}), "
                 f"{(1 - r.close / r.hi52) * 100:.0f}% below its high; {r.rs63:+.0f} points vs the Nifty ETF over 3 months.")
    lines += _filings(filings, window)
    lines.append(f"• What would change the view: a close back above the 200-day average ({_rs(r.sma200)}).")
    return "\n".join(lines)


def _pct(x: float) -> int:
    return max(1, int(math.ceil(x)))


def _filings(items: Optional[List[dict]], window: str = "30 days") -> List[str]:
    if items is None:
        return ["• Filings: NOT checked today (NSE's filings feed unavailable) - read them before acting."]
    if not items:
        return [f"• Filings ({window}): none on NSE."]
    out = [f"• Filings ({window}, NSE):"]
    for i in items[:4]:
        mark = "⛔ " if i.get("red") else "⚠️ " if i.get("caution") else "📅 " if i.get("ahead") else ""
        out.append(f"   {mark}{i['date']}: {i['title'][:110]}")
    if len(items) > 4:
        out.append(f"   (+{len(items) - 4} more on nseindia.com)")
    return out


def _event_risk(items: List[dict]) -> str:
    ahead = [i for i in items if i.get("ahead")]
    return f" Event ahead: {ahead[0]['title'][:80]} ({ahead[0]['date']})." if ahead else ""


def compose(rep: Report, flags: Optional[Dict[str, List[dict]]], track: str = "", window: str = "30 days") -> List[str]:
    get = (lambda s: flags.get(s, [])) if flags is not None else (lambda s: None)   # noqa: E731
    d = pd.Timestamp(rep.date)
    head = (f"📊 Swing ideas for {d:%a %d %b %Y} (information only - the bot does NOT trade these)\n"
            f"Screened {rep.universe} NSE stocks, {rep.liquid} liquid. Market: {rep.market.get('why', 'unknown')}.")
    msgs = [head]
    if len(rep.buys):
        msgs.append("BUY ideas (days to weeks):\n\n" + "\n\n".join(
            why_buy(s, r, rep.liquid, get(s), window=window) for s, r in rep.buys.iterrows()))
    else:
        msgs.append("No BUY idea today. How the stocks were filtered:\n" + "\n".join(
            f"• {name}: {n}" for name, n in rep.funnel_buy) + "\n" + _no_buy_reason(rep))
        if len(rep.watch):
            msgs.append("WATCHLIST - strongest stocks that pass every other rule; NOT buy ideas while the market is in "
                        "a downtrend (they become ideas when it recovers):\n\n" + "\n\n".join(
                            why_buy(s, r, rep.liquid, get(s), watch=True, window=window)
                            for s, r in rep.watch.iterrows()))
    if len(rep.avoids):
        msgs.append("AVOID / EXIT if held:\n\n" + "\n\n".join(
            why_avoid(s, r, rep.liquid, get(s), window=window) for s, r in rep.avoids.iterrows()))
    else:
        msgs.append("No AVOID idea today: " + "; ".join(f"{name}: {n}" for name, n in rep.funnel_avoid) + ".")
    if len(rep.holdings_weak):
        msgs.append("Your holdings showing weakness:\n\n" + "\n\n".join(
            why_avoid(s, r, rep.liquid, get(s), held=True, window=window) for s, r in rep.holdings_weak.iterrows()))
    msgs.append((track + "\n\n" if track else "") +
                "How these are chosen: fixed rules (trend, NSE-style momentum, 52-week high, liquidity, volatility, "
                "official filings), not tuned to recent results.\n" + TESTED + "\nNot advice; check the filings "
                "yourself before acting.")
    return _split(msgs)


def _no_buy_reason(rep: Report) -> str:
    if rep.market.get("ok") is False:
        return ("Why: the market itself is in a downtrend; in such markets most breakouts fail, so no stock is "
                "suggested however strong it looks.")
    zero = next((name for name, n in rep.funnel_buy if n == 0), None)
    return f"Why: no stock passed '{zero}'." if zero else "Why: no stock passed every rule."


def _split(msgs: List[str], limit: int = 3800) -> List[str]:
    out = []
    for m in msgs:
        while len(m) > limit:
            cut = m.rfind("\n\n", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(m[:cut])
            m = m[cut:].lstrip()
        out.append(m)
    return out


# ------------------------------------------------------------------ track record
def record(rep: Report, path: Path) -> None:
    rows = [{"date": f"{pd.Timestamp(rep.date):%Y-%m-%d}", "side": side, "symbol": s, "price": float(r.close)}
            for side, df in (("BUY", rep.buys), ("AVOID", rep.avoids)) for s, r in df.iterrows()]
    if not rows:
        return
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "side", "symbol", "price"])
    new = pd.concat([old, pd.DataFrame(rows)]).drop_duplicates(["date", "side", "symbol"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False)


def track_record(panel: Dict[str, pd.DataFrame], path: Path, horizon: int = 20, bench: str = "NIFTYBEES") -> str:
    """Average return of past ideas `horizon` sessions later, against the Nifty ETF over the same days."""
    if not path.exists():
        return ""
    t = pd.read_csv(path)
    c = panel["close"]
    idx = c.index
    lines = []
    for side in ("BUY", "AVOID"):
        res = []
        for r in t[t["side"] == side].itertuples():
            d = pd.Timestamp(r.date)
            if d not in idx or r.symbol not in c.columns or bench not in c.columns:
                continue
            i = idx.get_loc(d)
            if i + horizon >= len(idx):
                continue
            later = idx[i + horizon]
            a = c.at[later, r.symbol] / c.at[d, r.symbol] - 1
            b = c.at[later, bench] / c.at[d, bench] - 1
            if not (math.isnan(a) or math.isnan(b)):
                res.append((a, b))
        if res:
            a = np.mean([x for x, _ in res]) * 100
            b = np.mean([y for _, y in res]) * 100
            beat = np.mean([x > y for x, y in res]) * 100
            lines.append(f"{side} ideas ({len(res)} with {horizon} sessions since): average {a:+.1f}% vs Nifty ETF "
                         f"{b:+.1f}%; {beat:.0f}% beat it.")
    return ("Track record: " + " ".join(lines)) if lines else ""


# ------------------------------------------------------------------ the job
def run(notify, client=None, today: Optional[date] = None, update: bool = True, store: Path = STORE,
        filings_fn=None) -> str:
    today = today or date.today()
    state_p = store / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    if state.get("sent") == today.isoformat():
        return "insights: already sent today"
    if update:
        update_store(store, today)
    panel = load_panel(store)
    d = panel["close"].index[-1]
    if d.date() != today:
        return f"insights: today's NSE file is not out yet (latest {d:%d %b}); tried again later"
    df = facts(panel)
    mkt = market(panel)
    flags = None                                                    # None = filings not checked (said in the text)
    if filings_fn is None and update:
        def filings_fn(day):
            _, DN = _research()
            update_filings(store, DN.Fetcher(0.4), day)
            return filings_index(store, day)
    if filings_fn is not None:
        try:
            flags = filings_fn(today)
        except Exception as e:                                      # noqa: BLE001 - the screen still runs
            log.warning("filings unavailable: %s", e)
    holdings = []
    if client is not None:
        try:
            holdings = [h.symbol for h in client.holdings()]
        except Exception as e:                                      # noqa: BLE001
            log.warning("holdings unavailable: %s", e)
    rep = screen(df, mkt, flags, holdings)
    track = track_record(panel, store / "ideas.csv")
    for m in compose(rep, flags, track, filings_window(store, today)):
        notify(m)
    record(rep, store / "ideas.csv")
    state_p.write_text(json.dumps({"sent": today.isoformat()}))
    return f"insights: {len(rep.buys)} buy, {len(rep.avoids)} avoid ideas sent"
