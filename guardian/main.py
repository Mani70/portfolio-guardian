"""Portfolio guardian: checks your INDstocks holdings against rules in config.yaml and alerts you.

Usage:
  python -m guardian.main --mock            # offline test with sample data
  python -m guardian.main --dry-run         # real data, print alerts, don't send Telegram
  python -m guardian.main                   # real run (schedule this after 15:30 IST)
  python -m guardian.main --summary         # also print portfolio weights
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import yaml

from .notifier import Notifier, format_alerts
from .rules import IST, MarketData, Rule, evaluate_all, portfolio_weights
from .state import AlertState

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("exchange_prefix", "NSE")
    cfg.setdefault("sectors", {})
    cfg["rules"] = [Rule.from_dict(r) for r in cfg.get("rules", [])]
    ids = [r.id for r in cfg["rules"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Rule ids in config.yaml must be unique")
    cfg["sectors"] = {k.upper(): v for k, v in cfg["sectors"].items()}
    return cfg


def instrument_index(client, cache_dir: Path) -> Dict[str, Dict[str, str]]:
    """{exchange: {symbol: security_id}} for cash-market equities, cached for the day."""
    import json
    from datetime import date
    cache = cache_dir / f"equity_index_{date.today():%Y%m%d}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    rank = {"EQ": 0, "BE": 1, "BZ": 2}
    best: Dict[str, Dict[str, tuple]] = {}
    for row in client.equity_instruments():
        ex = (row.get("EXCH") or "").strip().upper()
        sid = (row.get("SECURITY_ID") or "").strip()
        if not ex or not sid or (row.get("EXPIRY_CODE") or "0").strip() not in ("0", ""):
            continue
        r = rank.get((row.get("SERIES") or "").strip().upper(), 5)
        names = {(row.get("SYMBOL_NAME") or "").strip().upper(), (row.get("TRADING_SYMBOL") or "").strip().upper()}
        names |= {n.split("-")[0] for n in names if n}
        for n in filter(None, names):
            if n not in best.setdefault(ex, {}) or r < best[ex][n][0]:
                best[ex][n] = (r, sid)
    index = {ex: {n: v[1] for n, v in m.items()} for ex, m in best.items()}
    cache_dir.mkdir(exist_ok=True)
    for old in cache_dir.glob("equity_index_*.json"):
        old.unlink()
    cache.write_text(json.dumps(index), encoding="utf-8")
    return index


def gather(client, rules: List[Rule], now: datetime) -> MarketData:
    holdings = client.holdings()
    # INDstocks' holdings security_id is sometimes the NSE token and sometimes the BSE one, with no
    # exchange field. So map by symbol through the (daily-cached) instrument master, preferring NSE.
    try:
        index = instrument_index(client, ROOT / "cache")
    except Exception as e:  # master unavailable: fall back to the raw ids
        logging.getLogger("guardian").warning("Instrument master unavailable (%s); using holding ids", e)
        index = {}
    pref = client.exchange_prefix
    other = "BSE" if pref == "NSE" else "NSE"
    scrip_of: Dict[str, str] = {}
    for h in holdings:
        for ex in (pref, other):
            if h.symbol in index.get(ex, {}):
                scrip_of[h.symbol] = f"{ex}_{index[ex][h.symbol]}"
                break
        else:
            scrip_of[h.symbol] = client.scrip_code(h.security_id)
    for r in rules:
        if r.symbol and r.scrip:
            scrip_of[r.symbol] = client.scrip_code(r.scrip)

    ltp = client.ltp(scrip_of.values())

    # Holdings carry a bare security_id with no exchange, and it isn't always the NSE token.
    # For anything unpriced, look the symbol up in the instrument master (NSE first, then BSE),
    # and finally try the holding's id with the other exchange prefix.
    overridden = {r.symbol for r in rules if r.symbol and r.scrip}
    missing = [h for h in holdings if scrip_of[h.symbol] not in ltp and h.symbol not in overridden]
    if missing:
        index = instrument_index(client, ROOT / "cache")
        alt = "BSE" if client.exchange_prefix == "NSE" else "NSE"
        candidates: Dict[str, List[str]] = {}
        for h in missing:
            c = [f"{ex}_{index[ex][h.symbol]}" for ex in ("NSE", "BSE") if h.symbol in index.get(ex, {})]
            c.append(client.scrip_code(h.security_id, alt))
            candidates[h.symbol] = [x for x in dict.fromkeys(c) if x != scrip_of[h.symbol]]
        found = client.ltp([x for cs in candidates.values() for x in cs])
        for sym, cs in candidates.items():
            for code in cs:
                if code in found:
                    scrip_of[sym], ltp[code] = code, found[code]
                    logging.getLogger("guardian").info("Resolved %s -> %s", sym, code)
                    break
    unpriced = [s for s, c in scrip_of.items() if c not in ltp]
    if unpriced:
        logging.getLogger("guardian").warning(
            "No live price for: %s. Add a 'scrip:' override for these in config.yaml.", ", ".join(unpriced))

    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=364)).timestamp() * 1000)  # API max: 1 year per call
    need_daily = {scrip_of[r.symbol] for r in rules
                  if r.symbol in scrip_of and (r.type == "off_52w_high" or
                                               (r.type.startswith("close_") and r.timeframe == "day"))}
    need_weekly = {scrip_of[r.symbol] for r in rules
                   if r.symbol in scrip_of and r.type.startswith("close_") and r.timeframe == "week"}
    daily = client.candles_many(sorted(need_daily), "1day", start_ms, end_ms) if need_daily else {}
    weekly = client.candles_many(sorted(need_weekly), "1week", start_ms, end_ms) if need_weekly else {}
    return MarketData(holdings, ltp, daily, weekly, scrip_of)


def summary_text(md: MarketData, sectors: Dict[str, str]) -> str:
    w = portfolio_weights(md)
    lines = ["Portfolio weights (stocks only):"]
    for sym, pct in sorted(w.items(), key=lambda kv: -kv[1]):
        h = next(h for h in md.holdings if h.symbol == sym)
        price = md.ltp.get(md.scrip_of.get(sym, ""))
        pnl = f"{(price / h.avg_price - 1) * 100:+.1f}%" if price and h.avg_price else "n/a"
        lines.append(f"  {sym:<12} {pct:5.1f}%  P&L {pnl:>7}  [{sectors.get(sym, 'Unclassified')}]")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Alert-only portfolio rule checker for INDstocks")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--mock", action="store_true", help="use sample_data/ instead of the live API")
    ap.add_argument("--dry-run", action="store_true", help="print alerts but don't send Telegram or save state")
    ap.add_argument("--repeat", action="store_true", help="resend alerts that already fired")
    ap.add_argument("--summary", action="store_true", help="print portfolio weights too")
    ap.add_argument("--now", help="override current time, ISO format (testing)")
    ap.add_argument("--check-auth", action="store_true", help="only verify the access token and exit")
    ap.add_argument("--show-holdings", action="store_true",
                    help="print raw holdings (symbol, security_id, ISIN) and exit; for troubleshooting")
    ap.add_argument("--debug-quotes", action="store_true",
                    help="query each holding's price one code at a time and print the raw API reply")
    args = ap.parse_args(argv)

    # Windows consoles often default to cp1252, which can't print ₹ or emoji. Print UTF-8 and
    # replace anything the terminal can't show instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(ROOT / "logs" / "guardian.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stderr)])
    log = logging.getLogger("guardian")

    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.yaml"
        log.warning("config.yaml not found; using config.example.yaml")
    cfg = load_config(cfg_path)
    now = datetime.fromisoformat(args.now).replace(tzinfo=IST) if args.now else datetime.now(IST)

    if args.mock:
        from .mock import MockClient
        client = MockClient(ROOT / "sample_data", cfg["exchange_prefix"])
    else:
        from .broker import IndStocksClient
        client = IndStocksClient.from_env(cfg["exchange_prefix"], token_cache=ROOT / ".token_cache.json")

    if args.check_auth:
        try:
            p = client.profile()
        except Exception as e:
            print(f"Authentication failed: {e}")
            return 2
        print(f"Authenticated as {p.get('first_name', '')} {p.get('last_name', '')} (UCC {p.get('ucc', '?')}).")
        return 0

    if args.debug_quotes:
        index = instrument_index(client, ROOT / "cache")
        for h in client.holdings():
            print(f"\n{h.symbol}  security_id={h.security_id!r}  "
                  f"master: NSE={index.get('NSE', {}).get(h.symbol)!r} BSE={index.get('BSE', {}).get(h.symbol)!r}")
            for code in dict.fromkeys([f"NSE_{h.security_id}", f"BSE_{h.security_id}"] +
                                      [f"{ex}_{index[ex][h.symbol]}" for ex in ("NSE", "BSE")
                                       if h.symbol in index.get(ex, {})]):
                print(f"  {code:<14} {client.raw_ltp(code)}")
        return 0

    if args.show_holdings:
        print(f"{'symbol':<14}{'security_id':<14}{'isin':<14}{'qty':>8}")
        for h in client.holdings():
            print(f"{h.symbol:<14}{h.security_id:<14}{h.isin:<14}{h.qty:>8g}")
        return 0

    notifier = Notifier(dry_run=args.dry_run or args.mock)
    try:
        md = gather(client, cfg["rules"], now)
    except Exception as e:  # report failures instead of failing silently in a scheduler
        log.exception("Data fetch failed")
        notifier.send(f"Portfolio guardian could not run: {e}")
        return 2

    alerts = evaluate_all(cfg["rules"], md, cfg["sectors"], now)
    state_path = ROOT / ("state.mock.json" if args.mock else "state.json")
    state = AlertState(state_path)
    new, cleared = state.diff(alerts, repeat=args.repeat)

    if args.summary:
        print(summary_text(md, cfg["sectors"]))
    if new:
        notifier.send(format_alerts(new, f"Portfolio guardian — {now:%d %b %Y %H:%M} IST"))
    else:
        log.info("No new alerts (%d active)", len(alerts))
    for rid in cleared:
        log.info("Cleared: %s", rid)
    if not args.dry_run:
        state.save(alerts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
