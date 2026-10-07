"""Daily backup (weekdays 16:30): trading journal + configs + state, as backups/pg-YYYYMMDD.tar.gz.
Keeps the last 30 locally. If OCI_BACKUP_PAR_URL is set in .env (a write-only pre-authenticated
request URL for an Object Storage bucket, ending in /o/), each backup is also uploaded there, so it
survives losing the server. Secrets (.env, token cache) are never included.

  python deploy/oci/backup.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
KEEP = 30
FILES = ["trader.yaml", "config.yaml", "state.json"]          # small, no secrets


def snapshot_db(src: Path, dst: Path) -> None:
    """Consistent copy even while another process is writing (SQLite online backup)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(str(dst))
    with d:
        s.backup(d)
    s.close()
    d.close()


def build(out_dir: Path, today: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"pg-{today}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(target, "w:gz") as tar:
        state = ROOT / "trader" / "state"
        for p in sorted(state.glob("*")) if state.exists() else []:
            if p.suffix == ".db":
                copy = Path(tmp) / p.name
                snapshot_db(p, copy)
                tar.add(copy, arcname=f"trader/state/{p.name}")
            elif p.is_file() and not p.name.endswith(("-journal", "-wal", "-shm")):
                tar.add(p, arcname=f"trader/state/{p.name}")
        for name in FILES:
            if (ROOT / name).exists():
                tar.add(ROOT / name, arcname=name)
    os.chmod(target, 0o600)
    return target


def prune(out_dir: Path, keep: int = KEEP) -> int:
    old = sorted(out_dir.glob("pg-*.tar.gz"))[:-keep]
    for p in old:
        p.unlink()
    return len(old)


def upload(path: Path, par_url: str) -> None:
    import requests
    url = par_url.rstrip("/") + "/" + path.name
    with path.open("rb") as f:
        r = requests.put(url, data=f, timeout=120, headers={"Content-Type": "application/gzip"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:200]}")


def main() -> int:
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    out = ROOT / "backups"
    target = build(out, datetime.now().strftime("%Y%m%d"))
    print(f"Backup written: {target} ({target.stat().st_size / 1024:.0f} KB)")
    removed = prune(out)
    if removed:
        print(f"Removed {removed} old backup(s)")
    par = os.getenv("OCI_BACKUP_PAR_URL", "").strip()
    if par:
        upload(target, par)
        print("Uploaded to Object Storage")
    else:
        print("OCI_BACKUP_PAR_URL not set: local copy only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
