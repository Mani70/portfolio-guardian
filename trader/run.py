"""Command line for the trading engine.

  python -m trader.run check          # token, funds, instruments, which strategies may trade live
  python -m trader.run preview        # dry run on live data: what the swing strategies would buy now
  python -m trader.run swing-plan     # after 15:35: decide month-end/swing orders (AMO for tomorrow's open)
  python -m trader.run swing-check    # ~09:30: record fills, re-price or cancel what didn't fill
  python -m trader.run intraday       # 09:10-15:30: scan every 5 minutes, trade only strong signals
  python -m trader.run status         # positions, today's orders, P&L, gate status
  python -m trader.run cancel         # cancel every open order (and tomorrow's plan)
  python -m trader.run flatten        # exit all intraday positions now
  python -m trader.run watch          # market hours: alerts on sharp falls / exit levels in all holdings
  python -m trader.run adopt          # hand your existing stocks to momentum_rotation (asks first)
  python -m trader.run transfer --from trend_allocation --to core_allocation   # move the bot's positions (asks)
  python -m trader.run retest         # autopilot: the yearly re-test of its rule (January; --force any time)
  python -m trader.run report         # P&L per strategy vs the Nifty ETF, charges, tax estimate -> Telegram
  python -m trader.run reconcile      # live positions vs real INDstocks holdings (--fix to correct the record)
  python -m trader.run keep --stock X --qty N   # keep N shares out of the bot's pending sell of X (after 16:00)
  python -m trader.run holidays       # refresh NSE's trading holidays (month-end = the real last session)
  python -m trader.run test-order     # on the server: 1-unit order far from the price, then cancel (AMO after hours)

Paper trading always runs. Live orders are sent only when trader.yaml says mode: live AND the
strategy has live: true AND its paper record passed the gates (or override_gate) AND there is no
STOP file. Add --dry-run to print messages instead of sending Telegram.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time as _time
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List

from . import config as C
from .brokers.base import CostModel
from .brokers.paper import PaperBroker
from .engine import Engine
from .gates import paper_gate, stats
from .journal import Journal
from .market import LiveMarket
from .models import CANCELLED, CNC, INTRADAY, OPEN, PARTIAL, NEW, REJECTED, SELL, UNKNOWN, Signal
from .strategies import build

log = logging.getLogger("trader")
ROOT = C.ROOT


def now_ist() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)


def _client():
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    from guardian.broker import IndStocksClient
    return IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")


def build_engines(cfg: dict, dry_run: bool) -> List[Engine]:
    from guardian.notifier import Notifier
    from .instruments import Instruments
    client = _client()
    inst = Instruments.load(client, ROOT / "cache")
    market = LiveMarket(client, inst, ROOT)
    notifier = Notifier(dry_run=dry_run)
    universe = C.universe(cfg)
    bench = cfg.get("benchmark", "NIFTYBEES")
    jpath = ROOT / cfg["journal"]
    if cfg.get("_autopilot"):                             # retired strategies' holdings go to the core first
        from .autopilot import handover
        moved = handover(cfg, jpath)
        if moved:
            notifier.send("\n".join(moved))
    engines = [Engine(cfg, Journal(jpath, "paper"), PaperBroker(market, CostModel()), market, inst, notifier,
                      build(cfg), ROOT, universe, bench)]
    lj = Journal(jpath, "live")
    open_live = bool(lj.positions() or lj.active_orders())
    if cfg["mode"] == "live" or open_live:
        # mode: paper with live orders/positions left: a frozen live engine still tracks and cancels them,
        # but sends nothing new (the owner's off switch)
        from .brokers.indstocks import IndStocksBroker
        live = Engine(cfg, lj, IndStocksBroker(client, CostModel(), float(cfg["limits"]["order_rate_per_sec"])),
                      market, inst, notifier, build(cfg), ROOT, universe, bench, frozen=cfg["mode"] != "live")
        for name, why in live.skipped.items():
            log.info("LIVE: %s stays on paper: %s", name, why)
        if live.strategies or live.exit_only or open_live:
            engines.append(live)
        else:
            lj.close()
    else:
        lj.close()
    try:                                                  # paper-only experiments (trader/experiments.yaml), last
        lab = C.lab(cfg)
        if lab:
            engines.append(Engine(lab, Journal(jpath, "lab"), PaperBroker(market, CostModel()), market, inst,
                                  notifier, build(lab), ROOT, universe, bench, quiet=True))
    except Exception:                                     # noqa: BLE001 - never let an experiment stop real trading
        log.exception("lab engine not started")
    return engines


def _real(engines):
    """The paper and live engines (commands that act on orders leave the experiments alone)."""
    return [e for e in engines if e.mode != "lab"]


def _run_each(engines, step) -> None:
    """step(engine) for every engine; an experiment's (lab) failure is logged and never stops the others."""
    for eng in engines:
        if eng.mode != "lab":
            step(eng)
            continue
        try:
            step(eng)
        except Exception as e:                            # noqa: BLE001
            log.exception("lab (paper experiment) step failed; real trading not affected")
            try:                                          # say so once a day, so a broken experiment is noticed
                key = f"lab_failed:{datetime.now():%Y-%m-%d}"
                if not eng.j.get(key) and eng.notifier:
                    eng.j.put(key, True)
                    eng.notifier.send(f"[LAB] Paper experiment failed ({str(e)[:150]}); real trading not affected. "
                                      "See logs/trader.log.")
            except Exception:                             # noqa: BLE001
                pass


