"""Download NSE history for the PREREGISTRATION.md Addendum 7 tests (run on the server: NSE blocks the laptop's
research container; it never touches the INDstocks API or its login).

  .venv/bin/python research/download_nse.py                 # Oct 2014 -> yesterday, resumes where it stopped
  .venv/bin/python research/download_nse.py --start 2024-01-01 --end 2024-01-31
  .venv/bin/python research/download_nse.py --pack          # research/data/nse_pack.tgz for the laptop

Per trading day (each file small; raw downloads are reduced and deleted at once):
  participant_oi.csv  FII / DII / pro / client open interest in index & stock futures and options (contracts)
  indices.csv         every NSE index: open/high/low/close, P/E, P/B, dividend yield
  fo.csv              per symbol: futures OI, near-month futures close & volume, call OI, put OI (all expiries)
  delivery.csv        per F&O / Nifty stock: traded qty, delivery qty, delivery %
Events (NSE website API, by month): board meetings (results dates), insider (PIT) trades, bulk deals -> *.jsonl

Polite: one request at a time with a pause; stops by itself at --stop-at (default 08:40 IST) so it never runs
into market hours, and the next run continues. Progress and errors go to stdout (redirect to a log).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tarfile
import time as _time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "data" / "nse"
ARCH = "https://nsearchives.nseindia.com"
SITE = "https://www.nseindia.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ---------------------------------------------------------------- URLs
def url_participant(d: date) -> str:
    return f"{ARCH}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"


def url_indices(d: date) -> str:
    return f"{ARCH}/content/indices/ind_close_all_{d:%d%m%Y}.csv"


def urls_fo(d: date) -> List[str]:
    old = f"{ARCH}/content/historical/DERIVATIVES/{d.year}/{MON[d.month - 1]}/fo{d:%d}{MON[d.month - 1]}{d.year}bhav.csv.zip"
    new = f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    return [new, old] if d >= date(2024, 6, 1) else [old, new]


def urls_delivery(d: date) -> List[str]:
    return [f"{ARCH}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
            f"{ARCH}/archives/equities/mto/MTO_{d:%d%m%Y}.DAT"]


# ---------------------------------------------------------------- parsers (pure: tested offline)
def _num(x) -> Optional[float]:
    try:
        s = str(x).strip().replace(",", "")
        return float(s) if s not in ("", "-", "NA", "None") else None
    except ValueError:
        return None


def parse_participant(text: str, d: date) -> List[dict]:
    lines = [l for l in text.splitlines() if l.strip()]
    start = next((i for i, l in enumerate(lines) if l.lower().startswith("client type")), None)
    if start is None:
        raise ValueError("no 'Client Type' header")
    rows = []
    for r in csv.DictReader(lines[start:]):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        who = r.get("Client Type", "").upper()
        if not who:
            continue
        rows.append({"date": d.isoformat(), "who": who, **{k: _num(v) for k, v in r.items() if k != "Client Type"}})
    return rows


INDEX_COLS = {"index name": "index", "open index value": "open", "high index value": "high",
              "low index value": "low", "closing index value": "close", "p/e": "pe", "p/b": "pb",
              "div yield": "div_yield"}


def parse_indices(text: str, d: date) -> List[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        r = {str(k).strip().lower(): v for k, v in r.items() if k}
        row = {"date": d.isoformat()}
        for src, dst in INDEX_COLS.items():
            row[dst] = (r.get(src) or "").strip() if dst == "index" else _num(r.get(src))
        if row["index"]:
            out.append(row)
    return out


def reduce_fo(rows: Iterable[dict], d: date) -> List[dict]:
    """Old bhavcopy (INSTRUMENT, SYMBOL, EXPIRY_DT, OPTION_TYP, CLOSE, CONTRACTS, OPEN_INT) or UDiFF
    (FinInstrmTp IDF/IDO/STF/STO, TckrSymb, XpryDt, OptnTp, ClsPric, TtlTradgVol, OpnIntrst, UndrlygPric)."""
    agg: Dict[str, dict] = {}
    for r in rows:
        r = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in r.items() if k}
        if "INSTRUMENT" in r:
            inst, sym, exp = r["INSTRUMENT"].upper(), r.get("SYMBOL", "").upper(), r.get("EXPIRY_DT", "")
            opt, close, vol, oi, und = r.get("OPTION_TYP", "").upper(), _num(r.get("CLOSE")), _num(r.get("CONTRACTS")), \
                _num(r.get("OPEN_INT")), None
            kind = {"FUTIDX": "IF", "OPTIDX": "IO", "FUTSTK": "SF", "OPTSTK": "SO"}.get(inst)
        else:
            tp, sym, exp = r.get("FinInstrmTp", "").upper(), r.get("TckrSymb", "").upper(), r.get("XpryDt", "")
            opt, close, vol, oi = r.get("OptnTp", "").upper(), _num(r.get("ClsPric")), _num(r.get("TtlTradgVol")), \
                _num(r.get("OpnIntrst"))
            und = _num(r.get("UndrlygPric"))
            kind = {"IDF": "IF", "IDO": "IO", "STF": "SF", "STO": "SO"}.get(tp)
        if not kind or not sym:
            continue
        a = agg.setdefault(sym, {"date": d.isoformat(), "symbol": sym, "is_index": kind[0] == "I", "fut_oi": 0.0,
                                 "fut_close": None, "fut_vol": None, "fut_expiry": None, "ce_oi": 0.0, "pe_oi": 0.0,
                                 "underlying": None})
        if und and not a["underlying"]:
            a["underlying"] = und
        if kind[1] == "F":
            a["fut_oi"] += oi or 0.0
            e = _expiry(exp)
            if e and (a["fut_expiry"] is None or e < a["fut_expiry"]):          # near month = earliest expiry
                a["fut_expiry"], a["fut_close"], a["fut_vol"] = e, close, vol
        elif opt == "CE":
            a["ce_oi"] += oi or 0.0
        elif opt == "PE":
            a["pe_oi"] += oi or 0.0
    out = []
    for a in agg.values():
        a["fut_expiry"] = a["fut_expiry"].isoformat() if a["fut_expiry"] else ""
        out.append(a)
    return out


def _expiry(s: str) -> Optional[date]:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d%b%Y"):
        try:
            return datetime.strptime(s.strip().title() if "%b" in fmt else s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_delivery(text: str, d: date, keep: Optional[set]) -> List[dict]:
    """sec_bhavdata_full (SYMBOL, SERIES, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER) or the older MTO .DAT file
    (record type 20: sr, symbol, series, traded qty, deliverable qty, % deliverable)."""
    out = []
    head = text.lstrip("﻿")[:300].upper()
    if "SYMBOL" in head and "DELIV" in head:
        for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
            r = {str(k).strip().upper(): (v or "").strip() for k, v in r.items() if k}
            sym, ser = r.get("SYMBOL", "").upper(), r.get("SERIES", "").upper()
            if ser != "EQ" or (keep is not None and sym not in keep):
                continue
            out.append({"date": d.isoformat(), "symbol": sym, "traded": _num(r.get("TTL_TRD_QNTY")),
                        "delivered": _num(r.get("DELIV_QTY")), "deliv_pct": _num(r.get("DELIV_PER"))})
        return out
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 7 and p[0] == "20" and p[3].upper() == "EQ":
            sym = p[2].upper()
            if keep is None or sym in keep:
                out.append({"date": d.isoformat(), "symbol": sym, "traded": _num(p[4]), "delivered": _num(p[5]),
                            "deliv_pct": _num(p[6])})
    return out


def unzip_csv(blob: bytes) -> List[dict]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


# ---------------------------------------------------------------- I/O
class Store:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.done_path = out / "done.json"
        self.done: Dict[str, List[str]] = json.loads(self.done_path.read_text()) if self.done_path.exists() else {}

    def is_done(self, source: str, d: date) -> bool:
        return d.isoformat() in set(self.done.get(source, []))

    def mark(self, source: str, d: date) -> None:
        self.done.setdefault(source, []).append(d.isoformat())
        tmp = self.done_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done))
        tmp.replace(self.done_path)

    def append(self, name: str, rows: List[dict]) -> None:
        if not rows:
            return
        p = self.out / name
        new = not p.exists()
        cols = list(rows[0].keys())
        if not new:
            with p.open(encoding="utf-8") as f:
                cols = next(csv.reader(f))
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)


class Fetcher:
    """One request at a time. 404 = file doesn't exist (holiday). A host that refuses us (HTTP 401/403) many times
    in a row is skipped for the rest of the run instead of being retried - NSE's website blocks most servers."""
    BLOCK_AFTER = 20                  # ~3 days of refusals in a row (a holiday alone gives about 6)

    def __init__(self, pause: float = 0.6):
        import requests
        self.requests = requests
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.pause = pause
        self.site_ok = False
        self.refused: Dict[str, int] = {}
        self.blocked: set = set()

    @staticmethod
    def host(url: str) -> str:
        return url.split("/")[2]

    def get(self, url: str, timeout: float = 30) -> Optional[bytes]:
        """Body, or None (missing, refused or failing). 429/5xx are retried with backoff, at most 3 times."""
        h = self.host(url)
        if h in self.blocked:
            return None
        for k in range(3):
            _time.sleep(self.pause)
            try:
                r = self.s.get(url, timeout=timeout)
            except self.requests.RequestException as e:
                print(f"  network error {url}: {str(e)[:120]}", flush=True)
                _time.sleep(3 * (k + 1))
                continue
            if r.status_code == 200 and r.content:
                self.refused[h] = 0
                return r.content
            if r.status_code == 404:
                self.refused[h] = 0
                return None
            if r.status_code in (401, 403):
                self.refused[h] = self.refused.get(h, 0) + 1
                if self.refused[h] >= self.BLOCK_AFTER:
                    self.blocked.add(h)
                    print(f"  {h} refuses this server (HTTP {r.status_code}); skipped for the rest of this run",
                          flush=True)
                else:
                    print(f"  HTTP {r.status_code} {url}", flush=True)
                return None
            print(f"  HTTP {r.status_code} {url}", flush=True)
            _time.sleep(5 * (k + 1))
        return None

    def api(self, path: str):
        """NSE website API: needs the cookies the home page sets."""
        if self.host(SITE) in self.blocked:
            return None
        if not self.site_ok:
            self.get(SITE + "/", timeout=30)                # cookies if it gives them; the API may answer anyway
            self.site_ok = True
        body = self.get(SITE + path)
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            self.site_ok = False                          # cookies expired: refresh next time
            return None


