"""Keep the momentum strategy's stock list equal to today's Nifty 50.

NSE reviews the Nifty 50 twice a year (changes take effect at the end of March and of September). The list that
ships with the code (backtest.example.yaml) is the Nifty 50 of 2025. This job downloads NSE's published
constituent file once a week (Mondays 08:35) and saves it in trader/state/universe.json; the trader uses that
list from then on. A `universe:` list in trader.yaml, if you set one, still wins.

  python -m trader.run universe        # check now; Telegram message if the list changed

A download is used only if it looks right: 45-55 symbols, at least 35 in common with the current list, at most 6
names swapped, and all but at most 3 known to INDstocks (unknown ones are left out). Otherwise the current list stays and the reason is
printed; the morning health message warns when the list has not been confirmed for 30 days.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

URLS = ("https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
        "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "text/csv,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}
STATE = Path("trader") / "state" / "universe.json"
STALE_DAYS = 30
MAX_CHANGES = 6                    # more than this many names swapped at once is treated as a bad file


def parse(text: str) -> List[str]:
    """Symbols from NSE's index constituent CSV (Company Name, Industry, Symbol, Series, ISIN Code)."""
    out = set()
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        r = {str(k).strip().lower(): str(v or "").strip() for k, v in row.items() if k}
        sym = r.get("symbol", "").upper()
        if sym and r.get("series", "EQ").upper() == "EQ" and re.fullmatch(r"[A-Z0-9&\-]{1,20}", sym):
            out.add(sym)
    return sorted(out)


def check(new: List[str], current: Iterable[str], known: Optional[Callable[[str], bool]] = None
          ) -> Tuple[Optional[List[str]], str]:
    """(list to use, note) or (None, reason it was refused)."""
    current = set(current)
    if not 45 <= len(new) <= 55:
        return None, f"the file has {len(new)} symbols (expected about 50)"
    if current and len(set(new) & current) < 35:
        return None, f"only {len(set(new) & current)} names in common with the current list - has the file format changed?"
    if current and len(set(new) ^ current) > 2 * MAX_CHANGES:
        return None, (f"{len(set(new) - current)} additions / {len(current - set(new))} removals at once (NSE changes "
                      f"a few names per review) - check the list and set `universe:` in trader.yaml if it is right")
    note = ""
    if known is not None:
        unknown = [s for s in new if not known(s)]
        if len(unknown) > 3:
            return None, f"{len(unknown)} symbols unknown to INDstocks ({', '.join(unknown[:6])})"
        if unknown:
            new = [s for s in new if s not in unknown]
            note = f"left out (unknown to INDstocks): {', '.join(unknown)}"
    return new, note


def fetch(get=None) -> Tuple[str, str]:
    """(url, text) of the first source that answers with a CSV."""
    if get is None:
        import requests
        get = requests.get
    errors = []
    for url in URLS:
        try:
            r = get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200 and "symbol" in r.text[:500].lower():
                return url, r.text
            errors.append(f"{url}: HTTP {r.status_code}")
        except Exception as e:                                    # noqa: BLE001
            errors.append(f"{url}: {str(e)[:120]}")
    raise RuntimeError("; ".join(errors))


def load(root: Path) -> Optional[dict]:
    p = Path(root) / STATE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ok = isinstance(d, dict) and isinstance(d.get("symbols"), list) and len(d["symbols"]) >= 40 \
            and all(isinstance(x, str) for x in d["symbols"])
        return d if ok else None
    except Exception:                                             # noqa: BLE001 - never stop a job over this file
        return None


def update(root: Path, current: List[str], today: date, known=None, get=None) -> Tuple[bool, str]:
    """Download, check and save. Returns (list changed, message for the owner). Raises on download failure."""
    url, text = fetch(get)
    new, note = check(parse(text), current, known)
    if new is None:
        raise ValueError(f"downloaded Nifty 50 list not used: {note}")
    added, removed = sorted(set(new) - set(current)), sorted(set(current) - set(new))
    prev = load(root) or {}
    d = {"symbols": new, "source": url, "checked": today.isoformat(),
         "as_of": today.isoformat() if (added or removed or not prev) else prev.get("as_of", today.isoformat())}
    p = Path(root) / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    if not (added or removed):
        return False, f"Nifty 50 list unchanged ({len(new)} stocks, checked {today:%d %b %Y})." + (f" {note}" if note else "")
    msg = (f"Nifty 50 list updated from NSE: added {', '.join(added) or 'none'}; removed {', '.join(removed) or 'none'}. "
           "Momentum uses the new list from its next month-end run: it sells removed stocks it holds and may buy "
           "added ones.")
    return True, msg + (f" ({note})" if note else "")


def staleness(root: Path, cfg: dict, today: date) -> Optional[str]:
    """Warning for the morning health message, or None."""
    if cfg.get("universe"):
        return None                                               # the owner set the list in trader.yaml
    d = load(root)
    if d is None:
        return "Nifty 50 list never downloaded: using the 2025 list in the code (run: .venv/bin/python -m trader.run universe)"
    age = (today - datetime.strptime(d.get("checked", "1970-01-01"), "%Y-%m-%d").date()).days
    if age > STALE_DAYS:
        return (f"Nifty 50 list last confirmed {age} days ago (NSE download failing? see logs/cron/universe.log, "
                "or set `universe:` in trader.yaml)")
    return None
