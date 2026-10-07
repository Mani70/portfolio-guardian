"""Download long price histories for research (read-only; places no orders).

  python -m backtest.fetch                       # 12 years daily + 5 years of 5-minute bars
  python -m backtest.fetch --daily-years 15 --intraday-years 3
  python -m backtest.fetch --no-intraday

Covers the universe in backtest.yaml plus a set of index/sector/gold ETFs. Data is cached in
cache/history (daily) and cache/intraday_5m (5-minute); re-running only tops up.
INDstocks serves 1 year of daily or 7 days of 5-minute bars per call, 5 instruments per call,
5 calls a second, so the first 5-minute download takes roughly 10-15 minutes.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from .data import PriceStore, resolve_codes, update_history

ROOT = Path(__file__).resolve().parent.parent
ETFS = ["NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES", "MON100", "LIQUIDBEES",
        "ITBEES", "PHARMABEES", "PSUBNKBEES", "CPSEETF"]
INTRADAY_ETFS = ["NIFTYBEES", "BANKBEES"]


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Download long daily and 5-minute histories")
    ap.add_argument("--config", default=str(ROOT / "backtest.yaml"))
    ap.add_argument("--daily-years", type=float, default=12)
    ap.add_argument("--intraday-years", type=float, default=5)
    ap.add_argument("--no-intraday", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    p = Path(args.config)
    cfg = yaml.safe_load((p if p.exists() else ROOT / "backtest.example.yaml").read_text(encoding="utf-8")) or {}
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    from guardian.broker import IndStocksClient
    from guardian.main import instrument_index
    client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
    index = instrument_index(client, ROOT / "cache")
    stocks = [s.upper() for s in cfg.get("universe", [])]
    codes, missing = resolve_codes(stocks + ETFS, index, "NSE")
    if missing:
        print(f"Not found on NSE, skipped: {', '.join(missing)}")

    print(f"Daily: {len(codes)} instruments, {args.daily_years:g} years")
    daily = update_history(client, codes, args.daily_years, PriceStore(ROOT))
    for s, df in sorted(daily.items()):
        if len(df):
            print(f"  {s:<12} {df.index[0]:%d %b %Y} -> {df.index[-1]:%d %b %Y}  ({len(df)} days)")
    if not args.no_intraday:
        icodes = {s: c for s, c in codes.items() if s in stocks or s in INTRADAY_ETFS}
        print(f"\n5-minute: {len(icodes)} instruments, {args.intraday_years:g} years (this is the slow part)")
        intra = update_history(client, icodes, args.intraday_years, PriceStore(ROOT, "intraday_5m"),
                               interval="5minute", window_days=7)
        firsts = sorted(df.index[0] for df in intra.values() if len(df))
        if firsts:
            print(f"  5-minute history starts {firsts[0]:%d %b %Y} (earliest) / {firsts[len(firsts) // 2]:%d %b %Y} (median)")
    print("\nDone. Research only: nothing was traded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