def cmd_check(cfg, args) -> int:
    client = _client()
    prof = client.profile()
    print(f"Token OK for {prof.get('name') or prof.get('client_id') or 'your account'}.")
    if prof.get("is_ddpi_active") is False:
        print("WARNING: DDPI is not active. Automatic SELL orders of delivery holdings will be rejected without a "
              "CDSL TPIN authorisation. Activate DDPI in the INDmoney app.")
    elif prof.get("is_ddpi_active"):
        print("DDPI active: automatic delivery sells are allowed.")
    from .instruments import Instruments
    inst = Instruments.load(client, ROOT / "cache")
    for s in ("RELIANCE", "NIFTYBEES", "GOLDBEES", "MON100"):
        print(f"  {s:<10} security_id {inst.security_id(s)}  tick ₹{inst.tick(s)}")
    try:
        from .brokers.indstocks import IndStocksBroker
        b = IndStocksBroker(client)
        print(f"Funds available: delivery ₹{b.available_funds(CNC) or 0:,.0f}, intraday ₹{b.available_funds(INTRADAY) or 0:,.0f}")
    except Exception as e:                                    # noqa: BLE001
        print(f"Funds check failed: {e}")
    print(f"\nMode: {cfg['mode']}  (config: {cfg['_path']})")
    jp = ROOT / cfg["journal"]
    for s in build(cfg):
        sc = cfg["strategies"].get(s.name) or {}
        ok, why, st = paper_gate(jp, s.name, s.engine, cfg)
        print(f"  {s.name:<20} {s.engine:<8} live={'yes' if sc.get('live') else 'no ':<3} gate: {why}")
    print("\nLive orders also need your static IP whitelisted on indstocks.com/app/api-trading/access-tokens.")
    return 0


def cmd_status(cfg, args) -> int:
    jp = ROOT / cfg["journal"]
    for mode in ("paper", "live", "lab"):
        j = Journal(jp, mode)
        pos = j.positions()
        t = j.trades()
        if not pos and t.empty and not j.load_orders():
            j.close()
            continue
        print(f"=== {mode.upper()} ===")
        for p in pos:
            print(f"  {p.strategy:<20} {p.symbol:<12} {p.product:<8} qty {p.qty:>6} @ ₹{p.avg_price:,.2f}"
                  + (f" stop ₹{p.stop:,.2f}" if p.stop else ""))
        today = now_ist().date()
        for o in j.load_orders(day=today):
            print(f"  order {o.req.tag}: {o.status} {o.req.side} {o.req.qty} @ ₹{o.req.limit_price} {o.message[:60]}")
        if not t.empty:
            for name, g in t.groupby("strategy"):
                s = stats(g)
                print(f"  {name:<20} {s['trades']} trades, net ₹{s['net']:,.0f}, win {s['win']:.0f}%, t {s['t']:.2f}")
        j.close()
    return 0


def cmd_cancel(cfg, args) -> int:
    """Cancel every open order; sells the bot is holding back or would retry are dropped too (the strategies
    decide again at their next run).
    --forget (after 15:35 only; today's orders are not touched): an order from an earlier day that INDstocks can't
    cancel and no longer lists in its order book is marked cancelled here, and a sell among them is re-sent at the
    next morning check."""
    now = now_ist()
    if args.forget and now.time() < time(15, 35):
        print("--forget only after 15:35, when no earlier order can still trade today. Nothing changed.")
        return 1
    for eng in _real(build_engines(cfg, args.dry_run)):
        n = 0
        book = None
        if args.forget and hasattr(eng.broker, "_order_book"):
            try:
                book = {str(r.get("id")) for r in eng.broker._order_book()}
            except Exception as e:                               # noqa: BLE001
                print(f"{eng.mode}: order book unavailable ({e}); nothing is forgotten")
                book = False
        for o in eng.j.load_orders(statuses=[NEW, OPEN, PARTIAL]):
            if args.forget and o.req.created.date() >= now.date():
                continue                                         # --forget leaves today's orders alone
            eng.broker.cancel(o, now)
            if (args.forget and book is not False and o.status in (NEW, OPEN, PARTIAL)
                    and (o.message or "").startswith("cancel failed") and o.req.created.date() < now.date()
                    and (book is None or o.broker_id not in book)):
                o.status, o.message = CANCELLED, f"marked cancelled by hand ({o.message})"[:300]
                if o.req.kind == "exit":
                    eng.j.put(f"ext_cancel:{o.req.tag}", now.isoformat())
                print(f"{eng.mode}: {o.req.side} {o.req.symbol} ({o.req.tag}) marked cancelled")
            eng.j.save_order(o)
            n += 1
        if not args.forget:
            eng.j.put("cancel_all_at", now.isoformat())         # held sells and retries from before now are dropped
        print(f"{eng.mode}: cancelled {n} open orders")
    return 0


