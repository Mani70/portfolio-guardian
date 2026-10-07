"""Send a short Telegram alert with the last lines of a log file.  Used by job.sh when a job fails.

  python deploy/oci/alert.py "message" [logfile]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# after any secret-like word, the rest of the line is masked before it leaves the server (over-masking is fine)
SECRET = re.compile(r"(?i)\b(\w*(?:token|secret|mpin|authorization|bearer|password|totp|api[_-]?key)\w*)\b.*")


def tail(path: Path, n: int = 12) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    body = [l for l in lines if not l.startswith("=====")][-n:]
    return SECRET.sub(r"\1 ***", "\n".join(body))[-2500:]


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    try:
        from guardian.secrets import load_secrets
        load_secrets(ROOT)
    except ImportError:
        pass
    from guardian.notifier import Notifier
    text = f"🚨 {argv[0]}"
    if len(argv) > 1:
        t = tail(Path(argv[1]))
        if t:
            text += f"\n\nLast lines of {Path(argv[1]).name}:\n{t}"
    Notifier().send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
