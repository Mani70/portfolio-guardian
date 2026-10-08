"""Download ~20 years of NSE history for PREREGISTRATION.md Addendum 12 (run on the server, like download_nse.py).

  .venv/bin/python research/download_history.py                 # Jan 2005 -> yesterday, resumes where it stopped
  .venv/bin/python research/download_history.py --extras        # NSE's list of renamed symbols (joins histories)
  .venv/bin/python research/download_history.py --pack          # research/data/hist_pack.tgz for the laptop

Per trading day, newest first:
  equities_YYYY.csv  every EQ/BE-series stock and ETF: open, high, low, close, prevclose, volume, value (Rs)
                     (bhavcopy: old cmDDMONYYYYbhav.csv.zip format, and the UDiFF format from July 2024)
  indices.csv        every NSE index close (ind_close_all files), where NSE has them (from about Feb 2012)
  corp_actions.csv   the "Bc" file of NSE's daily PR zip: bonuses, splits, demergers by ex-date (from Jan 2010;
                     PREREGISTRATION.md Addendum 12a - the bhavcopy's previous close is NOT adjusted on ex-dates)
Once: index_history.csv from niftyindices.com (Nifty 50, Next 50, Quality 30 and others, year by year), for the
years the daily index files don't cover, and tri.csv: total-return values (dividends included) of NI_TRI.
  .venv/bin/python research/download_history.py --indices-only   # just those two

Polite (one request at a time, a pause), resumable (done.json), stops by itself at --stop-at (default 08:40 IST).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tarfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_nse import (ARCH, MON, Fetcher, Store, _num, ist_now, parse_indices, unzip_csv,   # noqa: E402
                          url_indices)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "data" / "hist"
SERIES = {"EQ", "BE"}
NI_BASE = "https://www.niftyindices.com/BackPage"           # was Backpage.aspx until 2025 (then returns a web page)
NI_URL = f"{NI_BASE}/getHistoricaldatatabletoString"
NI_TRI_URL = f"{NI_BASE}/getTotalReturnIndexString"
NI_TRI = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY200 MOMENTUM 30", "NIFTY 200"]   # Addendum 13: dividends included
NI_INDICES = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY100 QUALITY 30", "NIFTY 100", "NIFTY100 LOW VOLATILITY 30",
              "NIFTY50 VALUE 20", "NIFTY200 MOMENTUM 30", "NIFTY MIDCAP 100", "NIFTY 1D RATE INDEX"]


def urls_equities(d: date) -> List[str]:
    old = f"{ARCH}/content/historical/EQUITIES/{d.year}/{MON[d.month - 1]}/cm{d:%d}{MON[d.month - 1]}{d.year}bhav.csv.zip"
    new = f"{ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    return [new, old] if d >= date(2024, 7, 8) else [old, new]


def url_pr(d: date) -> str:
    return f"{ARCH}/archives/equities/bhavcopy/pr/PR{d:%d%m%y}.zip"


def parse_bc(blob: bytes, d: date) -> List[dict]:
    """Corporate actions from the Bc file in a PR zip (SERIES, SYMBOL, ..., EX_DT dd/mm/yyyy, ..., PURPOSE)."""
    import zipfile
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next((n for n in z.namelist() if n.lower().startswith("bc") and n.lower().endswith(".csv")), None)
        if name is None:
            return []
        text = z.read(name).decode("latin-1")
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        r = {str(k).strip().upper(): (str(v).strip() if v is not None else "") for k, v in r.items() if k}
        ex = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):    # the format changed over the years
            try:
                ex = datetime.strptime(r.get("EX_DT", ""), fmt).date()
                break
            except ValueError:
                continue
        if ex is None:
            continue
        if r.get("SYMBOL") and r.get("PURPOSE"):
            out.append(dict(file_date=d.isoformat(), series=r.get("SERIES", "").upper(), symbol=r["SYMBOL"].upper(),
                            ex_date=ex.isoformat(), purpose=" ".join(r["PURPOSE"].split())))
    return out


def reduce_equities(rows: Iterable[dict], d: date) -> List[dict]:
    """Old bhavcopy (SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, PREVCLOSE, TOTTRDQTY, TOTTRDVAL) or UDiFF (TckrSymb,
    SctySrs, OpnPric, HghPric, LwPric, ClsPric, PrvsClsgPric, TtlTradgVol, TtlTrfVal). EQ and BE series only."""
    out = []
    for r in rows:
        r = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in r.items() if k}
        if "SYMBOL" in r:
            sym, ser = r["SYMBOL"].upper(), r.get("SERIES", "").upper()
            vals = [r.get(k) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL")]
        else:
            sym, ser = r.get("TckrSymb", "").upper(), r.get("SctySrs", "").upper()
            vals = [r.get(k) for k in ("OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol",
                                       "TtlTrfVal")]
        if not sym or ser not in SERIES:
            continue
        o, h, lo, c, pc, vol, val = (_num(v) for v in vals)
        if c is None or c <= 0:
            continue
        out.append(dict(date=d.isoformat(), symbol=sym, series=ser, open=o, high=h, low=lo, close=c, prevclose=pc,
                        volume=vol, value=val))
    return out


def daily(store: Store, f: Fetcher, d: date, want_indices: bool, want_ca: bool = False) -> str:
    notes = []
    if not store.is_done("equities", d):
        rows = None
        for url in urls_equities(d):
            blob = f.get(url)
            if blob:
                rows = reduce_equities(unzip_csv(blob), d)
                break
        if rows:
            store.append(f"equities_{d.year}.csv", rows)
        else:
            notes.append("equities: none")
        if Fetcher.host(ARCH) not in f.blocked:
            store.mark("equities", d)
    if want_indices and not store.is_done("indices", d):
        blob = f.get(url_indices(d))
        if blob:
            store.append("indices.csv", parse_indices(blob.decode("utf-8", "replace"), d))
        else:
            notes.append("indices: none")
        if Fetcher.host(ARCH) not in f.blocked:
            store.mark("indices", d)
    if want_ca and not store.is_done("ca", d):
        blob = f.get(url_pr(d))
        rows = parse_bc(blob, d) if blob else []
        if rows:
            store.append("corp_actions.csv", rows)
        elif not blob:
            notes.append("corp actions: none")
        if Fetcher.host(ARCH) not in f.blocked:
            store.mark("ca", d)
    return "; ".join(notes)


def parse_niftyindices(payload: dict) -> List[dict]:
    """niftyindices.com history reply: {"d": "[{\\"Index Name\\": ..., \\"HistoricalDate\\": \\"02 Jan 2015\\",
    \\"OPEN\\": ..., \\"CLOSE\\": ...}, ...]"} (the inner list is itself JSON text)."""
    if isinstance(payload, list):                                # the /BackPage/ endpoints reply with the list
        rows = payload
    else:
        inner = payload.get("d") if isinstance(payload, dict) else None
        rows = json.loads(inner) if isinstance(inner, str) else (inner or [])
    out = []
    for r in rows:
        name = r.get("Index Name") or r.get("INDEX_NAME") or ""
        day = None
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
            try:
                day = datetime.strptime(str(r.get("HistoricalDate", "")).strip(), fmt).date()
                break
            except ValueError:
                continue
        close = _num(r.get("CLOSE"))
        if day and close:
            out.append(dict(date=day.isoformat(), index=name, open=_num(r.get("OPEN")), high=_num(r.get("HIGH")),
                            low=_num(r.get("LOW")), close=close))
    return out