def cmd_flatten(cfg, args) -> int:
    for eng in _real(build_engines(cfg, args.dry_run)):
        eng.square_off(now_ist())
        print(f"{eng.mode}: intraday positions left: {len(eng.j.positions(product=INTRADAY))}")
    return 0


def _uses_indices(cfg) -> bool:
    lab = C.lab(cfg) or {}
    return any((sc or {}).get("params", {}).get("quality_filter")
               for c in (cfg, lab) for sc in (c.get("strategies") or {}).values())


def _update_indices(today, max_days: int = 15) -> str:
    from .holidays import is_session, load as load_holidays
    from .index_data import update
    hol = load_holidays(ROOT)
    return update(ROOT, today, max_days=max_days, is_session=lambda d: is_session(d, hol))


def cmd_indices(cfg, args) -> int:
    """Refresh the NSE index closes strategies read (trader/index_data.py); backfills ~300 sessions if short."""
    from .index_data import CHAINS, load
    short = any(len(load(ROOT, k)) < 200 for k in CHAINS)
    print(_update_indices(now_ist().date(), max_days=440 if short else 15))
    return 0


def cmd_swing_plan(cfg, args) -> int:
    now = now_ist()
    if now.time() < time(15, 35) and not args.force:
        print("Run after 15:35 (the session's closing price is needed). Use --force to override.")
        return 1
    if _uses_indices(cfg):
        try:
            log.info("NSE index closes: %s", _update_indices(now.date()))
        except Exception as e:                            # noqa: BLE001 - the filter then uses what it has
            log.warning("NSE index closes not updated: %s", e)
    def plan(eng):
        for s in eng.swing_strategies():       # a strategy that never traded starts now, not at month-end
            s.params["_start_now"] = True
        eng.swing_plan(now)
    _run_each(build_engines(cfg, args.dry_run), plan)
    return 0


def cmd_swing_check(cfg, args) -> int:
    engines = build_engines(cfg, args.dry_run)
    now = now_ist()
    bench = cfg.get("benchmark", "NIFTYBEES")
    if now.weekday() >= 5 or not len(engines[0].market.today_bars([bench], now).get(bench, [])):
        print("No trading session today (weekend/holiday or before 09:20): nothing to check.")
        return 0
    _run_each(engines, lambda eng: eng.swing_check(now))
    return 0


def cmd_intraday(cfg, args) -> int:
    engines = _real(build_engines(cfg, args.dry_run))
    engines = [e for e in engines if e.intraday_strategies()]
    if not engines:
        print("No intraday strategies enabled.")
        return 0
    day = now_ist().date()
    if day.weekday() >= 5:
        print("Weekend: no session.")
        return 0
    for e in engines:
        e.intraday_start(day, now_ist())
    stop_at = time(15, 25)
    seen_bars = False
    said = {}
    while True:
        now = now_ist()
        nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
        wake = max(nxt + timedelta(seconds=20), datetime.combine(day, time(9, 20, 20)))
        _time.sleep(max(1.0, (wake - now_ist()).total_seconds()))
        now = now_ist()
        for e in engines:                              # one engine's failure must not stop the other
            try:
                e.intraday_step(now)
            except Exception as ex:                    # noqa: BLE001
                log.exception("Intraday step failed (%s)", e.mode)
                key = (e.mode, str(ex)[:200])
                if key not in said or (now - said[key]).total_seconds() >= 3600:
                    said[key] = now                    # the same error at most once an hour on Telegram
                    e._say(f"Intraday step error: {ex}", force=True)
        try:
            if not seen_bars:
                bars = engines[0].market.today_bars(["NIFTYBEES"], now)
                seen_bars = bool(len(bars.get("NIFTYBEES", [])))
                if not seen_bars and now.time() >= time(9, 35):
                    print("No market data by 09:35: exchange holiday? Stopping.")
                    break
        except Exception as ex:                               # noqa: BLE001 - keep the loop alive
            log.exception("Holiday check failed: %s", ex)
        if now.time() >= stop_at:
            break
        if now.time() >= time(15, 16) and all(e._squared for e in engines):
            break
    for e in engines:
        e.intraday_end(now_ist())
    return 0