# ---------------------------------------------------------------- jobs
def daily(store: Store, f: Fetcher, d: date, keep: set) -> str:
    notes = []
    arch = Fetcher.host(ARCH)
    if not store.is_done("participant", d) and arch not in f.blocked:
        b = f.get(url_participant(d))
        if b is None:
            notes.append("participant: none")
        else:
            store.append("participant_oi.csv", parse_participant(b.decode("utf-8", "replace"), d))
        if arch not in f.blocked:
            store.mark("participant", d)
    if not store.is_done("indices", d) and arch not in f.blocked:
        b = f.get(url_indices(d))
        if b is not None:
            store.append("indices.csv", parse_indices(b.decode("utf-8", "replace"), d))
        else:
            notes.append("indices: none")
        if arch not in f.blocked:
            store.mark("indices", d)
    fo_syms = set()
    if not store.is_done("fo", d) and arch not in f.blocked:
        got = None
        for u in urls_fo(d):
            got = f.get(u, timeout=60)
            if got is not None:
                break
        if got is not None:
            rows = reduce_fo(unzip_csv(got), d)
            fo_syms = {r["symbol"] for r in rows if not r["is_index"]}
            store.append("fo.csv", rows)
        else:
            notes.append("fo: none")
        if arch not in f.blocked:
            store.mark("fo", d)
    if not store.is_done("delivery", d) and arch not in f.blocked:
        got = None
        for u in urls_delivery(d):
            got = f.get(u)
            if got is not None:
                break
        if got is not None:
            store.append("delivery.csv", parse_delivery(got.decode("utf-8", "replace"), d, keep | fo_syms or None))
        else:
            notes.append("delivery: none")
        if arch not in f.blocked:
            store.mark("delivery", d)
    return "; ".join(notes)


