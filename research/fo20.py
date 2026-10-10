"""Addendum 20 (research/PREREGISTRATION.md): defined-risk Nifty option strategies on NSE's daily F&O files.

  python research/fo20.py download        # resumable; research/data/fo/nifty_fo.csv (Nifty rows only)
  python research/fo20.py test            # writes research/fo20_results.csv

Shared with the paper trader (trader/fo_paper.py): the NSE file parsers, strike choice, cost model and the strategy
legs, so the paper run trades exactly what was tested.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

FO = ROOT / "research" / "data" / "fo"
ARCH = "https://nsearchives.nseindia.com"
UDIFF_FROM = date(2024, 7, 8)
LOT = 65                                           # today's Nifty lot (NSE's UDiFF file, Oct 2026)
ACCOUNT = 384_000.0
COLS = ["date", "kind", "expiry", "strike", "close", "settle", "traded", "oi", "lot", "spot"]


# ---------------------------------------------------------------- NSE files
def urls(d: date) -> List[str]:
    old = f"{ARCH}/content/historical/DERIVATIVES/{d:%Y}/{d.strftime('%b').upper()}/fo{d:%d}{d.strftime('%b').upper()}{d:%Y}bhav.csv.zip"
    new = f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    return [new, old] if d >= UDIFF_FROM else [old, new]


def _f(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return math.nan


def parse(rows: List[dict], d: date, symbol: str = "NIFTY") -> List[dict]:
    """Nifty futures and options rows of one day's file (old or UDiFF layout), in one shape."""
    out = []
    for r in rows:
        if "TckrSymb" in r:                                              # UDiFF (Jul 2024 on)
            if r.get("TckrSymb") != symbol or r.get("FinInstrmTp") not in ("IDO", "IDF"):
                continue
            kind = "FUT" if r["FinInstrmTp"] == "IDF" else r.get("OptnTp", "")
            exp = pd.Timestamp(r.get("XpryDt")).date()
            out.append({"date": d, "kind": kind, "expiry": exp, "strike": _f(r.get("StrkPric")) if kind != "FUT" else 0.0,
                        "close": _f(r.get("ClsPric")), "settle": _f(r.get("SttlmPric")),
                        "traded": _f(r.get("TtlTradgVol")), "oi": _f(r.get("OpnIntrst")),
                        "lot": _f(r.get("NewBrdLotQty")), "spot": _f(r.get("UndrlygPric"))})
        else:                                                            # old layout
            if (r.get("SYMBOL") or "").strip() != symbol or (r.get("INSTRUMENT") or "").strip() not in ("OPTIDX", "FUTIDX"):
                continue
            kind = "FUT" if r["INSTRUMENT"].strip() == "FUTIDX" else (r.get("OPTION_TYP") or "").strip()
            exp = pd.Timestamp(r.get("EXPIRY_DT")).date()
            out.append({"date": d, "kind": kind, "expiry": exp, "strike": _f(r.get("STRIKE_PR")),
                        "close": _f(r.get("CLOSE")), "settle": _f(r.get("SETTLE_PR")),
                        "traded": _f(r.get("CONTRACTS")), "oi": _f(r.get("OPEN_INT")), "lot": math.nan,
                        "spot": math.nan})
    return out


def sessions(start: date, end: date) -> List[date]:
    """NSE trading days, from the equity bhavcopy history (research/data/hist) and weekdays after it."""
    hist = ROOT / "research" / "data" / "hist"
    days = set()
    for p in sorted(hist.glob("equities_*.csv")):
        if int(p.stem[-4:]) >= start.year:
            days |= set(pd.read_csv(p, usecols=["date"])["date"].unique())
    out = sorted(pd.Timestamp(x).date() for x in days)
    last = out[-1] if out else start
    d = last + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return [d for d in out if start <= d <= end]


def download(start: date = date(2012, 1, 1), end: Optional[date] = None) -> None:
    import download_nse as DN
    FO.mkdir(parents=True, exist_ok=True)
    out = FO / "nifty_fo.csv"
    have = set(pd.read_csv(out, usecols=["date"])["date"].unique()) if out.exists() else set()
    missing_p = FO / "missing.txt"
    missing = set(missing_p.read_text().split()) if missing_p.exists() else set()
    f = DN.Fetcher(0.3)
    days = [d for d in sessions(start, end or date.today()) if d.isoformat() not in have and d.isoformat() not in missing]
    print(f"{len(have)} days stored, {len(days)} to fetch", flush=True)
    for k, d in enumerate(days):
        rows = None
        for u in urls(d):
            blob = f.get(u)
            if blob:
                rows = parse(DN.unzip_csv(blob), d)
                break
        if rows:
            pd.DataFrame(rows, columns=COLS).to_csv(out, mode="a", header=not out.exists(), index=False)
        elif d < date.today() - timedelta(days=3):
            with missing_p.open("a") as fh:
                fh.write(d.isoformat() + "\n")
        if k % 100 == 0:
            print(f"  {k}/{len(days)} {d}", flush=True)
    print("done", flush=True)