def cmd_preview(cfg, args) -> int:
    """Dry run of the whole swing path on live data: what each strategy would hold if today were month-end.
    Uses a throwaway journal and the paper broker, so nothing is saved and nothing is sent."""
    import tempfile
    from .instruments import Instruments
    client = _client()
    inst = Instruments.load(client, ROOT / "cache")
    market = LiveMarket(client, inst, ROOT)
    bench = cfg.get("benchmark", "NIFTYBEES")
    strategies = [s for s in build(cfg) if s.engine == "swing"]
    for s in strategies:
        s.params["_force"] = True
    last = market.daily([bench], now_ist())[bench].index[-1].date()
    when = datetime.combine(last, time(15, 45))
    j = Journal(Path(tempfile.mkdtemp()) / "preview.db", "paper")
    eng = Engine(cfg, j, PaperBroker(market, CostModel()), market, inst, None, strategies, ROOT,
                 C.universe(cfg), bench, quiet=True)
    print(f"Preview as of the {last:%d %b %Y} close (paper, nothing sent):")
    orders = eng.swing_plan(when)
    by = {}
    for o in orders:
        by.setdefault(o.req.strategy, []).append(o)
    for name in [s.name for s in strategies]:
        cap = eng.risk.strategy_capital(name, "swing")
        got = by.get(name, [])
        print(f"\n{name}  (capital ₹{cap:,.0f})")
        if not got:
            print("  nothing to buy right now (filters say cash)")
        for o in got:
            r = o.req
            print(f"  {r.side} {r.qty:>5} {r.symbol:<12} ~₹{r.ref_price:,.2f}  = ₹{r.qty * r.ref_price:,.0f}  [{o.status}]")
    blocked = j.db.execute("SELECT message FROM events WHERE message LIKE 'blocked%'").fetchall()
    for b in blocked:
        print("  " + b[0])
    return 0


def _test_order_window(now: datetime):
    """(amo, refusal reason). Normal order in market hours, after-market order otherwise; refuses the
    pre-open (cancels are not accepted 09:08-09:15) and the 15:25-16:00 changeover."""
    t, weekday = now.time(), now.weekday() < 5
    if weekday and time(8, 55) <= t < time(9, 15):
        return None, "Pre-open (08:55-09:15): orders can't be cancelled reliably. Run it after 09:15."
    if weekday and time(15, 25) < t < time(16, 0):
        return None, "Between 15:25 and 16:00 neither normal nor after-market orders are reliable: run it after 16:00."
    return not (weekday and time(9, 15) <= t <= time(15, 25)), None


def cmd_test_order(cfg, args) -> int:
    """Live order-path check: see trader/order_test.py. Asks before sending unless --yes."""
    from .brokers.indstocks import IndStocksBroker
    from .instruments import Instruments, round_tick
    from .order_test import MAX_PCT, MIN_PCT, order_path_test
    if args.dry_run:
        print("test-order always sends a real order; --dry-run is not allowed here.")
        return 1
    if (ROOT / cfg["limits"].get("kill_switch_file", "STOP")).exists():
        print("STOP file present: no orders. Remove it to run this test.")
        return 1
    if not (MIN_PCT <= args.pct <= MAX_PCT):
        print(f"--pct must be between {MIN_PCT:g} and {MAX_PCT:g}.")
        return 1
    amo, why = _test_order_window(now_ist())
    if why:
        print(why)
        return 1
    client = _client()
    inst = Instruments.load(client, ROOT / "cache")
    sym = args.symbol.upper()
    market = LiveMarket(client, inst, ROOT)
    tick = inst.tick(sym)
    ltp = market.ltp([sym], now_ist()).get(sym)
    if not ltp:
        print(f"No live price for {sym}.")
        return 1
    print(f"About to place a REAL {'after-market ' if amo else ''}order: BUY 1 {sym} @ "
          f"₹{round_tick(ltp * (1 - args.pct / 100), tick, 'down')} LIMIT, delivery (last price ₹{ltp}), "
          f"then cancel it. It should not fill.")
    if not args.yes and input("Type YES to send: ").strip() != "YES":
        print("Not sent.")
        return 1
    now = now_ist()                                       # the prompt may have waited: check everything again
    amo2, why = _test_order_window(now)
    if why or amo2 != amo:
        print(why or "The market opened or closed while waiting. Nothing sent; run it again.")
        return 1
    ltp2 = market.ltp([sym], now).get(sym)
    if not ltp2:
        print(f"No live price for {sym} any more. Nothing sent.")
        return 1
    if round_tick(ltp2 * (1 - args.pct / 100), tick, "down") != round_tick(ltp * (1 - args.pct / 100), tick, "down"):
        print(f"Price moved while waiting: last ₹{ltp2}, limit now ₹{round_tick(ltp2 * (1 - args.pct / 100), tick, 'down')}.")
    ltp = ltp2
    broker = IndStocksBroker(client, CostModel(), float(cfg["limits"]["order_rate_per_sec"]))
    ok, lines = order_path_test(broker, sym, inst.security_id(sym), ltp, tick, now, args.pct, amo=amo)
    text = "\n".join(lines)
    try:
        from guardian.notifier import Notifier
        Notifier().send(("✅" if ok else "🚨") + f" Live order-path test {'passed' if ok else 'FAILED'}\n{text}")
    except Exception:                                     # noqa: BLE001 - make sure the user still sees it
        print(text)
    return 0 if ok else 1


