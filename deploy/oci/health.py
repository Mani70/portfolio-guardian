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

JOBS = ["intraday", "watch", "swing-check", "swing-plan", "backup", "universe", "holidays", "autodeploy", "insights"]
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
            warn.append("could not read INDstocks funds")
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
        line = f"funds free ₹{free:,.0f}" + (f" (₹{reserve:,.0f} kept aside)" if reserve else "") + \
            f"; live plan ₹{plan:,.0f}, ₹{invested:,.0f} invested"
        if plan and usable < plan * 0.95:
            if cfg["capital"].get("live_from_account", True):
                warn.append(line + f" - live trades sized to the money available (~{usable / plan:.0%} of plan)")
            else:
                warn.append(line + " - orders will be cut down to the money available")
        else:
            ok.append(line)
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"funds check failed: {str(e)[:120]}")


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
            warn.append("HOLDINGS MISMATCH: " + "; ".join(f"{s} bot {q} vs account {h}" for s, q, h in bad)
                        + " - sold by hand, or the company merged/delisted? run: "
                          ".venv/bin/python -m trader.run reconcile --fix")
        else:
            ok.append("holdings match: " + ", ".join(f"{p.symbol} {p.qty}" for p in pos))
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"holdings check failed: {str(e)[:120]}")


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
            warn.append("NEW HOLDING not bought by the bot: " + ", ".join(f"{names[k]} {now[k]}" for k in new)
                        + " - shares from a demerger, a rights issue, an IPO allotment, or your own buy? "
                          "The bot does not manage them; the decision is yours.")
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"new-holdings check failed: {str(e)[:120]}")


def main(argv) -> int:
    reboot, dry = "--reboot" in argv, "--dry-run" in argv
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    ok, warn = [], []
    try:
        from guardian.secrets import SOURCE_VAR, dotenv_secrets
        src = os.environ.get(SOURCE_VAR, "env")
        if "FAILED" in src:
            warn.append(f"secrets: {src} - check the Vault policy / dynamic group")
        elif src.startswith("vault"):
            ok.append("secrets from OCI Vault")
            left = dotenv_secrets(ROOT)
            if left:
                warn.append(f"secrets still written in .env (remove them): {', '.join(left)}")
    except Exception as e:                                    # noqa: BLE001
        warn.append(f"secrets check failed: {str(e)[:120]}")

    # broker token (generates a fresh one via TOTP after the 07:00 reset; later jobs reuse the cache)
    client = None
    try:
        from guardian.broker import IndStocksClient
        client = IndStocksClient.from_env("NSE", token_cache=ROOT / ".token_cache.json")
        p = client.profile()
        ok.append(f"INDstocks token OK ({p.get('first_name', '')} {p.get('last_name', '')})".strip())
        if p.get("is_ddpi_active") is False:
            warn.append("DDPI is OFF: automatic sells of delivery holdings will be rejected - activate DDPI in INDmoney")
    except Exception as e:                                    # noqa: BLE001
        client = None
        warn.append(f"INDstocks login FAILED: {str(e)[:200]}")

    off = clock_offset()
    if off is None:
        warn.append("clock sync unknown (chronyc not answering)")
    elif off > 1.0:
        warn.append(f"clock is off by {off:.1f}s (TOTP may fail)")
    else:
        ok.append(f"clock in sync ({off * 1000:.0f} ms)")

    du = shutil.disk_usage("/")
    free_gb = du.free / 1e9
    (ok if free_gb > 3 else warn).append(f"disk free {free_gb:.0f} GB")
    m = mem_available_pct()
    if m is not None:
        (ok if m > 10 else warn).append(f"memory free {m:.0f}%")

    # configuration and kill switch
    try:
        from trader import config as C
        cfg = C.load(None)
        mode = cfg.get("mode", "paper")
        live = [n for n, s in (cfg.get("strategies") or {}).items() if (s or {}).get("live")]
        ok.append(f"mode {mode.upper()}" + (f", live-enabled: {', '.join(live)}" if mode == "live" and live else ""))
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
            warn.append("STOP file present: no new orders will be placed")
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
                warn.append(f"trader.yaml holidays: not dates, ignored: {', '.join(bad[:5])} (use YYYY-MM-DD in a list)")
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
                        ok.append(f"lab (paper experiment): {len(pos)} positions, working orders {len(orders)}")
                    continue
                if pos or orders or mode_ == "paper":
                    held = ", ".join(f"{p.symbol} {p.qty:g}" for p in pos[:8]) or "none"
                    ok.append(f"{mode_}: positions {held}; working orders {len(orders)}")
        except Exception as e:                                # noqa: BLE001
            warn.append(f"journal unreadable: {e}")

    # previous job runs
    runs = last_runs()
    if runs:
        parts = []
        for job in JOBS:
            if job in runs:
                when, rc = runs[job]
                parts.append(f"{job} {'✓' if rc == 0 else '✗'} {when:%a %H:%M}")
                if rc != 0:
                    warn.append(f"last {job} run failed (rc {rc}, {when:%a %d %b %H:%M})")
        ok.append("last runs: " + ", ".join(parts))
    else:
        ok.append("no scheduled runs yet")

    backups = sorted((ROOT / "backups").glob("pg-*.tar.gz"))
    if backups:
        age_h = (time.time() - backups[-1].stat().st_mtime) / 3600
        (ok if age_h < 80 else warn).append(f"last backup {age_h:.0f} h ago")

    ip = public_ip()
    ip_file = ROOT / "trader" / "state" / "public_ip.txt"
    if ip:
        old = ip_file.read_text().strip() if ip_file.exists() else None
        if old and old != ip:
            warn.append(f"public IP CHANGED {old} -> {ip}: update the static IP on INDstocks before live orders")
        else:
            ok.append(f"public IP {ip}")
        ip_file.parent.mkdir(parents=True, exist_ok=True)
        ip_file.write_text(ip + "\n")

    head = "🔄 Server restarted" if reboot else ("⚠️ Server check: attention needed" if warn else "✅ Server OK")
    from zoneinfo import ZoneInfo
    text = f"{head} — {datetime.now(ZoneInfo('Asia/Kolkata')):%a %d %b %H:%M} IST"
    if warn:
        text += "\n" + "\n".join(f"• {w}" for w in warn)
    text += "\n" + "\n".join(f"· {o}" for o in ok)
    from guardian.notifier import Notifier
    Notifier(dry_run=dry).send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
