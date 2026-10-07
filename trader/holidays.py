"""NSE trading holidays, so the live month-end is the real last trading session of the month.

Without a calendar the live strategies treat the last WEEKDAY of a month as its last session. When that weekday is
an exchange holiday, the session before it was the real month-end, and the rebalance would happen one session late
(the first session of the next month, as a catch-up). Replays use the exact trading days and never needed this.

This job downloads NSE's published list of trading holidays (equity segment) once a week (Mondays 08:37) and
saves it in trader/state/holidays.json. A `holidays:` list in trader.yaml (YYYY-MM-DD dates) is added to it, for
a holiday NSE announces at short notice. If the download fails, the last saved list stays; with no list at all
the bot falls back to weekdays (the old behaviour) and the morning health message says so.

  python -m trader.run holidays        # download now and print the list
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set, Tuple

SITE = "https://www.nseindia.com"
API = SITE + "/api/holiday-master?type=trading"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
           "Referer": SITE + "/resources/exchange-communication-holidays"}
STATE = Path("trader") / "state" / "holidays.json"
STALE_DAYS = 10                    # the job runs weekly: one missed Monday is fine, two are reported
FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")


def _date(x) -> Optional[date]:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):                             # YAML reads an unquoted 2026-10-30 as a date
        return x
    s = str(x or "").strip()
    if len(s) > 10 and s[:10].count("-") == 2 and s[10] in " T":
        s = s[:10]                                      # "2026-10-30 00:00:00"
    for f in FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def parse(data) -> List[date]:
    """Weekday holidays of the equity segment ("CM") from NSE's holiday-master reply."""
    rows = data.get("CM") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no 'CM' (equity) list in NSE's reply - has the format changed?")
    out = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _date(r.get("tradingDate") or r.get("date") or r.get("holidayDate"))
        if d is not None and d.weekday() < 5:          # weekend "holidays" change nothing
            out.add(d)
    return sorted(out)


def fetch(get: Optional[Callable] = None):
    """NSE's JSON reply. `get(url, headers, timeout)` -> object with .status_code and .text (tests pass a fake)."""
    if get is None:
        import requests
        s = requests.Session()
        try:
            s.get(SITE + "/", headers=HEADERS, timeout=20)     # cookies, if the home page gives them
        except Exception:                                        # noqa: BLE001 - the API may answer anyway
            pass
        get = s.get
    r = get(API, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"NSE holiday list: HTTP {r.status_code}")
    try:
        return json.loads(r.text)
    except ValueError as e:
        raise RuntimeError(f"NSE holiday list: not JSON ({str(e)[:80]})") from e


def load_file(root: Path) -> Optional[dict]:
    """The saved file, or None if missing or unreadable (dates that don't parse are skipped by load())."""
    try:
        d = json.loads((Path(root) / STATE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("dates"), list) else None
    except Exception:                                            # noqa: BLE001 - never stop a job over this file
        return None


def _config_entries(cfg: Optional[dict]) -> list:
    raw = (cfg or {}).get("holidays") or []
    return list(raw) if isinstance(raw, (list, tuple, set)) else [raw]      # a single date written without a list


def config_problems(cfg: Optional[dict]) -> List[str]:
    """`holidays:` entries in trader.yaml that are not dates (shown in the morning health message)."""
    return [str(x) for x in _config_entries(cfg) if _date(x) is None]


def load(root: Path, cfg: Optional[dict] = None) -> Set[date]:
    """Saved NSE holidays plus any `holidays:` dates in trader.yaml. Never raises."""
    try:
        out = {_date(x) for x in ((load_file(root) or {}).get("dates") or [])}
        out |= {_date(x) for x in _config_entries(cfg)}
        return {d for d in out if d is not None}
    except Exception:                                            # noqa: BLE001 - weekdays only, never a crash
        return set()


def update(root: Path, today: date, get=None) -> Tuple[bool, str]:
    """Download, check and save. Returns (list changed, message). Raises if the download is unusable."""
    new = parse(fetch(get))
    this_year = [d for d in new if d.year == today.year]
    if len(this_year) < 5:
        raise ValueError(f"only {len(this_year)} holidays for {today.year} in NSE's list (expected 10-20); not used")
    prev = load_file(root) or {}
    old = sorted({_date(x) for x in prev.get("dates") or []} - {None})
    # NSE's list covers the current year: keep earlier years from the saved file, replace the rest
    first = min(new)
    dates = sorted({d for d in old if d.year < first.year} | set(new))
    added = sorted(set(dates) - set(old))
    removed = sorted(set(old) - set(dates))
    p = Path(root) / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"dates": [d.isoformat() for d in dates], "source": API,
                               "checked": today.isoformat()}, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    upcoming = [d for d in dates if d >= today][:4]
    msg = (f"NSE trading holidays {today.year}: {len(this_year)} on weekdays; next: "
           + (", ".join(f"{d:%a %d %b}" for d in upcoming) or "none this year"))
    if prev and (added or removed):
        msg = ("NSE holiday list changed: "
               + (f"added {', '.join(f'{d:%d %b %Y}' for d in added)}" if added else "")
               + ("; " if added and removed else "")
               + (f"removed {', '.join(f'{d:%d %b %Y}' for d in removed)}" if removed else "")
               + f". {msg}")
        return True, msg
    return not prev, msg


def staleness(root: Path, today: date) -> Optional[str]:
    """Warning for the morning health message, or None."""
    d = load_file(root)
    if d is None:
        what = "damaged" if (Path(root) / STATE).exists() else "never downloaded"
        return (f"NSE holiday list {what}: month-end uses weekdays only "
                "(run: .venv/bin/python -m trader.run holidays)")
    if not any(_date(x) and _date(x).year == today.year for x in d["dates"]) and today >= date(today.year, 1, 15):
        return f"NSE holiday list has no {today.year} dates yet: month-end uses weekdays only until it does"
    age = (today - (_date(d.get("checked")) or date(1970, 1, 1))).days
    if age > STALE_DAYS:
        return f"NSE holiday list last checked {age} days ago (download failing? see logs/cron/holidays.log)"
    return None


def is_session(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)