def load() -> pd.DataFrame:
    df = pd.read_csv(FO / "nifty_fo.csv", parse_dates=["date", "expiry"])
    return df.drop_duplicates(["date", "kind", "expiry", "strike"], keep="last")


# ---------------------------------------------------------------- costs (Addendum 20)
def stt_sell_rate(d) -> float:
    return 0.001 if pd.Timestamp(d) >= pd.Timestamp("2024-10-01") else 0.000625


def leg_cost(premium: float, qty: int, buy: bool, d) -> float:
    """Charges in rupees for one option order of qty units at premium (slippage included)."""
    value = premium * qty
    slip = max(1.0, 0.02 * premium) * qty
    exch, sebi = 0.0005 * value, value * 10 / 1e7
    stt = 0.0 if buy else stt_sell_rate(d) * value
    stamp = 0.00003 * value if buy else 0.0
    return 20 + stt + exch + sebi + stamp + 0.18 * (20 + exch + sebi) + slip


def exercise_stt(intrinsic: float, qty: int) -> float:
    return 0.00125 * intrinsic * qty


# ---------------------------------------------------------------- strategies
@dataclass
class Leg:
    kind: str          # "CE" / "PE"
    moneyness: float   # strike / spot target (0.97 = 3% below)
    side: int          # +1 bought, -1 sold


STRATEGIES: Dict[str, List[Leg]] = {
    "S1 bull put spread 3%/6%": [Leg("PE", 0.97, -1), Leg("PE", 0.94, +1)],
    "S1 neighbour 2%/5%": [Leg("PE", 0.98, -1), Leg("PE", 0.95, +1)],
    "S1 neighbour 4%/7%": [Leg("PE", 0.96, -1), Leg("PE", 0.93, +1)],
    "S2 iron condor 4%/7%": [Leg("PE", 0.96, -1), Leg("PE", 0.93, +1), Leg("CE", 1.04, -1), Leg("CE", 1.07, +1)],
    "S2 neighbour 3%/6%": [Leg("PE", 0.97, -1), Leg("PE", 0.94, +1), Leg("CE", 1.03, -1), Leg("CE", 1.06, +1)],
    "S2 neighbour 5%/8%": [Leg("PE", 0.95, -1), Leg("PE", 0.92, +1), Leg("CE", 1.05, -1), Leg("CE", 1.08, +1)],
}


def chain(day: pd.DataFrame, expiry, kind: str) -> pd.DataFrame:
    c = day[(day["expiry"] == expiry) & (day["kind"] == kind) & (day["traded"] > 0) & (day["close"] > 0)]
    return c.sort_values("strike")


def pick(day: pd.DataFrame, expiry, kind: str, target: float) -> Optional[pd.Series]:
    c = chain(day, expiry, kind)
    if c.empty:
        return None
    return c.iloc[(c["strike"] - target).abs().argsort().iloc[0]]


def monthly_expiries(df: pd.DataFrame) -> List[pd.Timestamp]:
    """The monthly expiries: the last expiry of each calendar month among futures contracts (futures are monthly)."""
    fut = sorted(df.loc[df["kind"] == "FUT", "expiry"].unique())
    by = {}
    for e in fut:
        e = pd.Timestamp(e)
        by[(e.year, e.month)] = max(by.get((e.year, e.month), e), e)
    return sorted(by.values())


def spot_series(df: pd.DataFrame) -> pd.Series:
    """Nifty 50 close: niftyindices history (research/data/hist), filled with the UDiFF underlying price."""
    import history20 as H
    ix = H.load_index(ROOT / "research" / "data" / "hist", ["nifty50"])["close"]
    u = df.dropna(subset=["spot"]).groupby("date")["spot"].first()
    return ix.combine_first(u).sort_index()


