"""Morning heartbeat (weekdays 09:05) and restart notice. Sends one Telegram message:
token OK, clock in sync, disk/memory, last run of each job, open positions, backup age, public IP.

If the message does NOT arrive on a weekday morning, the server is down or stopped: check the
OCI console (Compute > Instances) and start it.

  python deploy/oci/health.py [--reboot] [--dry-run]
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

JOBS = ["intraday", "watch", "swing-check", "swing-plan", "backup", "universe", "holidays", "autodeploy", "insights", "fo-paper"]
END = re.compile(r"^===== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) end (\S+) rc=(\d+)")


def clock_offset() -> float | None:
    """System clock error in seconds according to chrony (None if unknown)."""
    try:
        out = subprocess.run(["chronyc", "-c", "tracking"], capture_output=True, text=True, timeout=10).stdout
        return abs(float(out.split(",")[4]))
    except Exception:                                         # noqa: BLE001
        return None


def last_runs() -> dict:
    res = {}
    for job in JOBS:
        p = ROOT / "logs" / "cron" / f"{job}.log"
        if not p.exists():
            continue
        try:
            with p.open("rb") as f:
                f.seek(max(0, p.stat().st_size - 20000))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            m = END.match(line)
            if m:
                res[job] = (datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), int(m.group(3)))
                break
    return res


def mem_available_pct() -> float | None:
    try:
        info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        kb = lambda k: float(info[k].strip().split()[0])     # noqa: E731
        return 100 * kb("MemAvailable") / kb("MemTotal")
    except Exception:                                         # noqa: BLE001
        return None


def public_ip() -> str | None:
    import requests
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = requests.get(url, timeout=6)
            if r.ok and re.fullmatch(r"[0-9a-fA-F.:]+", r.text.strip()):
                return r.text.strip()
        except Exception:                                     # noqa: BLE001
            continue
    return None


def funds_line(cfg, client, ok, warn) -> None:
    """Live: free cash at INDstocks against what the live swing strategies plan to use (same rules as the
    engine: reserve kept aside, 3% headroom for after-market limits and charges, their own holdings at cost)."""
    try:
        from trader.brokers.indstocks import IndStocksBroker
        from trader.journal import Journal
        from trader.models import CNC
        from trader.risk import Risk
        free = IndStocksBroker(client).available_funds(CNC)
        if free is None:
            warn.append("Could not read the cash in your INDstocks account today.")
            return
        from trader.gates import is_demoted, paper_gate
        from trader.strategies import build
        live = []
        for strat in build(cfg):
            sc = (cfg.get("strategies") or {}).get(strat.name) or {}
            if strat.engine != "swing" or not sc.get("live") or is_demoted(ROOT, strat.name):
                continue
            if sc.get("override_gate") or paper_gate(ROOT / cfg["journal"], strat.name, "swing", cfg)[0]:
                live.append(strat.name)
        j = Journal(ROOT / cfg["journal"], "live")
        try:
            plan = sum(Risk(cfg, j, ROOT).planned_capital(n, "swing") for n in live)
            invested = sum(abs(p.qty) * p.avg_price for p in j.positions(product=CNC) if p.strategy in live)
        finally:
            j.close()
        reserve = float(cfg["capital"].get("reserve", 0) or 0)
        usable = max(0.0, free - reserve) / 1.03 + invested
        from trader.plain import rupees
        line = (f"Cash in your INDstocks account: {rupees(free)}"
                + (f" ({rupees(reserve)} of it is always kept aside, never invested)" if reserve else "")
                + f". Invested by the bot so far: {rupees(invested)}.")
        if plan and usable < plan * 0.95:
            if cfg["capital"].get("live_from_account", True):
                warn.append(line + f" Its plan needs {rupees(plan)}, so it sizes purchases to the money available "
                                   f"(about {usable / plan:.0%} of the plan).")
            else:
                warn.append(line + f" Its plan needs {rupees(plan)}; orders will be cut down to the money available.")
        else:
            ok.append(line)
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"Could not check the cash in your account ({str(e)[:120]})")


def holdings_line(cfg, client, ok, warn, holdings=None) -> None:
    """Live: the bot's delivery positions must exist in the account (checked before the open)."""
    try:
        from trader.journal import Journal
        from trader.models import CNC
        from trader.reconcile import check
        j = Journal(ROOT / cfg["journal"], "live")
        try:
            pos = j.positions(product=CNC)
        finally:
            j.close()
        if not pos:
            return
        bad = check(pos, holdings if holdings is not None else client.holdings())
        if bad:
            warn.append("Your account and the bot disagree about what you own: "
                        + "; ".join(f"{s}: bot thinks {q}, account has {h}" for s, q, h in bad)
                        + ". Was something sold by hand, or did a company merge? To fix the bot's records run: "
                          ".venv/bin/python -m trader.run reconcile --fix")
        else:
            ok.append("Your holdings match the bot's records ✓: " + ", ".join(f"{p.symbol} {p.qty:g}" for p in pos))
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"Could not compare your holdings with the bot's records ({str(e)[:120]})")