def cmd_watch(cfg, args) -> int:
    from guardian.main import load_config
    from guardian.notifier import Notifier
    from .watch import Watch
    now = now_ist()
    if now.weekday() >= 5:
        print("Weekend: no session.")
        return 0
    gcfg_path = ROOT / "config.yaml"
    rules = load_config(gcfg_path if gcfg_path.exists() else ROOT / "config.example.yaml")["rules"]
    Watch(_client(), Notifier(dry_run=args.dry_run), rules, ROOT, now_ist).run()
    return 0


def cmd_adopt(cfg, args) -> int:
    """Hand the account's own stocks to momentum_rotation. See trader/adopt.py."""
    from .adopt import adopt, plan, release
    if args.release:
        j = Journal(ROOT / cfg["journal"], "live")
        try:
            for note in release(j, args.strategy, args.release.split(",")):
                print(note)
        finally:
            j.close()
        return 0
    from .gates import is_demoted
    from .instruments import Instruments
    name = args.strategy
    if name != "momentum_rotation":
        print("Only momentum_rotation can take over holdings (it sells what it would not choose).")
        return 1
    sc = cfg["strategies"].get(name) or {}
    if cfg["mode"] != "live" or not sc.get("live"):
        print(f"{name} must be live (mode: live and live: true in trader.yaml).")
        return 1
    if is_demoted(ROOT, name):
        print(f"{name} is demoted to paper; it can't take over holdings.")
        return 1
    if not sc.get("override_gate") and not paper_gate(ROOT / cfg["journal"], name, "swing", cfg)[0]:
        print(f"{name} hasn't passed its paper check and has no override_gate: nothing would manage the holdings.")
        return 1
    client = _client()
    if client.profile().get("is_ddpi_active") is not True:
        print("DDPI is not confirmed active: automatic sells of these holdings could be rejected. Not handing over.")
        return 1
    j = Journal(ROOT / cfg["journal"], "live")
    try:
        busy = j.active_orders()
        if busy:
            print("Live orders are still working or not yet confirmed, so holdings can't be told apart from them:")
            for o in busy:
                print(f"  {o.req.tag}: {o.status} {o.req.side} {o.req.qty} {o.req.symbol} {o.message[:60]}")
            print("Run it again after they settle (normally after the 09:30 check or after 16:30).")
            return 1
        items = plan(client.holdings(), j.positions(product=CNC), args.skip.split(",") if args.skip else [])
        inst = Instruments.load(client, ROOT / "cache")
        bad = [sym for sym, *_ in items if not inst.security_id(sym)]
        items = [it for it in items if it[0] not in bad]
        now = now_ist()
        prices = LiveMarket(client, inst, ROOT).ltp([it[0] for it in items], now) if items else {}
        if bad:
            print("Left with you (no NSE instrument found): " + ", ".join(bad))
        if not items:
            print("Nothing to hand over.")
            return 0
        total = sum(q * prices.get(sym, 0) for sym, q, _, _ in items)
        print(f"These holdings (worth ₹{total:,.0f} now) will be managed by {name}:")
        for sym, q, cost, _ in items:
            px = prices.get(sym)
            print(f"  {sym:<12} {q:>6}  now ₹{px:,.2f}  (your cost ₹{cost:,.2f})" if px else f"  {sym:<12} {q:>6}  NO PRICE: skipped")
        print(f"From its next evening run (16:10) {name}'s rules decide: holdings it would not choose get SELL "
              "orders for the next open (listed on Telegram; `trader.run cancel` before 09:00 stops them).\n"
              "To take holdings back later: `trader.run adopt --release SYM,SYM` (or `all`).")
        if not args.yes and input("Type YES to hand them over: ").strip() != "YES":
            print("Nothing changed.")
            return 1
        for note in adopt(j, name, items, prices, now):
            print(note)
        return 0
    finally:
        j.close()


from .autopilot import transfer_positions  # noqa: E402 - kept importable from trader.run


def cmd_transfer(cfg, args) -> int:
    """Hand the bot's positions from one strategy to another (e.g. when the long-term core replaces the ETF trend):
    no orders, cost kept; from the next evening run the new strategy's rules decide what to do with them."""
    src, dst = args.src, args.dst
    if not src or not dst or src == dst:
        print("Usage: trader.run transfer --from STRATEGY --to STRATEGY [--book live|paper]")
        return 1
    if dst not in (cfg["strategies"] or {}):
        print(f"{dst} is not in trader.yaml's strategies.")
        return 1
    j = Journal(ROOT / cfg["journal"], args.book)
    try:
        busy = [o for o in j.active_orders() if o.req.strategy in (src, dst)]
        if busy:
            print("Orders of these strategies are still working; run it again after they settle:")
            for o in busy:
                print(f"  {o.req.tag}: {o.status} {o.req.side} {o.req.qty} {o.req.symbol}")
            return 1
        pos = j.positions(strategy=src)
        if not pos:
            print(f"{src} holds nothing in the {args.book} book.")
            return 0
        print(f"{args.book.upper()} positions of {src} to be managed by {dst} from its next evening run:")
        for p in pos:
            print(f"  {p.symbol:<12} {abs(p.qty):>6} @ ₹{p.avg_price:,.2f}")
        if not args.yes and input("Type YES to move them: ").strip() != "YES":
            print("Nothing changed.")
            return 1
        for note in transfer_positions(j, src, dst):
            print(note)
        return 0
    finally:
        j.close()


