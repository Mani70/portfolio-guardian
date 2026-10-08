"""Nightly deploy from GitHub (weekdays 17:15 IST, deploy/oci/crontab): if `main` has new commits, test them in a
separate checkout and switch the server to them only if every test passes.

  .venv/bin/python deploy/oci/autodeploy.py [--dry-run]

- Nothing new on main: does nothing, says nothing.
- A trading job still running, or code edited by hand on the server, or main's history rewritten: no deploy, a
  Telegram alert explains why (the running version stays).
- New code: requirements installed, the tests run in a temporary checkout of origin/main; on success the server
  fast-forwards to it and reinstalls the schedule; on failure it stays on the running version and alerts with the
  failing tests. Server data (.env, trader.yaml, journal, logs, backups, cache) is git-ignored and never touched.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PY = sys.executable


def sh(*cmd, cwd: Path = ROOT, check: bool = True, input_text: str = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=cwd, check=check, text=True, capture_output=True, input=input_text)


def notify(text: str, dry_run: bool) -> None:
    print(text)
    if dry_run:
        return
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
        from guardian.notifier import Notifier
        Notifier().send(text)
    except Exception as e:                                   # noqa: BLE001 - the log still has it
        print(f"(Telegram failed: {e})")


def trading_job_running() -> bool:
    r = sh("pgrep", "-f", "trader.run|guardian.main", check=False)
    return bool(r.stdout.strip())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    sh("git", "fetch", "-q", "origin", "main")
    head = sh("git", "rev-parse", "HEAD").stdout.strip()
    new = sh("git", "rev-parse", "origin/main").stdout.strip()
    if head == new:
        print("up to date")
        return 0
    log = sh("git", "log", "--format=- %s", f"{head}..{new}").stdout.strip()
    if trading_job_running():
        notify("Deploy postponed: a trading job is still running; tried again tomorrow evening.", dry)
        return 0
    if sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        notify("Deploy NOT done: code on the server was edited by hand (`git status` shows changes). "
               "Put the change on GitHub or undo it (`git checkout -- .`); the running version stays.", dry)
        return 1
    if sh("git", "merge-base", "--is-ancestor", head, new, check=False).returncode != 0:
        notify("Deploy NOT done: GitHub main no longer contains the running version (history rewritten). "
               "Check it by hand; the running version stays.", dry)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="pg-deploy-")) / "co"
    sh("git", "worktree", "add", "--detach", str(tmp), new)
    try:
        sh(PY, "-m", "pip", "install", "-q", "-r", str(tmp / "requirements.txt"),
           "-r", str(tmp / "deploy" / "oci" / "requirements-server.txt"))
        t = sh(PY, "-m", "pytest", "-q", "tests", cwd=tmp, check=False)
        if t.returncode != 0:
            tail = "\n".join((t.stdout + t.stderr).strip().splitlines()[-12:])
            notify(f"Deploy NOT done: the new code's tests fail; the running version stays.\nNew commits:\n{log}\n\n"
                   f"{tail[-1500:]}", dry)
            return 1
        if dry:
            print(f"dry run: tests pass; would deploy {new[:7]}")
            return 0
        sh("git", "merge", "-q", "--ff-only", new)
        cron = (ROOT / "deploy" / "oci" / "crontab").read_text(encoding="utf-8").replace("__APP__", str(ROOT))
        sh("crontab", "-", input_text=cron)
        passed = (t.stdout.strip().splitlines() or ["tests passed"])[-1]
        notify(f"Deployed {new[:7]} ({passed}):\n{log}", dry)
        return 0
    finally:
        sh("git", "worktree", "remove", "--force", str(tmp), check=False)


if __name__ == "__main__":
    sys.exit(main())