def new_holdings_line(cfg, holdings, warn, state: Path | None = None) -> None:
    """Shares that appear in the account without the bot buying them - demerger shares, rights entitlements,
    an IPO allotment, a buy in the app. Said once; the bot does not manage them."""
    import json
    import os
    state = state or ROOT / "trader" / "state" / "known_holdings.json"
    try:
        holdings = list(holdings)
        if not holdings:
            return                                            # an empty reply is more likely a glitch: keep the memory
        now: dict = {}
        names: dict = {}
        for h in holdings:
            k = str(h.security_id) if h.security_id else h.symbol.upper()
            now[k] = now.get(k, 0) + int(round(float(h.qty)))
            names[k] = h.symbol.upper()
        try:
            prev = json.loads(state.read_text(encoding="utf-8")) if state.exists() else None
            prev = prev if isinstance(prev, dict) else None
        except (OSError, ValueError):
            prev = None                                       # damaged: start again from today's holdings
        bot = set()
        jp = ROOT / cfg["journal"]
        if jp.exists():
            from trader.journal import Journal
            j = Journal(jp, "live")
            try:
                for p in j.positions():
                    bot |= {str(p.security_id), p.symbol.upper()} if p.security_id else {p.symbol.upper()}
            finally:
                j.close()
        state.parent.mkdir(parents=True, exist_ok=True)
        keep = {**(prev or {}), **now}                        # a partial reply must not make old rows "new" later
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps(keep, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, state)
        if prev is None:
            return                                            # first run: remember what is there
        new = sorted(k for k in now if k not in prev and names[k] not in prev and k not in bot and names[k] not in bot)
        if new:
            warn.append("New shares in your account that the bot did not buy: " + ", ".join(f"{names[k]} {now[k]}"
                                                                                           for k in new)
                        + ". They may come from a company split-up (demerger), a rights issue, an IPO, or your own "
                          "purchase. The bot does not manage them; what to do with them is your decision.")
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"new-holdings check failed: {str(e)[:120]}")


