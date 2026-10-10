"""Server helper scripts (deploy/oci): backup, alert masking, schedule consistency, run-log parsing."""
import importlib.util
import re
import sqlite3
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OCI = ROOT / "deploy" / "oci"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"oci_{name}", OCI / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backup_snapshot_and_prune(tmp_path, monkeypatch):
    backup = _load("backup")
    (tmp_path / "trader" / "state").mkdir(parents=True)
    db = tmp_path / "trader" / "state" / "journal.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()                                   # connection left open, like a running job
    (tmp_path / "trader.yaml").write_text("mode: paper\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    out = backup.build(tmp_path / "backups", "20261030")
    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert "trader/state/journal.db" in names and "trader.yaml" in names
        assert not any(".env" in n or "token" in n for n in names)
        tar.extract("trader/state/journal.db", tmp_path / "x")
    assert sqlite3.connect(tmp_path / "x" / "trader/state/journal.db").execute("SELECT x FROM t").fetchone()[0] == 42
    con.close()
    for d in range(1, 41):
        (tmp_path / "backups" / f"pg-2001{d:04d}.tar.gz").write_text("")
    assert backup.prune(tmp_path / "backups", keep=30) == 11
    assert out.exists()                            # the newest is kept


def test_alert_masks_secrets(tmp_path):
    alert = _load("alert")
    log = tmp_path / "x.log"
    log.write_text("===== start\nfine\nAuthorization: Bearer abc123\nINDSTOCKS_TOTP_SECRET=JBSW\n"
                   '{"access_token": "eyJ"}\nmpin 1234\n')
    t = alert.tail(log)
    assert "fine" in t and "=====" not in t
    for s in ("abc123", "JBSW", "eyJ", "1234"):
        assert s not in t


def test_crontab_jobs_exist_in_wrapper():
    wrapper = (OCI / "job.sh").read_text()
    jobs = set(re.findall(r"^\s+([a-z-]+)\)\s", wrapper, re.M))
    cron = [l for l in (OCI / "crontab").read_text().splitlines() if l.strip() and not l.startswith("#")]
    used = {m.group(1) for l in cron for m in [re.search(r"\$PG (\S+)", l)] if m}
    assert used and used <= jobs, used - jobs
    assert "CRON_TZ=Asia/Kolkata" in cron
    for l in cron:                                 # cron treats % as a newline: must not appear
        assert "%" not in l
    times = {m.group(3): (int(m.group(2)), int(m.group(1)))
             for l in cron for m in [re.match(r"(\d+)\s+(\d+)\s+\*\s+\*\s+1-5\s+\$PG (\S+)", l)] if m}
    assert times["swing-plan"] >= (15, 35)         # needs the closing price
    assert times["health"] < times["intraday"] < times["swing-check"]
    assert b"\r" not in (OCI / "job.sh").read_bytes()


def test_health_reads_last_runs(tmp_path, monkeypatch):
    health = _load("health")
    (tmp_path / "logs" / "cron").mkdir(parents=True)
    (tmp_path / "logs" / "cron" / "swing-plan.log").write_text(
        "===== 2026-10-29 15:45:01 start swing-plan\n===== 2026-10-29 15:45:30 end swing-plan rc=0\n"
        "===== 2026-10-30 15:45:01 start swing-plan\nboom\n===== 2026-10-30 15:46:00 end swing-plan rc=1\n")
    monkeypatch.setattr(health, "ROOT", tmp_path)
    runs = health.last_runs()
    assert runs["swing-plan"][1] == 1 and runs["swing-plan"][0].day == 30
    assert "intraday" not in runs


def test_health_funds_line_warns_when_account_is_short(tmp_path, monkeypatch):
    health = _load("health")
    import trader.brokers.indstocks as ib
    from trader import config as C
    monkeypatch.setattr(health, "ROOT", tmp_path)
    cfg = C._merge(C.DEFAULTS, {"journal": "j.db", "capital": {"swing": 150000},
                                "strategies": {"trend_allocation": {"enabled": True, "live": True, "override_gate": True,
                                                                    "capital_share": 0.5},
                                               "momentum_rotation": {"enabled": True, "live": False,
                                                                     "capital_share": 0.5}}})
    monkeypatch.setattr(ib.IndStocksBroker, "available_funds", lambda self, p: 19_000.0)
    ok, warn = [], []
    health.funds_line(cfg, object(), ok, warn)                    # short: shown as attention, sized from the account
    assert warn and "₹19,000" in warn[0] and "₹75,000" in warn[0] and "about 25% of the plan" in warn[0]
    cfg["capital"]["live_from_account"] = False
    ok, warn = [], []
    health.funds_line(cfg, object(), ok, warn)
    assert warn and "cut down" in warn[0]
    monkeypatch.setattr(ib.IndStocksBroker, "available_funds", lambda self, p: 150_000.0)
    ok, warn = [], []
    health.funds_line(cfg, object(), ok, warn)
    assert ok and not warn


def test_health_holdings_line(tmp_path, monkeypatch):
    from datetime import datetime
    from guardian.broker import Holding
    from trader.journal import Journal
    from trader.models import CNC, Position
    health = _load("health")
    monkeypatch.setattr(health, "ROOT", tmp_path)
    j = Journal(tmp_path / "j.db", "live")
    j.save_position(Position("trend_allocation", "MON100", CNC, 58, 321.0, datetime(2026, 10, 6), "E1", "500"))
    j.close()

    class Client:
        def __init__(self, q):
            self.q = q

        def holdings(self):
            return [Holding("MON100", "500", self.q, 320.0)]

    ok, warn = [], []
    health.holdings_line({"journal": "j.db"}, Client(58), ok, warn)
    assert ok == ["Your holdings match the bot's records ✓: MON100 58"] and not warn
    ok, warn = [], []
    health.holdings_line({"journal": "j.db"}, Client(20), ok, warn)
    assert warn and "MON100: bot thinks 58, account has 20" in warn[0] and "reconcile --fix" in warn[0]


def test_health_tells_new_holdings_once(tmp_path, monkeypatch):
    from datetime import datetime
    from guardian.broker import Holding
    from trader.journal import Journal
    from trader.models import CNC, Position
    health = _load("health")
    monkeypatch.setattr(health, "ROOT", tmp_path)
    j = Journal(tmp_path / "j.db", "live")
    j.save_position(Position("momentum_rotation", "SBIN", CNC, 10, 800.0, datetime(2026, 10, 6), "E1", "3045"))
    j.close()
    cfg, warn = {"journal": "j.db"}, []
    health.new_holdings_line(cfg, [Holding("INFY", "1", 48, 1000.0)], warn)
    assert warn == []                                                  # first run: remembered, nothing said
    health.new_holdings_line(cfg, [], warn)                            # empty reply (glitch): memory kept
    now = [Holding("INFY", "1", 48, 1000.0), Holding("SBIN", "3045", 10, 800.0), Holding("TMCV", "9", 16, 300.0)]
    health.new_holdings_line(cfg, now, warn)
    assert len(warn) == 1 and "TMCV 16" in warn[0] and "SBIN" not in warn[0] and "INFY" not in warn[0]
    warn = []
    health.new_holdings_line(cfg, now, warn)
    assert warn == []                                                  # said once
    (tmp_path / "trader" / "state" / "known_holdings.json").write_text("{trunc")
    health.new_holdings_line(cfg, now, warn)
    assert warn == []                                                  # damaged memory: starts again, no false alarm
    health.new_holdings_line(cfg, now + [Holding("NEWCO", "77", 5, 10.0)], warn)
    assert len(warn) == 1 and "NEWCO 5" in warn[0]


def test_universe_job_is_scheduled_and_known_to_health():
    assert "universe)" in (OCI / "job.sh").read_text()
    assert "$PG universe" in (OCI / "crontab").read_text()
    assert "universe" in _load("health").JOBS
