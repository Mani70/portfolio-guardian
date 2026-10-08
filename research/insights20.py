"""Addendum 15 (research/PREREGISTRATION.md): do the daily insights ideas (trader/insights.py) have an edge?

The BUY and AVOID rules of trader/insights.py, unchanged, on every session of the survivorship-free 2010-2026
bhavcopy panel (official corporate actions applied, Addenda 12a/12b). Every number insights.facts() computes on its
400-session window is rebuilt here as rolling matrices and checked against facts() itself on sample days.

    python research/insights20.py            # writes research/insights20_results.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
import history20 as H                                              # noqa: E402
from backtest.engine import Costs                                  # noqa: E402
from trader import insights as I                                   # noqa: E402

LOAD_FROM = pd.Timestamp("2009-06-01")       # enough lookback for the first idea day of period A
PERIODS = {"A": (pd.Timestamp("2011-01-01"), pd.Timestamp("2015-12-31")),
           "B": (pd.Timestamp("2016-01-01"), pd.Timestamp("2099-12-31"))}
HORIZON, OTHER_HORIZONS = 20, (5, 60)
WINDOW = I.SESSIONS                          # the live store's window: history is counted within it
IDEA_VALUE = 100_000
COSTS = Costs(dp_per_sell=21.83)
SLIP = COSTS.slippage_pct / 100
BENCH = "NIFTYBEES"
FIELDS = ("open", "high", "low", "close", "value", "volume")


def load(hist: Path) -> Dict[str, pd.DataFrame]:
    panel = H.load_panel(hist, fields=FIELDS)
    eq = panel.pop("eq")
    keep = panel["close"].index >= LOAD_FROM
    eq = eq.loc[keep]
    for k in FIELDS:                                                 # the live store keeps EQ rows only
        panel[k] = panel[k].loc[keep].where(eq == 1).astype("float64")
    panel["raw_close"] = panel["close"].copy()
    H.adjust_prices(panel, H.official_events(hist, H.symbol_changes(hist)), use_detection=False)
    for k in ("open", "high", "low", "close"):
        panel[k] = panel[k].astype("float64")
    return panel


def matrices(panel: Dict[str, pd.DataFrame], raw_price: bool = True) -> Dict[str, pd.DataFrame]:
    """insights.facts() for every session at once (each row = facts() on the WINDOW sessions ending that day).
    raw_price: the ≥ ₹50 filter on the price the bot saw that day (False only to check against facts())."""
    R = I.RULES
    c, h, lo = panel["close"], panel["high"], panel["low"]
    pc = c.shift(1)
    m = {"close": c}
    m["day_pct"] = (c / pc - 1) * 100
    m["sma50"], m["sma200"] = c.rolling(50).mean(), c.rolling(200).mean()
    m["sma200_prev"] = m["sma200"].shift(20)
    for k in (63, 126, 252):
        m[f"r{k}"] = (c / c.shift(k) - 1) * 100
    m["vol"] = np.log(c).diff().rolling(252, min_periods=2).std() * math.sqrt(252) * 100
    m["hi52"], m["lo52"] = c.rolling(252, min_periods=1).max(), c.rolling(252, min_periods=1).min()
    tr = np.fmax(np.fmax(h - lo, (h - pc).abs()), (lo - pc).abs())
    m["atr_pct"] = tr.rolling(14).mean() / c * 100
    m["value_cr"] = panel["value"].rolling(20, min_periods=1).median() / 1e7
    m["n_hist"] = c.rolling(WINDOW, min_periods=1).count()
    b = c[BENCH]
    m["rs63"] = m["r63"].sub((b / b.shift(63) - 1) * 100, axis=0)
    px = panel["raw_close"] if raw_price else c
    m["liquid"] = c.notna() & (m["value_cr"] >= R["min_value_cr"]) & (px >= R["min_price"]) & \
        (m["n_hist"] >= R["min_history"])

    def z(x):
        x = x.where(m["liquid"])
        sd = x.std(axis=1, ddof=0)
        return x.sub(x.mean(axis=1), axis=0).div(sd.where(sd != 0), axis=0).where(sd.ne(0), x * 0, axis=0)

    score = (z(m["r126"] / m["vol"]) + z(m["r252"] / m["vol"])) / 2
    m["mom_pct"] = score.rank(axis=1, pct=True) * 100
    bb = b.dropna()
    ok = (bb > bb.rolling(200).mean()).where(bb.rolling(200).mean().notna())
    m["market_ok"] = ok.reindex(c.index).ffill()
    return m


def day_facts(m: Dict[str, pd.DataFrame], d: pd.Timestamp) -> pd.DataFrame:
    cols = ["close", "day_pct", "sma50", "sma200", "sma200_prev", "r63", "r126", "r252", "vol", "hi52", "lo52",
            "atr_pct", "value_cr", "n_hist", "rs63", "liquid", "mom_pct"]
    df = pd.DataFrame({k: m[k].loc[d] for k in cols})
    df = df[df["close"].notna()]
    df["deliv"] = df["deliv20"] = np.nan                              # not in the history: neutral in the ranking
    df.attrs["date"], df.attrs["n_liquid"] = d, int(df["liquid"].sum())
    return df


def check_against_facts(panel, n: int = 5) -> float:
    """The largest relative gap between the rolling matrices and insights.facts() on n sample days."""
    m = matrices(panel, raw_price=False)
    idx = panel["close"].index
    worst = 0.0
    for i in np.linspace(WINDOW + 30, len(idx) - 1, n).astype(int):
        win = {k: panel[k].iloc[i - WINDOW + 1:i + 1] for k in ("open", "high", "low", "close", "value", "volume")}
        win["deliv"] = pd.DataFrame(np.nan, index=win["close"].index, columns=win["close"].columns)
        f = I.facts(win)
        v = day_facts(m, idx[i])
        f, v = f.loc[v.index.intersection(f.index)], v.loc[v.index.intersection(f.index)]
        assert len(f) == len(v) and f["liquid"].equals(v["liquid"].astype(bool)), f"liquid differs on {idx[i]:%Y-%m-%d}"
        for col in ["close", "day_pct", "sma50", "sma200", "sma200_prev", "r63", "r126", "r252", "vol", "hi52",
                    "lo52", "atr_pct", "value_cr", "n_hist", "rs63", "mom_pct"]:
            a, b = f[col].astype(float), v[col].astype(float)
            assert a.isna().equals(b.isna()), f"{col}: missing values differ on {idx[i]:%Y-%m-%d}"
            gap = ((a - b).abs() / b.abs().clip(lower=1)).max()
            worst = max(worst, 0.0 if math.isnan(gap) else float(gap))
        print(f"  {idx[i]:%d %b %Y}: {len(f)} stocks, {f.attrs['n_liquid']} liquid - matches facts()")
    return worst


def ideas(m: Dict[str, pd.DataFrame], days) -> pd.DataFrame:
    """screen()'s BUY, WATCH (BUY blocked by the market filter) and AVOID lists on each day (no filings check)."""
    rows = []
    for d in days:
        df = day_facts(m, d)
        mk = m["market_ok"].loc[d]
        rep = I.screen(df, {"ok": None if pd.isna(mk) else bool(mk), "why": ""}, {})
        for side, part in (("BUY", rep.buys), ("WATCH", rep.watch), ("AVOID", rep.avoids)):
            rows += [{"date": d, "side": side, "symbol": s} for s in part.index]
        rows.append({"date": d, "side": "_liquid", "symbol": int(df["liquid"].sum())})
    return pd.DataFrame(rows)


