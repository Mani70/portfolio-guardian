"""Backtest swing strategies on INDstocks daily history.

Usage:
  python -m backtest.run --demo                  # offline, synthetic prices (checks the plumbing)
  python -m backtest.run                         # real history for the universe in backtest.yaml
  python -m backtest.run --strategy rsi2_pullback --years 3
  python -m backtest.run --refresh               # re-download history instead of using the cache
  python -m backtest.run --universe liquid --top 200   # all NSE stocks, top 200 by liquidity at the start

Outputs go to results/<date-time>/: summary.csv, equity.csv, trades_<strategy>.csv, equity.png.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import yaml

from .data import PriceStore, adjust_splits, resolve_codes, suspicious_jumps, update_history
from .engine import Costs, simulate
from .metrics import fd_curve, summarize
from .rotation import ROTATION_DESCRIPTIONS, ROTATION_STRATEGIES, index_trend
from .strategies import DESCRIPTIONS, ENGINE_DEFAULTS, NEEDS_RS, STRATEGIES, rs_rating
from .universe import liquid_top, nse_equity_symbols

ALL_STRATEGIES = list(STRATEGIES) + list(ROTATION_STRATEGIES) + ["index_trend"]
DESCRIPTIONS = {**DESCRIPTIONS, **ROTATION_DESCRIPTIONS}

ROOT = Path(__file__).resolve().parent.parent
WARMUP_YEARS = 1.2   # extra history downloaded so 200-day indicators are ready on day one
log = logging.getLogger("backtest")

ROWS = [("cagr", "Annual return (CAGR) %", "{:.1f}"), ("max_dd", "Worst drawdown %", "{:.1f}"),
        ("sharpe", "Sharpe ratio", "{:.2f}"), ("cagr_in_sample", "CAGR, first part %", "{:.1f}"),
        ("cagr_out_of_sample", "CAGR, last part (out-of-sample) %", "{:.1f}"),
        ("max_dd_out_of_sample", "Worst drawdown, last part %", "{:.1f}"),
        ("trades", "Trades", "{:.0f}"), ("win_rate", "Win rate %", "{:.0f}"),
        ("avg_win", "Average win %", "{:.1f}"), ("avg_loss", "Average loss %", "{:.1f}"),
        ("profit_factor", "Profit factor", "{:.2f}"), ("avg_bars", "Average hold (days)", "{:.0f}"),
        ("stops", "Exits by stop", "{:.0f}"), ("exposure", "Time invested %", "{:.0f}"),
        ("costs", "Costs paid ₹", "{:,.0f}"), ("final", "Final value ₹", "{:,.0f}")]


def load_config(path: Path) -> dict:
    if not path.exists():
        path = ROOT / "backtest.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def demo_frames(n_symbols: int = 20, years: float = 5, seed: int = 7) -> Dict[str, pd.DataFrame]:
    """Synthetic random-walk prices with trending regimes. For plumbing tests only."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=int(252 * years))
    out = {}
    for i in range(n_symbols):
        drift = np.repeat(rng.normal(0.0004, 0.0012, len(days) // 60 + 1), 60)[:len(days)]
        ret = drift + rng.normal(0, 0.017, len(days))
        close = 100 * np.exp(np.cumsum(ret))
        open_ = close * np.exp(rng.normal(0, 0.004, len(days)))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, len(days))))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, len(days))))
        out[f"DEMO{i:02d}"] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                                            "volume": 1e5}, index=pd.DatetimeIndex(days, name="date"))
    out["NIFTYBEES"] = pd.DataFrame(
        {k: np.mean([f[k].values for f in out.values()], axis=0) for k in ["open", "high", "low", "close"]},
        index=days).assign(volume=1e5)
    return out


def real_frames(cfg: dict, symbols, years: float, refresh: bool, bench: str = "") -> Dict[str, pd.DataFrame]:
    """symbols=None means every NSE equity (for --universe liquid)."""
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    from guardian.broker import IndStocksClient
    from guardian.main import instrument_index
    client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
    index = instrument_index(client, ROOT / "cache")
    if symbols is None:
        symbols = nse_equity_symbols(client) + ([bench] if bench else [])
        print(f"Liquid universe: {len(symbols)} NSE equities. The first download takes 10-20 minutes; "
              f"later runs only top up the cache.")
    codes, missing = resolve_codes(symbols, index, "NSE")
    if missing and len(missing) <= 20:
        print(f"Not found on NSE, skipped: {', '.join(missing)}")
    elif missing:
        print(f"{len(missing)} symbols not found on NSE, skipped")
    return update_history(client, codes, years, PriceStore(ROOT), refresh=refresh)


def benchmark_curve(frames, name: str, symbols, capital: float, index: pd.DatetimeIndex) -> pd.Series:
    if name and name in frames and len(frames[name]) > 1:
        c = frames[name]["close"].reindex(index).ffill().dropna()
        return capital * c / c.iloc[0]
    closes = pd.DataFrame({s: frames[s]["close"] for s in symbols if s in frames}).reindex(index).ffill()
    norm = closes / closes.bfill().iloc[0]
    return capital * norm.mean(axis=1).dropna()