def parse_tri(rows) -> List[dict]:
    """niftyindices.com total-return reply: [{"Index Name": "Nifty 50", "Date": "10 Jan 2008",
    "TotalReturnsIndex": "7483.81", "NTR_Value": ...}, ...]."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            day = datetime.strptime(str(r.get("Date", "")).strip(), "%d %b %Y").date()
        except ValueError:
            continue
        tri = _num(r.get("TotalReturnsIndex"))
        if tri:
            out.append(dict(date=day.isoformat(), index=r.get("Index Name") or "", tri=tri))
    return out


def index_history(store: Store, start_year: int, end_year: int, tri: bool = False) -> None:
    """Year-by-year history of the key indices from niftyindices.com (best effort: reported if refused): prices into
    index_history.csv, or with tri=True total-return values into tri.csv."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/126 Safari/537.36", "Content-Type": "application/json; charset=UTF-8",
                      "Accept": "application/json, text/javascript, */*; q=0.01",
                      "Origin": "https://www.niftyindices.com",
                      "Referer": "https://www.niftyindices.com/reports/historical-data",
                      "X-Requested-With": "XMLHttpRequest"})
    try:
        s.get("https://www.niftyindices.com/reports/historical-data", timeout=30)
    except Exception as e:                                       # noqa: BLE001
        print(f"niftyindices home page: {e}", flush=True)
    refused = 0
    src, url, out_file = ("tri", NI_TRI_URL, "tri.csv") if tri else ("ni2", NI_URL, "index_history.csv")
    for name in (NI_TRI if tri else NI_INDICES):
        got = 0
        for y in range(start_year, end_year + 1):
            key = f"{src}:{name}:{y}"
            if key in store.done.get(src, []):
                continue
            cinfo = "{'name':'%s','startDate':'01-Jan-%d','endDate':'31-Dec-%d','indexName':'%s'}" % (name, y, y, name)
            try:
                r = s.post(url, json={"cinfo": cinfo}, timeout=40)
                rows = (parse_tri(r.json()) if tri else parse_niftyindices(r.json())) if r.status_code == 200 else []
            except Exception as e:                               # noqa: BLE001
                rows, r = [], None
                print(f"  niftyindices {name} {y}: {str(e)[:120]}", flush=True)
            if rows:
                store.append(out_file, rows)
                got += len(rows)
            else:
                refused += 1
                if r is not None:
                    print(f"  niftyindices {name} {y}: HTTP {r.status_code}, no rows", flush=True)
            if y < date.today().year:                             # the current year is fetched again next run
                store.done.setdefault(src, []).append(key)
            time.sleep(0.5)
            tmp = store.done_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(store.done))
            tmp.replace(store.done_path)
            if refused >= 12 and got == 0:
                print("niftyindices.com gives nothing to this server; skipped (index files cover Oct 2014 on).",
                      flush=True)
                return
        print(f"  niftyindices {name}: {got} rows", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--stop-at", default="08:40", help="IST time to stop by (resume later); '' = never")
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--skip-index-history", action="store_true")
    ap.add_argument("--indices-only", action="store_true",
                    help="only the niftyindices.com histories (prices and total return), then exit")
    ap.add_argument("--pack", action="store_true", help="write research/data/hist_pack.tgz and exit")
    ap.add_argument("--extras", action="store_true", help="fetch NSE's symbol-change list and exit")
    a = ap.parse_args(argv)
    if a.extras:
        f = Fetcher(a.pause)
        OUT.mkdir(parents=True, exist_ok=True)
        blob = f.get(f"{ARCH}/content/equities/symbolchange.csv")
        if blob:
            (OUT / "symbolchange.csv").write_bytes(blob)
            print(f"symbolchange.csv: {len(blob.splitlines())} lines")
        else:
            print("symbolchange.csv: not available (renamed stocks start a new history)")
        return 0
    if a.pack:
        pack = OUT.parent / "hist_pack.tgz"
        with tarfile.open(pack, "w:gz") as t:
            for p in sorted(OUT.glob("*")):
                if p.suffix in (".csv", ".json"):
                    t.add(p, arcname=f"hist/{p.name}")
        print(f"{pack} {pack.stat().st_size / 1e6:.1f} MB")
        return 0
    start = date.fromisoformat(a.start)
    end = date.fromisoformat(a.end) if a.end else ist_now().date() - timedelta(days=1)
    stop = datetime.strptime(a.stop_at, "%H:%M").time() if a.stop_at else None
    began = ist_now()

    def must_stop() -> bool:
        if stop is None:
            return False
        now = ist_now()
        stop_dt = datetime.combine(now.date(), stop)
        if began.time() > stop:
            stop_dt = datetime.combine(began.date() + timedelta(days=1), stop)
        return now >= stop_dt

    store, f = Store(OUT), Fetcher(a.pause)
    if a.indices_only:
        index_history(store, start.year, end.year)
        index_history(store, start.year, end.year, tri=True)
        return 0
    print(f"NSE history {start} -> {end} into {OUT} (stops at {a.stop_at or 'never'} IST)", flush=True)
    if not a.skip_index_history:
        try:
            index_history(store, start.year, end.year)
        except Exception as e:                                   # noqa: BLE001 - the daily files matter more
            print(f"index history failed: {e}", flush=True)
    d, n = end, 0
    while d >= start:
        if must_stop():
            print(f"Stopping at {ist_now():%H:%M}; run again to continue.", flush=True)
            return 0
        if Fetcher.host(ARCH) in f.blocked:
            print("NSE's file archive refuses this server; stopping.", flush=True)
            return 1
        if d.weekday() < 5:
            try:
                note = daily(store, f, d, want_indices=True, want_ca=d >= date(2010, 1, 1))
            except Exception as e:                               # noqa: BLE001
                note = f"ERROR {type(e).__name__}: {str(e)[:200]}"
            n += 1
            if note or n % 100 == 0:
                print(f"{d} {note or 'ok'} ({n} days)", flush=True)
        d -= timedelta(days=1)
    print(f"Finished: {n} days checked. Next: --pack", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