EVENT_APIS = {
    "board_meetings": "/api/corporate-board-meetings?index=equities&from_date={a}&to_date={b}",
    "insider": "/api/corporates-pit?index=equities&from_date={a}&to_date={b}",
    "bulk_deals": "/api/historical/bulk-deals?from={a}&to={b}",
}


def events(store: Store, f: Fetcher, start: date, end: date) -> None:
    m = date(start.year, start.month, 1)
    while m <= end:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = min(nxt - timedelta(days=1), end)
        for name, path in EVENT_APIS.items():
            if store.is_done(name, m):
                continue
            data = f.api(path.format(a=f"{m:%d-%m-%Y}", b=f"{last:%d-%m-%Y}"))
            if Fetcher.host(SITE) in f.blocked:
                return
            if data is None:
                print(f"  {name} {m:%Y-%m}: no answer", flush=True)
                continue                                   # not marked: retried next run
            rows = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(rows, list):                  # unexpected shape: keep it whole for inspection
                print(f"  {name} {m:%Y-%m}: unexpected answer, keys {list(data)[:8] if isinstance(data, dict) else type(data)}",
                      flush=True)
                rows = [data]
            with (store.out / f"{name}.jsonl").open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps({"_month": f"{m:%Y-%m}", **r} if isinstance(r, dict) else {"_month": f"{m:%Y-%m}", "value": r}) + "\n")
            store.mark(name, m)
            print(f"  {name} {m:%Y-%m}: {len(rows) if isinstance(rows, list) else 0} rows", flush=True)
        m = nxt