def fmt_table(summaries: Dict[str, dict]) -> str:
    """One row per strategy; the full set of statistics is in summary.csv."""
    cols = [("cagr", "CAGR%", "{:.1f}"), ("max_dd", "MaxDD%", "{:.1f}"), ("sharpe", "Sharpe", "{:.2f}"),
            ("cagr_in_sample", "CAGR 1st%", "{:.1f}"), ("cagr_out_of_sample", "CAGR OOS%", "{:.1f}"),
            ("max_dd_out_of_sample", "DD OOS%", "{:.1f}"), ("trades", "Trades", "{:.0f}"),
            ("win_rate", "Win%", "{:.0f}"), ("profit_factor", "PF", "{:.2f}"),
            ("avg_bars", "Hold d", "{:.0f}"), ("costs", "Costs ₹", "{:,.0f}")]
    width = max(20, *(len(n) for n in summaries)) + 1
    lines = [f"{'':{width}}" + "".join(f"{label:>11}" for _, label, _ in cols)]
    for n, sm in summaries.items():
        cells = []
        for key, _, f in cols:
            v = sm.get(key)
            bad = v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v)))
            cells.append(f"{'—':>11}" if bad else f"{f.format(v):>11}")
        lines.append(f"{n:{width}}" + "".join(cells))
    lines.append("CAGR 1st = first 70% of the period; OOS = last 30% (out-of-sample); PF = profit factor.")
    return "\n".join(lines)

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Backtest swing strategies on INDstocks history")
    ap.add_argument("--config", default=str(ROOT / "backtest.yaml"))
    ap.add_argument("--strategy", action="append", choices=sorted(ALL_STRATEGIES),
                    help="run only this strategy (repeatable)")
    ap.add_argument("--years", type=float)
    ap.add_argument("--demo", action="store_true", help="synthetic data, no API calls")
    ap.add_argument("--refresh", action="store_true", help="re-download history")
    ap.add_argument("--keep-jumps", action="store_true",
                    help="don't trim history at unexplained >35%% one-day moves before the test start")
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--universe", choices=["config", "liquid"], default="config",
                    help="config: the list in backtest.yaml; liquid: all NSE stocks, top --top by liquidity")
    ap.add_argument("--top", type=int, default=200, help="stocks kept with --universe liquid")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)

    cfg = load_config(Path(args.config))
    years = args.years or float(cfg.get("years", 5))
    capital = float(cfg.get("capital", 500_000))
    universe = [s.upper() for s in cfg.get("universe", [])]
    bench = str(cfg.get("benchmark", "") or "").upper()

    if args.demo:
        frames = demo_frames(years=years + WARMUP_YEARS)
        universe = [s for s in frames if s != "NIFTYBEES"]
        print("DEMO MODE: synthetic prices. Results say nothing about real markets.\n")
    elif args.universe == "liquid":
        frames = real_frames(cfg, None, years + WARMUP_YEARS, args.refresh, bench)
        universe = [s for s in frames if s != bench]
    else:
        frames = real_frames(cfg, universe + ([bench] if bench else []), years + WARMUP_YEARS, args.refresh)

    # data checks
    long_enough = {s: frames[s] for s in universe if frames.get(s) is not None and len(frames[s]) >= 250}
    short = [s for s in universe if s not in long_enough]
    if short:
        print(f"Skipping {len(short)} with under 250 days of history"
              + (f": {', '.join(short)}" if len(short) <= 10 else ""))
    if not long_enough:
        print("No usable price history.")
        return 1
    full_dates = pd.DatetimeIndex(sorted(set().union(*[f.index for f in long_enough.values()])))
    # Indicators need ~200 days of history, so the extra year is warm-up only: every curve
    # (strategies, buy-and-hold, FD) starts on the same day after it.
    start = max(full_dates[-1] - pd.Timedelta(days=int(years * 365.25)), full_dates[0] + pd.Timedelta(days=300))
    usable, adjusted, trimmed, kept = {}, [], [], []
    for s, df in long_enough.items():
        df, splits = adjust_splits(df)
        if splits:
            adjusted.append(s)
        jumps = suspicious_jumps(df)
        if jumps and not args.keep_jumps:
            pre = [j for j in jumps if j[0] < start]
            if pre:
                # an unexplained jump before the test starts: keep only the data after it
                df = df.loc[pre[-1][0]:]
                trimmed.append(s)
            kept += [f"{s} {v:+.0%} on {d:%d %b %Y}" for d, v in jumps if d >= start]
        usable[s] = df
    if adjusted:
        print("Unadjusted split/bonus found and back-adjusted: "
              + (", ".join(adjusted) if len(adjusted) <= 10 else f"{len(adjusted)} stocks"))
    if trimmed:
        print("Unexplained >35% move before the test start, history trimmed to after it: "
              + (", ".join(trimmed) if len(trimmed) <= 10 else f"{len(trimmed)} stocks"))
    if kept:
        print("Kept as real moves (>35% in a day during the test; check if any is a missed split): "
              + ("; ".join(kept) if len(kept) <= 10 else f"{len(kept)} moves"))
    if args.universe == "liquid" and not args.demo:
        keep = liquid_top(usable, start, args.top)
        usable = {s: usable[s] for s in keep}
        print(f"Kept the {len(usable)} most liquid stocks as of {start:%d %b %Y} (median traded value, prior year)")
    all_dates = full_dates[full_dates >= start]
    if len(all_dates) < 120:
        print("Not enough history after the warm-up period.")
        return 1
    split = all_dates[int(len(all_dates) * (1 - float(cfg.get("out_of_sample_pct", 30)) / 100))]
    print(f"Testing {len(usable)} stocks, {all_dates[0]:%d %b %Y} to {all_dates[-1]:%d %b %Y}; "
          f"out-of-sample from {split:%d %b %Y}\n")

    costs = Costs(**(cfg.get("costs") or {}))
    params_cfg = cfg.get("strategies") or {}
    chosen = args.strategy or ALL_STRATEGIES
    cash_yield = float(cfg.get("cash_yield", 6.0))
    bench_df = frames.get(bench) if bench else None
    out_dir = ROOT / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    rs = None
    if any(n in NEEDS_RS for n in chosen):
        rs = rs_rating(pd.DataFrame({s: f["close"] for s, f in usable.items()}))
    summaries, curves = {}, {}
    for name in chosen:
        params = params_cfg.get(name) or {}
        if name in ROTATION_STRATEGIES:
            res = ROTATION_STRATEGIES[name](usable, bench_df, all_dates, capital, costs, cash_yield, **params)
        elif name == "index_trend":
            if bench_df is None or bench_df.empty:
                print("Skipping index_trend: no benchmark ETF history")
                continue
            res = index_trend(bench, bench_df, all_dates, capital, costs, cash_yield, **params)
        else:
            fn = STRATEGIES[name]
            extra = (lambda s: {"rs": rs[s]}) if name in NEEDS_RS else (lambda s: {})
            sig = {s: fn(df, **params, **extra(s)).loc[all_dates[0]:] for s, df in usable.items()}
            eng = ENGINE_DEFAULTS.get(name, {})
            res = simulate(sig, capital=capital, max_positions=int(cfg.get("max_positions", 5)), costs=costs,
                           stop_atr=float(cfg.get("stop_atr", 3.0)),
                           max_hold=int(eng.get("max_hold", cfg.get("max_hold", 0))),
                           cash_yield_pct=cash_yield, stop_pct=float(eng.get("stop_pct", 0)))
        summaries[name] = summarize(res.equity, res.trades, res.costs_paid, res.exposure, split)
        curves[name] = res.equity
        pd.DataFrame([t.__dict__ for t in res.trades]).to_csv(out_dir / f"trades_{name}.csv", index=False)

    b = benchmark_curve(frames, bench, usable, capital, all_dates)
    bench_label = bench if bench in frames else "equal-weight"
    summaries[f"{bench_label} hold"] = summarize(b, [], split=split)
    curves[f"{bench_label} hold"] = b
    if bench in frames:
        # the fair yardstick for stock-picking rules: simply holding the same stocks, equally weighted
        # (it shares their survivorship bias, which the Nifty ETF does not)
        ew = benchmark_curve(frames, "", usable, capital, all_dates)
        summaries["Equal-weight hold"] = summarize(ew, [], split=split)
        curves["Equal-weight hold"] = ew
    fd = fd_curve(all_dates, capital, float(cfg.get("fd_rate", 6.25)))
    summaries["FD"] = summarize(fd, [], split=split)
    summaries["FD"]["sharpe"] = float("nan")   # no volatility: ratio is meaningless
    curves["FD"] = fd

    print(fmt_table(summaries))
    print("\nStrategies:")
    for n in chosen:
        print(f"  {n}: {DESCRIPTIONS[n]}")
    print(f"\nIdle cash earns {cash_yield:.1f}%/yr (liquid fund) in every strategy.")
    print("\nReturns are before income tax (short-term gains: 20%). Costs and slippage are included.")
    print("Past results, even out-of-sample, don't guarantee future ones.")

    pd.DataFrame(summaries).to_csv(out_dir / "summary.csv")
    pd.DataFrame(curves).to_csv(out_dir / "equity.csv", index_label="date")
    if not args.no_chart:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(11, 5.5))
            for n, c in curves.items():
                ax.plot(c.index, c.values / 1e5, label=n, linewidth=1.6 if n in chosen else 1.0,
                        linestyle="--" if n == "FD" else "-")
            ax.axvline(split, color="grey", linewidth=0.8, linestyle=":")
            ax.text(split, ax.get_ylim()[1], " out-of-sample →", va="top", fontsize=8, color="grey")
            ax.set_ylabel("Portfolio value (₹ lakh)")
            ax.set_title("Strategy equity vs buy-and-hold and FD")
            ax.grid(alpha=0.3)
            ax.legend(loc="upper left", fontsize=8)
            fig.tight_layout()
            fig.savefig(out_dir / "equity.png", dpi=130)
        except ImportError:
            print("(Install matplotlib for the equity chart.)")
    print(f"\nSaved results to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
