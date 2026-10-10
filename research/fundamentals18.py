"""Addendum 18 (research/PREREGISTRATION.md): annual results as filed on NSE, for every company in the universe.

  python research/fundamentals18.py download     # resumable; writes research/data/fund/results.csv
  python research/fundamentals18.py check        # coverage report

For each symbol in research/data/fund/universe.csv: NSE's list of annual results (www.nseindia.com/api/
corporates-financial-results) gives every filing with its date; each standalone result is read from NSE's archive
page (FY2005-FY2017) or its XBRL file (FY2018 on, the full-year "FourD" context). Amounts in Rs crore.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
FUND = ROOT / "research" / "data" / "fund"
API = "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={}&period=Annual"
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
                      "Safari/537.36", "Accept": "application/json, text/html, */*", "Referer": "https://www.nseindia.com/"}
FIELDS = ["revenue", "total_income", "other_income", "interest", "pbt", "pat", "equity_capital", "face_value",
          "reserves", "eps", "dividend_pct"]
PAUSE = 0.4
_lock = threading.Lock()


def _num(s) -> float:
    s = str(s).replace(",", "").strip()
    try:
        x = float(s)
    except ValueError:
        return math.nan
    return math.nan if x == -999 else x


# ---------------------------------------------------------------- archive pages (FY2005-FY2017)
HTML_RULES = {  # field: (non-bank label patterns in order of preference, bank-format patterns); first non-blank wins
    "revenue": ([r"^Total income from operations", r"^\(a\)\s*Net sales", r"^Net Sales", r"^Income from Operations",
                 r"^Revenue from Operations", r"^Net Income from Operations"], [r"^Total Income"]),
    "total_income": ([r"^Total Income$", r"^Total Income\b"], [r"^Total Income"]),
    "other_income": ([r"^Other Income"], [r"^Other Income"]),
    "interest": ([r"^Finance costs?\b", r"^Interest$", r"^Interest\s*\(", r"^Interest and finance"], [r"^Interest Expended"]),
    "pbt": ([r"activities before tax", r"before tax"], [r"activities before tax", r"before tax"]),
    "pat": ([r"^Net Profit.*for the period", r"^Net Profit.*after tax"], [r"^Net Profit.*for the period", r"^Net Profit.*after tax"]),
    "equity_capital": ([r"^Paid-up Equity Share Capital"], [r"^Paid-up Equity Share Capital"]),
    "face_value": ([r"^Face Value"], [r"^Face Value"]),
    "reserves": ([r"^Reserves? excluding Revaluation"], [r"^Reserves? excluding Revaluation"]),
    "dividend_pct": ([r"^Dividend \(%\)"], [r"^Dividend \(%\)"]),
}
MONEY = {"revenue", "total_income", "other_income", "interest", "pbt", "pat", "equity_capital", "reserves"}
NUM = re.compile(r"^-?[\d,]*\.?\d+$|^-$")


def parse_html(text: str, bank: bool) -> Dict[str, float]:
    t = re.sub(r"<[^>]+>", " | ", text)
    tok = [x.strip() for x in html.unescape(t).split("|") if x.strip()]
    try:
        start = next(i for i, x in enumerate(tok) if x == "Description")
    except StopIteration:
        return {}
    bank = bank or any(x == "Interest Earned" for x in tok[start:start + 40])
    unit = tok[start + 1].lower() if start + 1 < len(tok) else ""
    scale = 1 / 100 if "lakh" in unit else 1.0 if "crore" in unit else 1 / 10 if "million" in unit else 1 / 100
    pairs = [(tok[i], tok[i + 1]) for i in range(start + 2, len(tok) - 1)
             if NUM.match(tok[i + 1]) and not NUM.match(tok[i])]
    out: Dict[str, float] = {"_bank_format": float(bank)}
    for field, (pats, bank_pats) in HTML_RULES.items():
        for pat in (bank_pats if bank else pats):
            rx = re.compile(pat, re.I)
            v = next((_num(val) for lab, val in pairs if rx.search(lab) and not math.isnan(_num(val))), math.nan)
            if not math.isnan(v):
                out[field] = v * (scale if field in MONEY else 1.0)
                break
    # EPS: "Basic EPS after Extraordinary items | 21.22", or a heading "...after extraordinary items..." then "(a) Basic"
    eps = math.nan
    for i, (lab, val) in enumerate(pairs):
        if re.search(r"^Basic EPS after Extraordinary|^Basic EPS for continuing and discontinued", lab, re.I):
            eps = _num(val)
            break
    if math.isnan(eps):
        for i in range(start, len(tok)):
            if re.search(r"after extraordinary", tok[i], re.I) and re.search(r"earnings per share|EPS", tok[i], re.I):
                for j in range(i + 1, min(i + 6, len(tok) - 1)):
                    if re.match(r"^\(a\)\s*Basic", tok[j], re.I) and NUM.match(tok[j + 1]):
                        eps = _num(tok[j + 1])
                        break
                break
    if math.isnan(eps):
        eps = next((_num(v) for lab, v in pairs if re.search(r"^Basic EPS|^\(a\)\s*Basic", lab, re.I)
                    and not math.isnan(_num(v))), math.nan)
    out["eps"] = eps
    return out


# ---------------------------------------------------------------- XBRL (FY2018 on)
XBRL_TAGS = {
    "revenue": ["RevenueFromOperations", "Income"],
    "total_income": ["Income"],
    "other_income": ["OtherIncome"],
    "interest": ["FinanceCosts", "InterestExpended"],
    "pbt": ["ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "pat": ["ProfitLossForPeriod", "ProfitLossForThePeriod"],
    "equity_capital": ["PaidUpValueOfEquityShareCapital"],
    "face_value": ["FaceValueOfEquityShareCapital"],
    "reserves": ["ReserveExcludingRevaluationReserves", "OtherEquity", "ReservesAndSurplus"],
    "eps": ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "BasicEarningsPerShareAfterExtraordinaryItems",
            "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShareBeforeExtraordinaryItems"],
}


def parse_xbrl(x: str, bank: bool) -> Dict[str, float]:
    vals: Dict[str, Dict[str, float]] = {}
    for tag, ctx, v in re.findall(r'<in-bse-fin:([A-Za-z]+)\s[^>]*contextRef="([^"]+)"[^>]*>([^<]+)<', x):
        vals.setdefault(tag, {})[ctx] = _num(v)
    out: Dict[str, float] = {}
    for field, tags in XBRL_TAGS.items():
        if bank and field == "revenue":
            tags = ["Income"]
        for tg in tags:
            c = vals.get(tg) or {}
            v = next((c[k] for k in ("FourD", "OneD", "OneI", "FourI") if k in c and not math.isnan(c[k])), math.nan)
            if not math.isnan(v) and not (field == "reserves" and v == 0):    # 0 = not given
                out[field] = v / 1e7 if field in MONEY else v
                break
    return out


# ---------------------------------------------------------------- download
class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEAD)

    def get(self, url: str, tries: int = 4) -> Optional[requests.Response]:
        for k in range(tries):
            try:
                r = self.s.get(url, timeout=60)
                if r.status_code == 200:
                    time.sleep(PAUSE)
                    return r
                if r.status_code in (404, 410):
                    return None
            except requests.RequestException:
                pass
            time.sleep(2 ** (k + 1))
        return None


def pick(records: List[dict]) -> List[dict]:
    """One record per year end: the standalone result first filed (what was known first); consolidated only when no
    standalone exists."""
    by = {}
    for r in records:
        try:
            end = datetime.strptime(r["toDate"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        if end.year < 2004:
            continue
        fd = r.get("filingDate") or "-"
        try:
            filed = datetime.strptime(fd, "%d-%b-%Y %H:%M") if fd != "-" else None
        except ValueError:
            filed = None
        stand = r.get("consolidated", "").startswith("Non")
        key = (not stand, filed or datetime(2007, 7, 1))
        if end not in by or key < by[end][0]:
            by[end] = (key, {**r, "_end": end, "_filed": filed, "_standalone": stand})
    return [v[1] for v in by.values()]


def fetch_symbol(c: Client, sym: str) -> List[dict]:
    lp = FUND / "lists" / f"{sym}.json"
    if lp.exists():
        records = json.loads(lp.read_text())
    else:
        r = c.get(API.format(requests.utils.quote(sym)))
        records = r.json() if r is not None else []
        lp.write_text(json.dumps(records))
    rows = []
    for rec in pick(records if isinstance(records, list) else []):
        bank = rec.get("bank") in ("Y", "B")
        xb = rec.get("xbrl") or ""
        link = rec.get("resultDetailedDataLink") or ""
        data, src = {}, ""
        if xb.endswith((".xml", ".XML")):
            r = c.get(xb)
            data, src = (parse_xbrl(r.text, bank), "xbrl") if r is not None else ({}, "xbrl-missing")
        elif link.startswith("http"):
            r = c.get(link)
            data, src = (parse_html(r.text, bank), "html") if r is not None else ({}, "html-missing")
        else:
            src = "no-document"
        rows.append({"symbol": sym, "year_end": rec["_end"].isoformat(),
                     "filed": rec["_filed"].strftime("%Y-%m-%d %H:%M") if rec["_filed"] else "",
                     "standalone": rec["_standalone"], "bank": bank, "audited": rec.get("audited", ""),
                     "source": src, "seq": rec.get("seqNumber", ""), **{f: data.get(f, math.nan) for f in FIELDS}})
    return rows


def download(workers: int = 3) -> None:
    (FUND / "lists").mkdir(parents=True, exist_ok=True)
    out = FUND / "results.csv"
    done = set(pd.read_csv(out, usecols=["symbol"])["symbol"]) if out.exists() else set()
    syms = [s for s in pd.read_csv(FUND / "universe.csv")["symbol"].drop_duplicates() if s not in done]
    print(f"{len(done)} symbols done, {len(syms)} to go", flush=True)
    clients = [Client() for _ in range(workers)]
    t0, n = time.time(), [0]

    def job(k_sym):
        k, sym = k_sym
        try:
            rows = fetch_symbol(clients[k % workers], sym)
        except Exception as e:                                       # noqa: BLE001 - one bad company never stops the run
            print(f"  {sym}: {str(e)[:120]}", flush=True)
            rows = [{"symbol": sym, "source": f"error: {str(e)[:60]}"}]
        if not rows:
            rows = [{"symbol": sym, "source": "no-results"}]
        with _lock:
            df = pd.DataFrame(rows).reindex(columns=["symbol", "year_end", "filed", "standalone", "bank", "audited",
                                                     "source", "seq", *FIELDS])
            df.to_csv(out, mode="a", header=not out.exists(), index=False)
            n[0] += 1
            if n[0] % 25 == 0:
                print(f"  {n[0]}/{len(syms)} symbols, {time.time() - t0:.0f}s", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, enumerate(syms)))
    print("done", flush=True)


def check() -> None:
    r = pd.read_csv(FUND / "results.csv")
    print(r["source"].value_counts().to_string())
    ok = r[r["source"].isin(["html", "xbrl"])]
    print("rows with revenue and profit:", int((ok["revenue"].notna() & ok["pat"].notna()).sum()), "of", len(ok))
    print(ok.assign(y=ok["year_end"].str[:4]).groupby("y")[["revenue", "pat", "reserves", "eps"]].apply(
        lambda g: g.notna().mean().round(2)).to_string())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "check"])
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    download(a.workers) if a.cmd == "download" else check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
