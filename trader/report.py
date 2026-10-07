"""Monthly performance report (Telegram, 1st of each month 08:40): P&L per strategy against the Nifty ETF,
charges, and an estimate of capital-gains tax for the financial year so far.

  python -m trader.run report                 # last month on the 1st-3rd, else this month so far
  python -m trader.run report --month 2026-10

P&L = realized trades + the change in open positions' value at today's prices. A month's figure is the
change since the previous report's snapshot; the first report covers everything since the start.
Tax is an ESTIMATE: STCG 20% (held < 12 months), LTCG 12.5% above ₹1.25 lakh a year (that allowance is shared
with all your other investments), losses set off. Stocks you handed to the bot use YOUR purchase cost (their
buy dates aren't known, so they are counted as short-term). Charges are deducted as if allowed in full.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .models import CNC

NAMES = {"trend_allocation": "ETF trend", "momentum_rotation": "Momentum", "gap_reversal": "Gap reversal (intraday)",
         "momentum_control": "One portfolio (last session)", "momentum_t07": "Tranche A (7th session)",
         "momentum_t14": "Tranche B (14th session)", "momentum_tlast": "Tranche C (last session)",
         "momentum_parked": "One portfolio, idle cash parked (LIQUIDCASE)",
         "momentum_quality": "One portfolio + Quality 30 check"}
LT_EXEMPT, STCG, LTCG = 125_000.0, 0.20, 0.125


def rs(x: float) -> str:
    return f"{'-' if x < 0 else ''}₹{abs(x):,.0f}"


def month_bounds(month: str) -> Tuple[date, date]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt - timedelta(days=1)


def default_month(today: date) -> Tuple[str, bool]:
    """(month, complete): the previous month on the 1st-3rd, else the current month so far."""
    if today.day <= 3:
        return f"{today.replace(day=1) - timedelta(days=1):%Y-%m}", True
    return f"{today:%Y-%m}", False


def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def owner_cost(journal, strategy: str, symbol: str) -> Optional[float]:
    v = journal.get(f"owner_cost:{strategy}:{symbol}")
    return float(v) if v is not None else None


def park_symbols(cfg: dict) -> set:
    """Liquid ETFs the strategies park idle cash in (taxed as debt: at the slab rate, not as equity)."""
    out = set()
    for sc in (cfg.get("strategies") or {}).values():
        pc = (sc or {}).get("park")
        sym = pc if isinstance(pc, str) else (pc or {}).get("symbol")
        if sym:
            out.add(str(sym).upper())
    return out


def tax_estimate(journal, upto: date, debt: Optional[set] = None) -> Dict[str, float]:
    """Equity capital gains (STCG/LTCG). Trades in `debt` symbols (parked cash) are summed separately: their
    gains are taxed at your slab rate."""
    t = journal.trades()
    st = lt = slab = 0.0
    if not t.empty:
        t = t[(t["exit_time"].str[:10] >= fy_start(upto).isoformat()) & (t["exit_time"].str[:10] <= upto.isoformat())]
    for _, r in (t.iterrows() if not t.empty else []):
        if str(r["symbol"]).upper() in (debt or set()) or str(r["symbol"]).upper().startswith("LIQUID"):
            slab += r["net"]
            continue
        if str(r["entry_tag"]).startswith("adopted:"):
            cost = owner_cost(journal, r["strategy"], r["symbol"])
            gain = (r["exit_px"] - (cost if cost else r["entry_px"])) * r["qty"] - r["charges"]
            st += gain                                       # buy date unknown: counted as short-term
            continue
        held = (pd.Timestamp(r["exit_time"]) - pd.Timestamp(r["entry_time"])).days
        if r["product"] != CNC or held < 365:
            st += r["net"]
        else:
            lt += r["net"]
    st_loss, lt_loss = max(0.0, -st), max(0.0, -lt)
    st_g, lt_g = max(0.0, st), max(0.0, lt)
    use = min(st_loss, lt_g)
    lt_g -= use
    st_loss -= use
    tax = STCG * st_g + LTCG * max(0.0, lt_g - LT_EXEMPT)
    return {"short_term": st, "long_term": lt, "tax": tax, "loss_carried": st_loss + lt_loss, "slab": slab}


def strategy_rows(journal, cfg: dict, marks: Dict[str, float], start: date, end: date) -> List[dict]:
    t = journal.trades()
    names = set(t["strategy"]) if not t.empty else set()
    names = sorted(names | {p.strategy for p in journal.positions()})
    rows = []
    for name in names:
        tt = t[t["strategy"] == name] if not t.empty else t
        inm = tt[(tt["exit_time"].str[:10] >= start.isoformat()) & (tt["exit_time"].str[:10] <= end.isoformat())] \
            if not tt.empty else tt
        pos = journal.positions(strategy=name)
        unreal = sum((marks.get(p.symbol, p.avg_price) - p.avg_price) * p.qty - p.entry_charges for p in pos)
        realized_all = float(tt["net"].sum()) if not tt.empty else 0.0
        opened = sum(p.entry_charges for p in pos if start <= p.entry_time.date() <= end)
        sc = (cfg.get("strategies") or {}).get(name) or {}
        base = float(cfg["capital"]["intraday" if name == "gap_reversal" else "swing"]) * float(sc.get("capital_share", 1.0))
        rows.append(dict(strategy=name, since_start=realized_all + unreal, realized_month=float(inm["net"].sum())
                         if not inm.empty else 0.0, trades_month=len(inm), charges_month=(float(inm["charges"].sum())
                         if not inm.empty else 0.0) + opened, open_value=sum(marks.get(p.symbol, p.avg_price) * p.qty
                         for p in pos), holdings=", ".join(f"{p.symbol} {p.qty}" for p in pos[:12]), base=base))
    return rows


def nifty_change(closes: pd.Series, start: date, end: date) -> Optional[float]:
    c = closes.dropna()
    before, upto = c.loc[:pd.Timestamp(start) - pd.Timedelta(days=1)], c.loc[:pd.Timestamp(end)]
    if before.empty or upto.empty:
        return None
    return float(upto.iloc[-1] / before.iloc[-1] - 1)


def build(cfg: dict, live, paper, marks: Dict[str, float], nifty: pd.Series, month: str, complete: bool,
          lab=None, lab_cfg: Optional[dict] = None) -> str:
    start, end = month_bounds(month)
    label = datetime.strptime(month, "%Y-%m").strftime("%B %Y") + ("" if complete else " so far")
    nchg = nifty_change(nifty, start, end)
    lines = [f"📊 Monthly report - {label}"]
    tot = tot_base = 0.0
    fam = {"tranches": [0.0, 0.0], "control": [0.0, 0.0], "parked": [0.0, 0.0], "quality": [0.0, 0.0]}

    def family(name: str) -> str:                       # paper experiment families: [P&L, capital]
        for k in ("control", "parked", "quality"):
            if name.endswith(k):
                return k
        return "tranches"

    for j, title, c in ((live, "LIVE (real money)", cfg), (paper, "Paper (for comparison)", cfg),
                        (lab, "Paper experiment: momentum in 3 tranches vs one portfolio (no real orders)",
                         lab_cfg or cfg)):
        if j is None:
            continue
        rows = strategy_rows(j, c, marks, start, end)
        if not rows:
            continue
        lines.append(f"\n{title}")
        for r in rows:
            prev = j.get(f"report:{r['strategy']}:since_start:{_prev(month)}")
            first = prev is None
            pnl = r["since_start"] - (0.0 if first else float(prev))
            pct = pnl / r["base"] * 100 if r["base"] else 0.0
            if j is live:
                tot += pnl
                tot_base += r["base"]
            if j is lab:
                fam[family(r["strategy"])][0] += pnl
            name = NAMES.get(r["strategy"], r["strategy"])
            lines.append(f"• {name}: {'+' if pnl >= 0 else '-'}₹{abs(pnl):,.0f} ({pct:+.1f}% of ₹{r['base']:,.0f})"
                         + (" since the start" if first else "")
                         + f" | closed trades: {r['trades_month']}, charges ₹{r['charges_month']:,.0f}")
            if r["holdings"]:
                lines.append(f"   holding {r['holdings']} (₹{r['open_value']:,.0f})")
            if complete:
                j.put(f"report:{r['strategy']}:since_start:{month}", r["since_start"])
    for name, sc in ((lab_cfg or {}).get("strategies") or {}).items():    # a tranche all in cash still counts
        fam[family(name)][1] += float(lab_cfg["capital"]["swing"]) * float((sc or {}).get("capital_share", 1.0))
    if lab is not None and fam["control"][1]:
        cp, cb = fam["control"]
        parts = [f"one portfolio {cp / cb * 100:+.1f}%"]
        if fam["tranches"][1]:
            parts.insert(0, f"3 tranches together {fam['tranches'][0] / fam['tranches'][1] * 100:+.1f}%")
        if fam["parked"][1]:
            parts.append(f"one portfolio with idle cash parked {fam['parked'][0] / fam['parked'][1] * 100:+.1f}%")
        if fam["quality"][1]:
            parts.append(f"one portfolio with the Quality 30 check {fam['quality'][0] / fam['quality'][1] * 100:+.1f}%")
        lines.append("   " + " vs ".join(parts) + " (a few months say nothing about the edge; this run checks that "
                     "they work as designed)")
    if nchg is not None:
        lines.insert(1, f"Nifty ETF this month: {nchg:+.1%}")
    if tot_base:
        lines.append(f"\nLive total: {'+' if tot >= 0 else '-'}₹{abs(tot):,.0f} ({tot / tot_base * 100:+.1f}%)"
                     + (f" vs Nifty ETF {nchg:+.1%}" if nchg is not None else ""))
    if live is not None:
        tx = tax_estimate(live, end, park_symbols(cfg))
        fy = fy_start(end)
        lines.append(f"\nTax estimate FY {fy.year}-{(fy.year + 1) % 100:02d} so far (live trades): short-term "
                     f"{rs(tx['short_term'])}, long-term {rs(tx['long_term'])} -> about {rs(tx['tax'])} tax"
                     + (f"; losses to carry forward ₹{tx['loss_carried']:,.0f} (file your return on time)"
                        if tx["loss_carried"] > 0 else "")
                     + ". Handed-over stocks use your purchase cost. Estimate only; the ₹1.25 lakh LTCG allowance "
                       "is shared with your other investments."
                     + (f" Parked cash (liquid ETF) gains {rs(tx['slab'])}: taxed at your income-tax slab, not "
                        "included above." if tx["slab"] else ""))
    lines.append("Dividends are paid to your bank account and are not included above.")
    return "\n".join(lines)


def _prev(month: str) -> str:
    start, _ = month_bounds(month)
    return f"{start - timedelta(days=1):%Y-%m}"