def open_position(day: pd.DataFrame, expiry, spot: float, legs: List[Leg], d) -> Optional[dict]:
    chosen = []
    for leg in legs:
        r = pick(day, expiry, leg.kind, spot * leg.moneyness)
        if r is None:
            return None
        chosen.append((leg, float(r["strike"]), float(r["close"])))
    strikes = [k for _, k, _ in chosen]
    if len(set(strikes)) < len(strikes):
        return None                                                     # strikes collapsed onto one: no spread
    credit = sum(-leg.side * px for leg, _, px in chosen) * LOT
    costs = sum(leg_cost(px, LOT, leg.side > 0, d) for leg, _, px in chosen)
    return {"legs": chosen, "credit": credit, "costs": costs, "expiry": expiry}


def settle(pos: dict, final: float) -> Tuple[float, float]:
    """(value of the legs at expiry in rupees, exercise STT)."""
    value, stt = 0.0, 0.0
    for leg, k, _ in pos["legs"]:
        intrinsic = max(0.0, final - k) if leg.kind == "CE" else max(0.0, k - final)
        value += leg.side * intrinsic * LOT
        if leg.side > 0 and intrinsic > 0:
            stt += exercise_stt(intrinsic, LOT)
    return value, stt


def max_loss(pos: dict) -> float:
    """Most a spread can lose (rupees): the widest gap between a sold and a bought strike on one side, less the credit."""
    width = 0.0
    for kind in ("PE", "CE"):
        ks = sorted(k for leg, k, _ in pos["legs"] if leg.kind == kind)
        if len(ks) == 2:
            width = max(width, (ks[1] - ks[0]) * LOT)
    return width - pos["credit"] + pos["costs"]


def run_monthly(df: pd.DataFrame, spot: pd.Series, legs_fn) -> pd.DataFrame:
    """One position a month: opened at the close of the first session after each monthly expiry, held to the next."""
    exps = monthly_expiries(df)
    dates = sorted(df["date"].unique())
    rows = []
    for e0, e1 in zip(exps[:-1], exps[1:]):
        nxt = [x for x in dates if x > e0]
        if not nxt or e1 > dates[-1]:
            continue
        d = pd.Timestamp(nxt[0])
        s = spot.get(d)
        fin = spot.get(pd.Timestamp(e1))
        if s is None or fin is None or np.isnan(s) or np.isnan(fin):
            continue
        legs = legs_fn(d, s)
        if not legs:
            continue
        pos = open_position(df[df["date"] == d], pd.Timestamp(e1), s, legs, d)
        if pos is None:
            rows.append({"open": d, "expiry": e1, "skipped": True})
            continue
        val, stt = settle(pos, fin)
        pnl = pos["credit"] + val - pos["costs"] - stt
        rows.append({"open": d, "expiry": e1, "spot": s, "final": fin, "credit": pos["credit"], "costs": pos["costs"] + stt,
                     "pnl": pnl, "risk": max_loss(pos) if any(l.side < 0 for l, _, _ in pos["legs"]) else -pos["credit"] + pos["costs"],
                     "strikes": "/".join(f"{l.kind}{k:g}{'+' if l.side > 0 else '-'}" for l, k, _ in pos["legs"]),
                     "skipped": False})
    return pd.DataFrame(rows)


def account_curve(trades: pd.DataFrame, liq: pd.Series, start, end) -> pd.Series:
    """₹3.84 lakh: each month's profit or loss lands at expiry; the account otherwise earns the liquid fund's rate."""
    idx = liq.loc[start:end].index
    growth = (1 + liq.loc[start:end].fillna(0))
    pnl = trades.set_index("expiry")["pnl"].groupby(level=0).sum().reindex(idx, fill_value=0.0)
    v, out = ACCOUNT, []
    for d in idx:
        v = v * growth.loc[d] + pnl.loc[d]
        out.append(v)
    return pd.Series(out, index=idx)


PERIODS = {"A": ("2012-02-01", "2018-12-31"), "B": ("2019-01-01", "2026-09-30")}


def weekly_pairs(df: pd.DataFrame, since="2019-02-01") -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    exps = sorted(pd.Timestamp(e) for e in df.loc[df["kind"].isin(["CE", "PE"]), "expiry"].unique()
                  if pd.Timestamp(e) >= pd.Timestamp(since))
    return list(zip(exps[:-1], exps[1:]))