def forward(c: pd.DataFrame, h: int):
    """Gross return bought at the next close, sold h sessions later (a stock that stops trading: its last close)."""
    entry = c.shift(-1)
    exit_ = c.ffill(limit=h).shift(-(h + 1))
    stopped = c.shift(-(h + 1)).isna() & exit_.notna()
    return exit_ / entry - 1, stopped


def net(r: np.ndarray) -> np.ndarray:
    after_slip = (1 + r) * (1 - SLIP) / (1 + SLIP) - 1
    charges = (COSTS.buy(IDEA_VALUE) + np.vectorize(COSTS.sell)(IDEA_VALUE * (1 + r))) / IDEA_VALUE
    return after_slip - charges


def nw_t(x: pd.Series, pos: pd.Series, lags: int) -> float:
    """Newey-West t of the mean; pos = session numbers, so autocovariances pair observations k sessions apart."""
    x = x.to_numpy(float)
    n = len(x)
    if n < 10:
        return float("nan")
    full = np.zeros(int(pos.max()) + 1)
    full[pos.to_numpy(int)] = x - x.mean()
    var = (full * full).sum() / n
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * (full[:-k] * full[k:]).sum() / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def evaluate(ix: pd.DataFrame, c: pd.DataFrame, nifty: pd.Series, liquid: pd.DataFrame, h: int) -> List[dict]:
    fwd, stopped = forward(c, h)
    bench = nifty.shift(-(h + 1)) / nifty.shift(-1) - 1
    uni = fwd.where(liquid).mean(axis=1)                              # buying any liquid stock that day
    pos = pd.Series(np.arange(len(c.index)), index=c.index)
    out = []
    for side in ("BUY", "WATCH", "AVOID"):
        t = ix[ix["side"] == side].copy()
        if t.empty:
            continue
        ri, ci = c.index.get_indexer(t["date"]), c.columns.get_indexer(t["symbol"])
        t["r"] = fwd.to_numpy()[ri, ci]
        t["stopped"] = stopped.to_numpy()[ri, ci]
        t = t[np.isfinite(t["r"])]
        t["bench"], t["uni"] = bench.loc[t["date"]].to_numpy(), uni.loc[t["date"]].to_numpy()
        t = t[np.isfinite(t["bench"])]
        t["r_net"] = net(t["r"].to_numpy())
        for per, (a, b) in PERIODS.items():
            p = t[(t["date"] >= a) & (t["date"] <= b)]
            if p.empty:
                continue
            g = p.groupby("date")
            day = pd.DataFrame({"net_x": g["r_net"].mean() - g["bench"].first(),
                                "gross_x": g["r"].mean() - g["bench"].first(),
                                "uni_x": g["r"].mean() - g["uni"].first(),
                                "pos": pos.loc[g.size().index]})
            lag = h - 1
            out.append({"side": side, "period": per, "horizon": h, "from": f"{p['date'].min():%Y-%m-%d}",
                        "to": f"{p['date'].max():%Y-%m-%d}", "idea_days": len(day), "ideas": len(p),
                        "stopped_trading": int(p["stopped"].sum()),
                        "mean_net_x_pct": day["net_x"].mean() * 100, "t_net_x": nw_t(day["net_x"], day["pos"], lag),
                        "mean_gross_x_pct": day["gross_x"].mean() * 100,
                        "t_gross_x": nw_t(day["gross_x"], day["pos"], lag),
                        "mean_uni_x_pct": day["uni_x"].mean() * 100, "t_uni_x": nw_t(day["uni_x"], day["pos"], lag),
                        "hit_rate_pct": float((p["r_net"] > p["bench"]).mean() * 100) if side != "AVOID"
                        else float((p["r"] < p["bench"]).mean() * 100),
                        "mean_idea_pct": p["r"].mean() * 100, "mean_nifty_pct": p["bench"].mean() * 100})
    return out