def universe() -> set:
    syms = set()
    try:
        sys.path.insert(0, str(ROOT))
        from trader import config as C
        syms |= set(C.universe({}, nse=False)) | set(C.universe({}))
    except Exception as e:                                # noqa: BLE001
        print(f"universe unavailable ({e}); delivery kept for F&O stocks only", flush=True)
    return syms | {"NIFTYBEES", "JUNIORBEES", "BANKBEES", "GOLDBEES", "MON100", "ITBEES"}


def ist_now() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2014-10-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--stop-at", default="08:40", help="IST time to stop by (resume later); '' = never")
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--skip-events", action="store_true")
    ap.add_argument("--pack", action="store_true", help="write research/data/nse_pack.tgz and exit")
    a = ap.parse_args(argv)
    if a.pack:
        pack = OUT.parent / "nse_pack.tgz"
        with tarfile.open(pack, "w:gz") as t:
            for p in sorted(OUT.glob("*")):
                if p.suffix in (".csv", ".jsonl", ".json"):
                    t.add(p, arcname=f"nse/{p.name}")
        print(f"{pack} {pack.stat().st_size / 1e6:.1f} MB")
        return 0
    start = date.fromisoformat(a.start)
    end = date.fromisoformat(a.end) if a.end else ist_now().date() - timedelta(days=1)
    stop = datetime.strptime(a.stop_at, "%H:%M").time() if a.stop_at else None
    began = ist_now()

    def must_stop() -> bool:
        now = ist_now()
        if stop is None:
            return False
        stop_dt = datetime.combine(now.date(), stop)
        if began.time() > stop:                           # started in the evening: stop tomorrow morning
            stop_dt = datetime.combine(began.date() + timedelta(days=1), stop)
        return now >= stop_dt

    store, f, keep = Store(OUT), Fetcher(a.pause), universe()
    print(f"NSE download {start} -> {end} into {OUT} (stops at {a.stop_at or 'never'} IST)", flush=True)
    if not a.skip_events:
        try:
            events(store, f, start, end)
        except Exception as e:                            # noqa: BLE001 - daily files still worth getting
            print(f"events failed: {e}", flush=True)
    d, n = end, 0                                         # newest first: recent years are most useful
    while d >= start:
        if must_stop():
            print(f"Stopping at {ist_now():%H:%M} (before market hours); run again to continue.", flush=True)
            return 0
        if Fetcher.host(ARCH) in f.blocked:
            print("NSE's file archive refuses this server; stopping (nothing more can be downloaded).", flush=True)
            return 1
        if d.weekday() < 5:
            try:
                note = daily(store, f, d, keep)
            except Exception as e:                        # noqa: BLE001 - one bad day must not stop the run
                note = f"ERROR {type(e).__name__}: {str(e)[:200]}"
            n += 1
            if note or n % 50 == 0:
                print(f"{d} {note or 'ok'} ({n} days)", flush=True)
        d -= timedelta(days=1)
    print(f"Finished: {n} days checked. Next: --pack", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