def run_pairs(df: pd.DataFrame, spot: pd.Series, pairs, legs_fn) -> pd.DataFrame:
    dates = sorted(df["date"].unique())
    rows = []
    for e0, e1 in pairs:
        nxt = [x for x in dates if x > e0]
        if not nxt or e1 > dates[-1] or pd.Timestamp(nxt[0]) >= e1:
            continue
        d = pd.Timestamp(nxt[0])
        s, fin = spot.get(d), spot.get(pd.Timestamp(e1))
        if s is None or fin is None or np.isnan(s) or np.isnan(fin):
            continue
        pos = open_position(df[df["date"] == d], pd.Timestamp(e1), s, legs_fn(d, s), d)
        if pos is None:
            continue
        val, stt = settle(pos, fin)
        rows.append({"open": d, "expiry": e1, "pnl": pos["credit"] + val - pos["costs"] - stt})
    return pd.DataFrame(rows)


def summary(name: str, tr: pd.DataFrame, liq: pd.Series, liq_cagr: Dict[str, float]) -> dict:
    from backtest.metrics import cagr, max_drawdown
    row = {"strategy": name}
    for tag, (a, b) in PERIODS.items():
        x = tr[(tr["expiry"] >= a) & (tr["expiry"] <= b) & ~tr.get("skipped", False)]
        if x.empty:
            continue
        acc = account_curve(x, liq, a, b)
        sd = x["pnl"].std()
        row.update({f"months_{tag}": len(x), f"win_{tag}": (x["pnl"] > 0).mean() * 100,
                    f"mean_{tag}": x["pnl"].mean(), f"t_{tag}": x["pnl"].mean() / sd * math.sqrt(len(x)) if sd else np.nan,
                    f"worst_{tag}": x["pnl"].min(), f"worst_pct_{tag}": x["pnl"].min() / ACCOUNT * 100,
                    f"cagr_{tag}": cagr(acc), f"dd_{tag}": max_drawdown(acc), f"liq_{tag}": liq_cagr[tag],
                    f"risk_{tag}": x["risk"].median() if "risk" in x else np.nan})
    return row


def verdict(r: dict) -> bool:
    return all(r.get(f"t_{p}", 0) >= 2 and r.get(f"mean_{p}", 0) > 0 and r.get(f"dd_{p}", -100) >= -25
               and r.get(f"worst_pct_{p}", -100) >= -10 and r.get(f"cagr_{p}", 0) >= r.get(f"liq_{p}", 0) + 2
               for p in PERIODS)


def show(r: dict) -> None:
    parts = []
    for p in PERIODS:
        if f"months_{p}" in r:
            parts.append(f"{p}: {r[f'months_{p}']} m, won {r[f'win_{p}']:.0f}%, avg ₹{r[f'mean_{p}']:+,.0f} (t {r[f't_{p}']:+.2f}), "
                         f"worst month ₹{r[f'worst_{p}']:,.0f} ({r[f'worst_pct_{p}']:+.1f}%), account {r[f'cagr_{p}']:.1f}%/yr "
                         f"(liquid {r[f'liq_{p}']:.1f}%), worst fall {r[f'dd_{p}']:.1f}%")
    print(f"  {r['strategy']}\n    " + "\n    ".join(parts), flush=True)