def verdicts(res: pd.DataFrame) -> List[str]:
    r = res[res["horizon"] == HORIZON].set_index(["side", "period"])

    def bar(side, mean, t, sign):
        ok = all((side, p) in r.index and sign * r.at[(side, p), mean] > 0 and sign * r.at[(side, p), t] >= 2
                 for p in PERIODS)
        return "PASS" if ok else "FAIL"

    return [f"BUY has an edge (beats the Nifty 50 after costs, t >= 2, A and B): {bar('BUY', 'mean_net_x_pct', 't_net_x', 1)}",
            f"BUY beats buying any liquid stock (t >= 2, A and B): {bar('BUY', 'mean_uni_x_pct', 't_uni_x', 1)}",
            f"AVOID is a useful warning (lags the Nifty 50, t <= -2, A and B): "
            f"{bar('AVOID', 'mean_gross_x_pct', 't_gross_x', -1)}"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default=str(ROOT / "research" / "data" / "hist"))
    a = ap.parse_args(argv)
    hist = Path(a.hist)
    print("Loading the bhavcopy panel ...", flush=True)
    panel = load(hist)
    c = panel["close"]
    print(f"  {c.shape[0]} sessions {c.index.min():%d %b %Y} - {c.index.max():%d %b %Y}, {c.shape[1]} symbols")
    print("Checking the rolling matrices against insights.facts() ...", flush=True)
    worst = check_against_facts(panel)
    print(f"  largest relative gap {worst:.1e}")
    if worst > 1e-6:
        print("FAILED: the vectorised rules do not match insights.facts(); no results.")
        return 1
    m = matrices(panel)
    last = len(c.index) - 1 - (HORIZON + 1)
    days = [d for d in c.index[:last + 1] if d >= PERIODS["A"][0]]
    print(f"Screening {len(days)} days ...", flush=True)
    ix = ideas(m, days)
    nifty = H.load_index(hist, ["nifty50"])["close"].reindex(c.index)
    rows = []
    for h in (HORIZON, *OTHER_HORIZONS):
        rows += evaluate(ix, c, nifty, m["liquid"], h)
    res = pd.DataFrame(rows)
    res.to_csv(ROOT / "research" / "insights20_results.csv", index=False)
    liq = ix[ix["side"] == "_liquid"].set_index("date")["symbol"].astype(int)
    per_day = ix[ix["side"] != "_liquid"].groupby(["date", "side"]).size().unstack(fill_value=0).reindex(
        liq.index, fill_value=0)
    print(f"\nLiquid stocks a day: {liq.loc[:'2015'].median():.0f} (A), {liq.loc['2016':].median():.0f} (B)")
    for side in ("BUY", "WATCH", "AVOID"):
        if side in per_day:
            s = per_day[side]
            print(f"{side}: ideas on {(s > 0).mean() * 100:.0f}% of days, {s[s > 0].mean():.1f} a day when any")
    pd.set_option("display.width", 220)
    show = ["side", "period", "horizon", "idea_days", "ideas", "stopped_trading", "mean_idea_pct", "mean_nifty_pct",
            "mean_net_x_pct", "t_net_x", "mean_gross_x_pct", "t_gross_x", "mean_uni_x_pct", "t_uni_x", "hit_rate_pct"]
    print(res[show].round(2).to_string(index=False))
    print()
    for v in verdicts(res):
        print(v)
    print("\nWritten research/insights20_results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
