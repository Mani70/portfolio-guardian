"""F&O practice run (research/PREREGISTRATION.md Addenda 20 / 20a): paper only - never an order, never real money.

Every weekday evening (after NSE publishes the day's F&O file):
  1. settles practice positions that expired (worth their value at the Nifty 50's close on expiry day);
  2. opens the strategies whose cycle starts today (the first session after an expiry), with the same strike choice,
     prices and cost model as the 14-year test (research/fo20.py);
  3. values every open position at today's closing prices and sends one plain-language Telegram message.
The run lasts RUN_WEEKS from its first day; after that it opens nothing new and reports the final tally once the last
position has expired.

  python -m trader.run fo-paper
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "trader" / "state" / "fo_paper.json"
STORE = ROOT / "cache" / "fo"
RUN_WEEKS = 4
log = logging.getLogger("trader.fo_paper")

# name: (research strategy, cadence, plain description)
PAPER = {
    "S1 monthly": ("S1 bull put spread 3%/6%", "monthly",
                   "Bull put spread: earns its fee if the Nifty does NOT fall more than about 3% before expiry"),
    "S2 monthly": ("S2 iron condor 4%/7%", "monthly",
                   "Iron condor: earns its fee if the Nifty stays within about 4% up or down until expiry"),
    "S1 weekly": ("S1 bull put spread 3%/6%", "weekly",
                  "Bull put spread (weekly): earns its fee if the Nifty does NOT fall more than about 3% in the week"),
    "S2 weekly": ("S2 iron condor 4%/7%", "weekly",
                  "Iron condor (weekly): earns its fee if the Nifty stays within about 4% up or down for the week"),
    "S3 monthly": ("insurance", "monthly",
                   "Crash insurance: a put that pays out if the Nifty falls more than 5% before expiry; it costs a fee"),
    "S4 monthly": ("trend", "monthly",
                   "Trend bet: buys a call (gains if the Nifty rises) when the market trend is up, a put when it is down"),
}
GUIDE = ("1-minute guide: an option is a contract on the Nifty 50 index. A PUT pays its buyer if the Nifty falls below a "
         "set level (the \"strike\") by a set date (the \"expiry\"); a CALL pays if it rises above. The seller receives a "
         "fee up front (the \"premium\") and pays out if that happens. A \"spread\" sells one option and buys a cheaper "
         "one further away as protection, so the most it can lose is fixed in advance. One lot = {lot} units of the "
         "Nifty (about {notional} of index value).")


def _research():
    sys.path.insert(0, str(ROOT / "research"))
    import fo20 as F                                                    # noqa: E402 - the tested rules and costs
    return F


def load_state(path: Path = STATE) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)


def fetch_day(d: date, store: Path = STORE) -> Optional[pd.DataFrame]:
    """Today's Nifty futures and options rows (NSE's daily F&O file), cached in store."""
    p = store / f"fo_{d:%Y%m%d}.csv"
    if p.exists():
        return pd.read_csv(p, parse_dates=["date", "expiry"])
    F = _research()
    import download_nse as DN                                           # noqa: E402
    f = DN.Fetcher(0.3)
    for u in F.urls(d):
        blob = f.get(u)
        if blob:
            rows = F.parse(DN.unzip_csv(blob), d)
            if rows:
                store.mkdir(parents=True, exist_ok=True)
                df = pd.DataFrame(rows, columns=F.COLS)
                df.to_csv(p, index=False)
                return pd.read_csv(p, parse_dates=["date", "expiry"])
    return None


def _price(day: pd.DataFrame, expiry, kind: str, strike: float) -> float:
    r = day[(day["expiry"] == pd.Timestamp(expiry)) & (day["kind"] == kind) & (day["strike"] == strike)]
    if r.empty:
        return math.nan
    r = r.iloc[0]
    return float(r["close"]) if r["traded"] > 0 and r["close"] > 0 else float(r["settle"])


def _legs_for(name: str, spot: float, trend_up: Optional[bool]):
    F = _research()
    research, _, _ = PAPER[name]
    if research == "insurance":
        return [F.Leg("PE", 0.95, +1)]
    if research == "trend":
        if trend_up is None:
            return None
        return [F.Leg("CE", 1.0, +1)] if trend_up else [F.Leg("PE", 1.0, +1)]
    return F.STRATEGIES[research]


def _trend_up() -> Optional[bool]:
    """Nifty above its 200-day average (the daily ideas report's market check on the Nifty 50 fund)."""
    try:
        from .insights import STORE as IS, load_panel, market
        return market(load_panel(IS)).get("ok")
    except Exception as e:                                              # noqa: BLE001
        log.warning("trend unknown: %s", e)
        return None


def _value(pos: dict, day: pd.DataFrame, spot: float, lot: int) -> Optional[float]:
    """What the position is worth if closed at today's close: rupees received (+) or paid (-) to close it."""
    total = 0.0
    for kind, strike, side, _ in pos["legs"]:
        px = _price(day, pos["expiry"], kind, strike)
        if math.isnan(px):
            px = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
        total += side * px * lot
    return total


def _settle(pos: dict, final: float, lot: int) -> float:
    F = _research()
    val, stt = 0.0, 0.0
    for kind, strike, side, _ in pos["legs"]:
        intrinsic = max(0.0, final - strike) if kind == "CE" else max(0.0, strike - final)
        val += side * intrinsic * lot
        if side > 0 and intrinsic > 0:
            stt += F.exercise_stt(intrinsic, lot)
    return pos["credit"] + val - pos["costs"] - stt


def _rs(x: float) -> str:
    from .plain import rupees
    return rupees(x)


def _describe_open(name: str, pos: dict, value: float, spot: float, lot: int) -> str:
    legs = pos["legs"]
    sold = [l for l in legs if l[2] < 0]
    bought = [l for l in legs if l[2] > 0]
    words = {"PE": "put", "CE": "call"}
    head = f"{PAPER[name][2]}."
    what = ("Sold " + " and ".join(f"the {k:,.0f} {words[t]}" for t, k, _, _ in sold) + "; " if sold else "") + \
           ("bought " + " and ".join(f"the {k:,.0f} {words[t]}" for t, k, _, _ in bought) if bought else "")
    exp = pd.Timestamp(pos["expiry"])
    if pos["credit"] >= 0:
        money = f"Fee received {_rs(pos['credit'])}, charges {_rs(pos['costs'])}."
    else:
        money = f"Paid {_rs(-pos['credit'])} for it, charges {_rs(pos['costs'])}."
    now = pos["credit"] + value - pos["costs"]
    line = (f"{head}\n   {what[0].upper() + what[1:]} (expiry {exp:%a %d %b}). {money}\n"
            f"   If closed at today's prices: {'profit' if now >= 0 else 'loss'} {_rs(abs(now))} (after the charges "
            "paid so far).")
    if pos.get("max_loss") is not None and sold:
        line += f" Most it can lose: {_rs(pos['max_loss'])}."
    puts_sold = [k for t, k, s, _ in sold if t == "PE"]
    calls_sold = [k for t, k, s, _ in sold if t == "CE"]
    if puts_sold:
        line += f" Safe while the Nifty stays above {max(puts_sold):,.0f} (now {(spot / max(puts_sold) - 1) * 100:.1f}% above)"
        line += (f" and below {min(calls_sold):,.0f} (now {(1 - spot / min(calls_sold)) * 100:.1f}% below)." if calls_sold
                 else ".")
    return line


def run(notify, today: Optional[date] = None, fetch: bool = True, state_path: Path = STATE, store: Path = STORE,
        trend_fn=_trend_up) -> str:
    F = _research()
    today = today or date.today()
    st = load_state(state_path)
    if st.get("sent") == today.isoformat():
        return "fo-paper: already reported today"
    day = fetch_day(today, store) if fetch else (pd.read_csv(store / f"fo_{today:%Y%m%d}.csv", parse_dates=["date", "expiry"])
                                                if (store / f"fo_{today:%Y%m%d}.csv").exists() else None)
    if day is None or day.empty:
        return f"fo-paper: NSE's F&O file for {today:%d %b} is not out yet; tried again later"
    spot = float(day["spot"].dropna().iloc[0]) if day["spot"].notna().any() else math.nan
    if math.isnan(spot):
        fut = day[day["kind"] == "FUT"].sort_values("expiry")
        spot = float(fut["close"].iloc[0]) if len(fut) else math.nan
    lot = int(day["lot"].dropna().iloc[0]) if day["lot"].notna().any() else F.LOT
    st.setdefault("started", today.isoformat())
    st.setdefault("open", {})
    st.setdefault("closed", [])
    ends = date.fromisoformat(st["started"]) + timedelta(weeks=RUN_WEEKS)
    t = pd.Timestamp(today)
    notes = []

    # 1. settle what expired (today or on a day the job missed: settled at today's close, and said so)
    for name, pos in list(st["open"].items()):
        exp = pd.Timestamp(pos["expiry"])
        if exp <= t:
            pnl = _settle(pos, spot, lot)
            st["closed"].append({"name": name, "opened": pos["opened"], "expiry": pos["expiry"], "pnl": pnl,
                                 "final": spot, "late": bool(exp < t)})
            del st["open"][name]
            notes.append(f"• {PAPER[name][2].split(':')[0]} ({pd.Timestamp(pos['opened']):%d %b} - {exp:%d %b}): "
                         f"{'profit' if pnl >= 0 else 'loss'} {_rs(abs(pnl))} after charges (Nifty ended at "
                         f"{spot:,.0f}{', settled a day late' if exp < t else ''}).")

    # 2. open the strategies whose cycle starts today: an expiry seen in the last file is now in the past
    fut_exp = sorted({pd.Timestamp(e) for e in day.loc[day["kind"] == "FUT", "expiry"]})
    opt_exp = sorted({pd.Timestamp(e) for e in day.loc[day["kind"].isin(["CE", "PE"]), "expiry"]})
    seen = {k: [pd.Timestamp(x) for x in v] for k, v in (st.get("seen") or {}).items()}
    passed = {"monthly": [e for e in seen.get("monthly", []) if e < t],
              "weekly": [e for e in seen.get("weekly", []) if e < t]}
    if today < ends:
        trend = None
        for name, (research, cadence, _) in PAPER.items():
            if name in st["open"] or not passed[cadence]:
                continue
            target = (fut_exp if cadence == "monthly" else opt_exp)
            target = next((e for e in target if e > t), None)
            if target is None:
                continue
            if research == "trend" and trend is None:
                trend = trend_fn()
            legs = _legs_for(name, spot, trend)
            if not legs:
                notes.append(f"• {PAPER[name][2].split(':')[0]}: not opened (market trend unknown today).")
                continue
            pos = F.open_position(day, target, spot, legs, today)
            if pos is None:
                notes.append(f"• {PAPER[name][2].split(':')[0]}: not opened (no traded option near the chosen levels).")
                continue
            rec = {"opened": today.isoformat(), "expiry": target.date().isoformat(), "credit": pos["credit"],
                   "costs": pos["costs"], "legs": [[l.kind, k, l.side, px] for l, k, px in pos["legs"]],
                   "max_loss": F.max_loss(pos) if any(l.side < 0 for l, _, _ in pos["legs"]) else None}
            st["open"][name] = rec
            notes.append(f"• Opened: {PAPER[name][2].split(':')[0]} (expiry {target:%a %d %b}).")
    st["seen"] = {"monthly": [e.date().isoformat() for e in fut_exp], "weekly": [e.date().isoformat() for e in opt_exp]}

    # 3. the message
    msg = [f"📄 F&O PRACTICE RUN — {today:%a %d %b %Y} (paper only: no real money, no orders)",
           "What is this? For 4 weeks the bot practises option strategies on paper with NSE's real closing prices, to "
           "check they behave as designed before any real money is even considered.",
           GUIDE.format(lot=lot, notional=_rs(lot * spot) if not math.isnan(spot) else "-"),
           f"Nifty 50 today: {spot:,.2f}"]
    if notes:
        msg.append("Today:\n" + "\n".join(notes))
    if st["open"]:
        parts = []
        for i, (name, pos) in enumerate(sorted(st["open"].items()), 1):
            parts.append(f"{i}) " + _describe_open(name, pos, _value(pos, day, spot, lot), spot, lot))
        msg.append("Open practice positions:\n" + "\n".join(parts))
    elif not st["closed"]:
        msg.append("No practice position yet: each strategy starts the day after an expiry (weekly ones within a "
                   "week, monthly ones after the end-of-month expiry).")
    if st["closed"]:
        c = st["closed"]
        tot = sum(x["pnl"] for x in c)
        msg.append(f"Practice run so far: {len(c)} finished, {sum(x['pnl'] > 0 for x in c)} profitable, total "
                   f"{'profit' if tot >= 0 else 'loss'} {_rs(abs(tot))} after charges.")
    verdicts = _verdicts()
    if verdicts:
        msg.append(verdicts)
    if today >= ends:
        msg.append(f"The 4-week practice run ended on {ends:%d %b}: no new positions; open ones are followed until they "
                   "expire." + (" All positions have now finished." if not st["open"] else ""))
    text = "\n\n".join(msg)
    for chunk in _split(text):
        notify(chunk)
    st["sent"] = today.isoformat()
    save_state(st, state_path)
    return f"fo-paper: {len(st['open'])} open, {len(st['closed'])} finished"


def _verdicts() -> str:
    p = ROOT / "research" / "fo20_results.csv"
    if not p.exists():
        return ""
    r = pd.read_csv(p)
    v = r[r["strategy"].astype(str).str.startswith("verdict: ")]
    if v.empty:
        return ""
    names = {"S1": "bull put spread", "S2": "iron condor", "S3": "crash insurance", "S4": "trend option buying"}
    out = []
    for _, x in v.iterrows():
        code = x["strategy"].split(": ", 1)[1].split()[0]
        out.append(f"{names.get(code, code)}: {'PASSED' if bool(x['passes']) else 'did NOT pass'}")
    return "Our 14-year test (2012-2026, real NSE prices, all charges): " + "; ".join(out) + "."


def _split(text: str, limit: int = 3800) -> List[str]:
    out, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + block) if cur else block
    return out + ([cur] if cur else [])