def test() -> int:
    import allocation20 as AL
    from backtest.metrics import cagr, max_drawdown
    hist = ROOT / "research" / "data" / "hist"
    df = load()
    spot = spot_series(df)
    liq = AL.load_sleeves(hist)["LIQ"]
    liq_cagr = {p: cagr((1 + liq.loc[a:b].fillna(0)).cumprod()) for p, (a, b) in PERIODS.items()}
    print(f"F&O data: {df['date'].min():%d %b %Y} - {df['date'].max():%d %b %Y}, {df['date'].nunique()} sessions; "
          f"lot {LOT}; account ₹{ACCOUNT:,.0f}")
    rows = []
    print("\nMONTHLY, held to expiry (per lot; account = ₹3.84 lakh, one position at a time)")
    for name, legs in STRATEGIES.items():
        tr = run_monthly(df, spot, lambda d, s, legs=legs: legs)
        r = summary(name, tr, liq, liq_cagr)
        r["skipped"] = int(tr.get("skipped", pd.Series(dtype=bool)).sum())
        rows.append(r)
        show(r)
        tr.to_csv(ROOT / "research" / f"fo20_trades_{name.split()[0]}{'' if 'neighbour' not in name else '_' + name.split()[-1].replace('/', '-')}.csv", index=False)
    sma = spot.rolling(200).mean()
    trend = lambda d, s: [Leg("CE", 1.0, +1)] if s > sma.get(d, np.nan) else [Leg("PE", 1.0, +1)]   # noqa: E731
    tr = run_monthly(df, spot, trend)
    r = summary("S4 trend option buying (at-the-money)", tr, liq, liq_cagr)
    rows.append(r)
    show(r)

    print("\nS3 CRASH INSURANCE (per unit: Nifty held + a put 5% below bought every month; prices only)")
    tr = run_monthly(df, spot, lambda d, s: [Leg("PE", 0.95, +1)])
    tr = tr[~tr["skipped"]].copy()
    tr["unhedged"] = tr["final"] / tr["spot"] - 1
    tr["hedged"] = tr["unhedged"] + tr["pnl"] / LOT / tr["spot"]
    for p, (a, b) in PERIODS.items():
        x = tr[(tr["expiry"] >= a) & (tr["expiry"] <= b)]
        m_liq = (1 + liq.loc[a:b].fillna(0)).prod() ** (1 / max(len(x), 1)) - 1
        res = {}
        for col in ("unhedged", "hedged"):
            eq = (1 + x[col]).cumprod()
            ex = x[col] - m_liq
            res[col] = (cagr(pd.Series(eq.values, index=x["expiry"])), max_drawdown(pd.Series(eq.values, index=x["expiry"])),
                        ex.mean() / x[col].std() * math.sqrt(12))
        print(f"  {p}: Nifty held {res['unhedged'][0]:.1f}%/yr, worst fall {res['unhedged'][1]:.1f}%, Sharpe "
              f"{res['unhedged'][2]:.2f}; insured {res['hedged'][0]:.1f}%/yr, worst fall {res['hedged'][1]:.1f}%, Sharpe "
              f"{res['hedged'][2]:.2f}; insurance cost {-x['pnl'].mean() / LOT / x['spot'].mean() * 1200:.1f}% a year")
        rows.append({"strategy": f"S3 insurance {p}", "cagr_held": res["unhedged"][0], "dd_held": res["unhedged"][1],
                     "sh_held": res["unhedged"][2], "cagr_ins": res["hedged"][0], "dd_ins": res["hedged"][1],
                     "sh_ins": res["hedged"][2]})
    s3 = all(r_["sh_ins"] > r_["sh_held"] and r_["dd_ins"] >= r_["dd_held"] + 10
             for r_ in rows if str(r_.get("strategy", "")).startswith("S3 insurance"))

    print("\nWEEKLY versions (2019 on; reported, no bar)")
    for name in ("S1 bull put spread 3%/6%", "S2 iron condor 4%/7%"):
        legs = STRATEGIES[name]
        wk = run_pairs(df, spot, weekly_pairs(df), lambda d, s, legs=legs: legs)
        if len(wk):
            sd = wk["pnl"].std()
            print(f"  {name}, weekly: {len(wk)} weeks, won {(wk['pnl'] > 0).mean() * 100:.0f}%, avg ₹{wk['pnl'].mean():+,.0f} "
                  f"(t {wk['pnl'].mean() / sd * math.sqrt(len(wk)):+.2f}), worst week ₹{wk['pnl'].min():,.0f}, "
                  f"total ₹{wk['pnl'].sum():+,.0f}")

    print("\nVERDICTS (pre-registered bars)")
    by = {r["strategy"]: r for r in rows}
    out = []
    for main_, nbs in (("S1 bull put spread 3%/6%", ["S1 neighbour 2%/5%", "S1 neighbour 4%/7%"]),
                       ("S2 iron condor 4%/7%", ["S2 neighbour 3%/6%", "S2 neighbour 5%/8%"]),
                       ("S4 trend option buying (at-the-money)", [])):
        ok = verdict(by[main_]) and all(by[n].get(f"mean_{p}", 0) > 0 for n in nbs for p in PERIODS)
        print(f"  {main_}: {'PASS' if ok else 'FAIL'}")
        out.append({"strategy": "verdict: " + main_, "passes": ok})
    print(f"  S3 crash insurance: {'PASS' if s3 else 'FAIL'}")
    out.append({"strategy": "verdict: S3 crash insurance", "passes": s3})
    pd.DataFrame(rows + out).to_csv(ROOT / "research" / "fo20_results.csv", index=False)
    print("\nWritten research/fo20_results.csv")
    return 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "test"])
    a = ap.parse_args(argv)
    if a.cmd == "download":
        download()
        return 0
    return test()


if __name__ == "__main__":
    sys.exit(main())