def cmd_retest(cfg, args) -> int:
    """Yearly re-test of the autopilot's rule (trader/retest.py; Addendum 14)."""
    from guardian.notifier import Notifier
    from .retest import run
    if not cfg.get("_autopilot"):
        print("The autopilot is off (trader.yaml: autopilot: {enabled: true}); nothing to re-test.")
        return 0
    notifier = Notifier(dry_run=args.dry_run)
    print(run(ROOT, notify=notifier.send, update=not args.no_update, force=args.force))
    return 0


def cmd_report(cfg, args) -> int:
    """Monthly performance report (see trader/report.py)."""
    import pandas as pd
    from guardian.notifier import Notifier
    from .instruments import Instruments
    from .report import build, default_month
    today = now_ist().date()
    month, complete = (args.month, args.month < f"{today:%Y-%m}") if args.month else default_month(today)
    client = _client()
    inst = Instruments.load(client, ROOT / "cache")
    market = LiveMarket(client, inst, ROOT)
    jp = ROOT / cfg["journal"]
    live, paper = Journal(jp, "live"), Journal(jp, "paper")
    lab_cfg = C.lab(cfg)
    lab = Journal(jp, "lab") if lab_cfg else None
    try:
        syms = sorted({p.symbol for j in (live, paper, lab) if j is not None for p in j.positions()})
        marks = market.ltp(syms, now_ist()) if syms else {}
        nb = market.daily(["NIFTYBEES"], now_ist()).get("NIFTYBEES")
        closes = nb["close"] if nb is not None else pd.Series(dtype=float)
        has_live = cfg["mode"] == "live" or bool(live.positions()) or not live.trades().empty
        Notifier(dry_run=args.dry_run).send(build(cfg, live if has_live else None, paper, marks, closes, month, complete,
                                                  lab=lab, lab_cfg=lab_cfg))
    finally:
        live.close()
        paper.close()
        if lab is not None:
            lab.close()
    return 0


def cmd_split(cfg, args) -> int:
    """Record a bonus issue, split or consolidation by hand for the bot's live positions in one stock:
    `trader.run split --stock HDFCBANK --ratio 2` (new shares per old share: 2 for a 1:1 bonus or 2-for-1 split,
    1.5 for a 1:2 bonus, 0.5 for a 2-into-1 consolidation). Normally the bot does this by itself."""
    from .corporate import Adjustment, apply
    sym = (args.stock or "").upper()
    if not sym or args.ratio <= 0:
        print("usage: python -m trader.run split --stock SYMBOL --ratio NEW_SHARES_PER_OLD_SHARE  (--ratio 1: there "
              "was no bonus/split, the price really fell - stop waiting)")
        return 1
    j = Journal(ROOT / cfg["journal"], "live")
    try:
        ps = [p for p in j.positions(product=CNC) if p.symbol == sym and p.qty > 0]
        if not ps:
            print(f"The bot holds no {sym}.")
            return 1
        now = now_ist()
        if abs(args.ratio - 1) < 1e-9:
            for p in ps:
                w = j.get(f"ca_wait:{p.strategy}:{p.symbol}") or {}
                rej = list(j.get(f"ca_rejected:{p.strategy}:{p.symbol}") or [])
                rej += [d for d in (w.get("when"), now.date().isoformat()) if d and d not in rej]
                j.put(f"ca_rejected:{p.strategy}:{p.symbol}", rej)
                j.put(f"ca_wait:{p.strategy}:{p.symbol}", None)
                print(f"{sym} ({p.strategy}): treated as a real price fall; its sells are no longer held.")
            return 0
        for p in ps:
            if j.active_orders(p.strategy, p.symbol, product=CNC):
                print(f"{sym} ({p.strategy}): an order is working; run `trader.run cancel` first. Not changed.")
                continue
            new_qty = int(p.qty * args.ratio + 1e-6)
            if new_qty < 1:
                print(f"{sym} ({p.strategy}): {p.qty} x {args.ratio:g} is less than one share. Not changed.")
                continue
            print(apply(j, Adjustment(p, p.qty / new_qty, now.date(), new_qty), now))
            j.put(f"ca_wait:{p.strategy}:{p.symbol}", None)
    finally:
        j.close()
    return 0