def main(argv) -> int:
    reboot, dry = "--reboot" in argv, "--dry-run" in argv
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    ok, warn, tech = [], [], []
    try:
        from guardian.secrets import SOURCE_VAR, dotenv_secrets
        src = os.environ.get(SOURCE_VAR, "env")
        if "FAILED" in src:
            warn.append(f"Could not read the bot's passwords from the Oracle vault ({src}): check the Vault policy.")
        elif src.startswith("vault"):
            tech.append("passwords in Oracle vault ✓")
            left = dotenv_secrets(ROOT)
            if left:
                warn.append(f"Passwords are still written in the .env file (remove them; they are in the vault): "
                            f"{', '.join(left)}")
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"secrets check failed: {str(e)[:120]}")

    # broker token (generates a fresh one via TOTP after the 07:00 reset; later jobs reuse the cache)
    client = None
    try:
        from guardian.broker import IndStocksClient
        client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
        p = client.profile()
        tech.append("broker login ✓")
        if p.get("is_ddpi_active") is False:
            warn.append("DDPI is OFF. DDPI is the permission that lets the bot sell shares from your account; without "
                        "it every automatic sale is refused. Turn it on in the INDmoney app.")
    except Exception as e:                                    # noqa: BLE001
        client = None
        warn.append(f"Could not log in to INDstocks (your broker), so the bot cannot trade until this is fixed. "
                    f"Details: {str(e)[:200]}")

    off = clock_offset()
    if off is None:
        warn.append("Could not check the server clock (the broker login needs the right time).")
    elif off > 1.0:
        warn.append(f"The server clock is {off:.1f} seconds off; the broker login code may fail.")
    else:
        tech.append(f"clock ✓ ({off * 1000:.0f} ms)")

    du = shutil.disk_usage("/")
    free_gb = du.free / 1e9
    if free_gb > 3:
        tech.append(f"disk {free_gb:.0f} GB free")
    else:
        warn.append(f"The server disk is almost full ({free_gb:.0f} GB free).")
    m = mem_available_pct()
    if m is not None:
        if m > 10:
            tech.append(f"memory {m:.0f}% free")
        else:
            warn.append(f"The server is low on memory ({m:.0f}% free).")

    # configuration and kill switch
    try:
        from trader import config as C
        cfg = C.load(None)
        mode = cfg.get("mode", "paper")
        live = [n for n, s in (cfg.get("strategies") or {}).items() if (s or {}).get("live")]
        from trader.plain import strategy as plain_strategy
        if mode == "live" and live:
            ok.append("💰 Real-money trading is ON for: " + ", ".join(plain_strategy(n) for n in live) + ".")
        else:
            ok.append("📄 Practice mode: no real money is used." if mode != "live" else
                      "💰 Real-money mode, but no strategy is allowed to use real money.")
        if cfg.get("_autopilot"):
            from trader.autopilot import describe
            ok.append(describe(cfg, ROOT))
        holdings = None
        if client is not None:
            try:
                holdings = client.holdings()
            except Exception as e:                            # noqa: BLE001
                warn.append(f"holdings unavailable: {str(e)[:120]}")
        if mode == "live" and client is not None:
            funds_line(cfg, client, ok, warn)
            if holdings is not None:
                holdings_line(cfg, client, ok, warn, holdings)
        if holdings is not None:
            new_holdings_line(cfg, holdings, warn)
        if (ROOT / cfg["limits"].get("kill_switch_file", "STOP")).exists():
            warn.append("The STOP switch is on (a file named STOP on the server): the bot places no new orders.")
        try:
            from trader.universe_update import staleness
            stale = staleness(ROOT, cfg, datetime.now().date())
            if stale:
                warn.append(stale)
        except Exception as e:                                # noqa: BLE001
            warn.append(f"Nifty 50 list check failed: {str(e)[:120]}")
        try:
            from trader.holidays import config_problems, staleness as holiday_staleness
            stale = holiday_staleness(ROOT, datetime.now().date())
            if stale:
                warn.append(stale)
            bad = config_problems(cfg)
            if bad:
                warn.append(f"Some market holidays in trader.yaml are not valid dates and were ignored: "
                        f"{', '.join(bad[:5])} (write them as YYYY-MM-DD).")
        except Exception as e:                                # noqa: BLE001
            warn.append(f"holiday list check failed: {str(e)[:120]}")
    except Exception as e:                                    # noqa: BLE001
        cfg = None
        warn.append(f"trader.yaml could not be read: {e}")

    # positions per journal
    if cfg:
        try:
            from trader.journal import Journal
            from trader.models import ACTIVE
            jp = ROOT / cfg["journal"]
            for mode_ in ("paper", "live", "lab"):
                if not jp.exists():
                    break
                j = Journal(jp, mode_)
                pos, orders = j.positions(), j.load_orders(statuses=ACTIVE)
                j.close()
                if mode_ == "lab":
                    if pos or orders:
                        tech.append(f"lab experiments: {len(pos)} positions")
                    continue
                if pos or orders or mode_ == "paper":
                    held = ", ".join(f"{p.symbol} {p.qty:g}" for p in pos[:8]) or "nothing yet"
                    label = "Real-money holdings" if mode_ == "live" else "Practice (paper) holdings"
                    ok.append(f"{label}: {held}" + (f"; orders waiting to be filled: {len(orders)}" if orders else "")
                              + ".")
        except Exception as e:                                # noqa: BLE001
            warn.append(f"The bot's trade records could not be read ({e}).")

    # previous job runs
    runs = last_runs()
    if runs:
        parts = []
        for job in JOBS:
            if job in runs:
                when, rc = runs[job]
                parts.append(f"{job} {'✓' if rc == 0 else '✗'} {when:%a %H:%M}")
                if rc != 0:
                    warn.append(f"The '{job}' job failed when it last ran ({when:%a %d %b %H:%M}); see "
                                f"logs/cron/{job}.log on the server.")
        tech.append("jobs: " + ", ".join(parts))
    else:
        tech.append("no scheduled jobs have run yet")

    backups = sorted((ROOT / "backups").glob("pg-*.tar.gz"))
    if backups:
        age_h = (time.time() - backups[-1].stat().st_mtime) / 3600
        if age_h < 80:
            tech.append(f"backup {age_h:.0f} h ago")
        else:
            warn.append(f"The last backup is {age_h:.0f} hours old.")

    ip = public_ip()
    ip_file = ROOT / "trader" / "state" / "public_ip.txt"
    if ip:
        old = ip_file.read_text().strip() if ip_file.exists() else None
        if old and old != ip:
            warn.append(f"The server's internet address changed from {old} to {ip}. Update it in INDstocks' API "
                        "settings, or real orders will be refused.")
        else:
            tech.append(f"address {ip}")
        ip_file.parent.mkdir(parents=True, exist_ok=True)
        ip_file.write_text(ip + "\n")

    head = ("🔄 The server restarted" if reboot else "⚠️ Your trading bot needs attention" if warn
            else "✅ Your trading bot is healthy")
    from zoneinfo import ZoneInfo
    text = f"{head} — {datetime.now(ZoneInfo('Asia/Kolkata')):%a %d %b %H:%M} IST"
    text += ("\n" + "\n".join(f"⚠️ {w}" for w in warn)) if warn else "\nEverything needed for today's trading is working."
    text += "\n\n" + "\n".join(f"• {o}" for o in ok)
    if tech:
        text += "\n\nTechnical checks: " + " · ".join(tech)
    from guardian.notifier import Notifier
    Notifier(dry_run=dry).send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