def cmd_keep(cfg, args) -> int:
    """Keep some shares of a stock the bot is selling: its working after-market sell is replaced by one for fewer
    shares, and the kept shares become yours (the bot no longer counts or manages them).
      python -m trader.run keep --stock RELIANCE --qty 1      (weekdays after 16:00 or before 08:55)"""
    sym, keep = (args.stock or "").upper(), int(args.qty or 0)
    now = now_ist()
    if now.weekday() < 5 and time(8, 55) <= now.time() < time(16, 0):
        print("Run it after 16:00 or before 08:55: the replacement sell is an after-market order. Nothing changed.")
        return 1
    if not sym or keep < 1:
        print("usage: python -m trader.run keep --stock SYMBOL --qty SHARES_TO_KEEP")
        return 1
    live = [e for e in build_engines(cfg, args.dry_run) if e.broker.live]
    if not live:
        print("No live engine (mode: paper and nothing live open): nothing to change.")
        return 1
    eng = live[0]
    eng.sync(now)
    pos = [p for p in eng.j.positions(product=CNC) if p.symbol == sym and p.qty > 0]
    if not pos:
        print(f"The bot holds no {sym}: the shares in the account are already yours.")
        return 1
    p = max(pos, key=lambda x: x.qty)
    if keep >= p.qty:
        print(f"The bot holds {p.qty} {sym}. To keep all of them: python -m trader.run adopt --release {sym}")
        return 1
    working = eng.j.active_orders(p.strategy, sym, "exit", CNC)
    key = f"keep_pending:{p.strategy}:{sym}"
    pending = eng.j.get(key) or {}
    prev = eng.j.order(pending["tag"]) if pending.get("tag") and not working else None
    if prev is not None and prev.status != CANCELLED:
        prev = None
    if (working or prev) and (eng.frozen or eng.risk.kill_switch()):
        print("Live trading is switched off (mode: paper or a STOP file), so a smaller sell could not be sent. "
              "Nothing changed.")
        return 1
    if any(o.filled_qty > 0 or o.status not in (NEW, OPEN) for o in working):
        print(f"The sell of {sym} is already (partly) filled or changing: nothing changed.")
        return 1
    old = working[0] if working else prev               # prev: our cancel of an earlier try went through later
    if working:
        eng.j.put(key, {"tag": old.req.tag, "at": now.isoformat()})
    for o in working:
        eng.broker.cancel(o, now)
        eng.j.save_order(o)
    if working:
        eng.settle(now)
        if eng.j.active_orders(p.strategy, sym, "exit", CNC):
            print(f"INDstocks did not confirm the cancel of the {sym} sell yet; the bot's record is unchanged. Run "
                  "this command again in a minute (it finishes the job whichever way the cancel went).")
            return 1
    eng.j.put(key, None)
    p = eng._find_position(p.strategy, sym, CNC) or p
    if p.entry_charges:
        p.entry_charges *= (p.qty - keep) / p.qty
    p.qty -= keep
    eng.j.save_position(p)
    eng.j.event("warning", f"keep: {keep} {sym} given back to the owner by {p.strategy}; the bot holds {p.qty}")
    msg = f"{sym}: {keep} share{'s' if keep > 1 else ''} kept for you; {p.strategy} now manages {p.qty}."
    if old is None:
        print(msg + " No sell was working.")
        return 0
    r = old.req
    sig = Signal(r.strategy, sym, SELL, "exit", 1.0, r.ref_price or r.limit_price, now, CNC,
                 reason=f"sell after keeping {keep}")
    o = eng.submit(sig, p.qty, r.limit_price, now, amo=True, n=4, reprice_of=r.tag)
    if o is not None and o.status == UNKNOWN:
        text = (f"{msg} INDstocks did not confirm the new sell of {p.qty} {sym} (the bot looks it up again at every "
                f"run). Check the app: if it lists no SELL of {p.qty} {sym}, nothing sells them tomorrow.")
        print(text)
        eng._say(text, force=True)
        return 1
    if o is None or o.status == REJECTED:
        why = eng.last_block or (o.message if o else "") or "no reason given"
        eng.j.put(f"ext_cancel:{r.tag}", now.isoformat())       # the morning check sends the sell instead
        text = (f"{msg} The new after-market sell of {p.qty} was NOT placed ({why}); the 09:30 check sends it "
                "instead, at the market price.")
        print(text)
        eng._say(text, force=True)
        return 1
    text = f"{msg} Sell replaced: SELL {p.qty} {sym} @ ₹{o.req.limit_price:,.2f} (after-market, next open)."
    print(text)
    eng._say(text, force=True)
    return 0


def cmd_universe(cfg, args) -> int:
    """Refresh the Nifty 50 list from NSE (see trader/universe_update.py)."""
    from guardian.notifier import Notifier
    from .instruments import Instruments
    from .universe_update import update
    if cfg.get("universe"):
        print("trader.yaml sets `universe:` itself, so the NSE list is not used. Remove it to follow the Nifty 50.")
        return 0
    current = C.universe(cfg)
    known = None
    try:
        inst = Instruments.load(_client(), ROOT / "cache")
        known = lambda s: bool(inst.security_id(s))              # noqa: E731
    except Exception as e:                                       # noqa: BLE001
        print(f"Instrument list unavailable ({e}); symbols are not checked against INDstocks.")
    try:
        changed, msg = update(ROOT, current, now_ist().date(), known)
    except Exception as e:                                       # noqa: BLE001 - keep the current list
        print(f"Nifty 50 list NOT refreshed, the current one stays: {e}")
        return 0
    print(msg)
    if changed:
        Notifier(dry_run=args.dry_run).send(msg)
    return 0


def cmd_holidays(cfg, args) -> int:
    """Refresh NSE's trading-holiday list (see trader/holidays.py)."""
    from guardian.notifier import Notifier
    from .holidays import update
    try:
        changed, msg = update(ROOT, now_ist().date())
    except Exception as e:                                       # noqa: BLE001 - keep the saved list
        print(f"NSE holiday list NOT refreshed, the saved one stays: {e}")
        return 1                                                 # job.sh sends the failure to Telegram
    print(msg)
    if changed:
        Notifier(dry_run=args.dry_run).send(msg)
    return 0


def cmd_reconcile(cfg, args) -> int:
    """Live delivery positions vs the account's holdings. Best before 09:15 or after settlement."""
    from .reconcile import check, fix
    client = _client()
    j = Journal(ROOT / cfg["journal"], "live")
    try:
        bad = check(j.positions(product=CNC), client.holdings())
        if not bad:
            print("Live positions match the account's holdings.")
            return 0
        for sym, q, have in bad:
            print(f"MISMATCH {sym}: the bot's record says {q}, the account holds {have}")
        if args.fix:
            for note in fix(j, bad, now_ist()):
                print(note)
            return 0
        print("Run again with --fix to lower the bot's record to what the account holds.")
        return 1
    finally:
        j.close()


COMMANDS = {"check": cmd_check, "adopt": cmd_adopt, "transfer": cmd_transfer, "retest": cmd_retest, "report": cmd_report, "reconcile": cmd_reconcile, "universe": cmd_universe, "holidays": cmd_holidays, "indices": cmd_indices, "split": cmd_split, "keep": cmd_keep, "watch": cmd_watch, "test-order": cmd_test_order, "preview": cmd_preview, "status": cmd_status, "cancel": cmd_cancel, "flatten": cmd_flatten,
            "swing-plan": cmd_swing_plan, "swing-check": cmd_swing_check, "intraday": cmd_intraday}


def main(argv=None) -> int:
    try:                                                  # secrets (Vault or .env) for every command, not only
        from guardian.secrets import load_secrets         # those that log in to INDstocks
        load_secrets(ROOT)
    except ImportError:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Signal-gated trading engine (paper by default)")
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("--config")
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of sending Telegram")
    ap.add_argument("--force", action="store_true", help="swing-plan: run even before 15:35")
    ap.add_argument("--symbol", default="NIFTYBEES", help="test-order: instrument (default NIFTYBEES)")
    ap.add_argument("--pct", type=float, default=3.0, help="test-order: limit this %% below the last price")
    ap.add_argument("--yes", action="store_true", help="test-order: don't ask before sending")
    ap.add_argument("--fix", action="store_true", help="reconcile: correct the bot's record to the account")
    ap.add_argument("--strategy", default="momentum_rotation", help="adopt: strategy that takes over the holdings")
    ap.add_argument("--skip", default="", help="adopt: comma-separated symbols to leave alone")
    ap.add_argument("--month", default="", help="report: YYYY-MM (default: last month on the 1st-3rd, else this month)")
    ap.add_argument("--release", default="", help="adopt: take back handed-over holdings: SYM,SYM or 'all'")
    ap.add_argument("--from", dest="src", default="", help="transfer: strategy whose positions move")
    ap.add_argument("--to", dest="dst", default="", help="transfer: strategy that takes them over")
    ap.add_argument("--book", default="live", choices=["live", "paper"], help="transfer: which journal book")
    ap.add_argument("--no-update", action="store_true", help="retest: use the data already downloaded")
    ap.add_argument("--forget", action="store_true",
                    help="cancel: mark earlier-day orders INDstocks no longer knows as cancelled (check the app first)")
    ap.add_argument("--stock", default="", help="split / keep: the stock")
    ap.add_argument("--qty", type=int, default=0, help="keep: shares to keep out of the bot's sell")
    ap.add_argument("--ratio", type=float, default=0.0, help="split: new shares per old share (2 = 1:1 bonus)")
    args = ap.parse_args(argv)
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(ROOT / "logs" / "trader.log", encoding="utf-8")])
    cfg = C.load(Path(args.config) if args.config else None)
    return COMMANDS[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
