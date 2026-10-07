"""Where the bot's secrets come from.

On the OCI server they live in OCI Vault: ONE secret whose content is a JSON object with the keys below. The server
reads it with its own identity (instance principal: a dynamic group + a read-only policy), so no OCI key or password
is stored on it. `.env` then only needs the secret's OCID (not sensitive):

    OCI_SECRET_ID=ocid1.vaultsecret.oc1....

Order: values from Vault win; `.env` and the environment fill the rest (on the laptop, or while switching over).
A day's first Vault read is kept for the rest of that day in RAM only (/dev/shm, readable by you only, gone on
reboot), so a short Vault outage at 09:00 can't stop the trading jobs. If Vault can't be read and nothing is cached,
the values in `.env` are used if present and the morning health message warns.

  python -m guardian.secrets                    # which secrets were found and from where (never the values)
  python -m guardian.secrets --json-from-env    # on the laptop: print the Vault secret's JSON built from .env
                                                #   (pipe it to `clip` on Windows: ... --json-from-env | clip)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

log = logging.getLogger("guardian.secrets")

KEYS = ("INDSTOCKS_CLIENT_ID", "INDSTOCKS_MPIN", "INDSTOCKS_TOTP_SECRET", "INDSTOCKS_ACCESS_TOKEN",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OCI_BACKUP_PAR_URL")
SOURCE_VAR = "PG_SECRETS_SOURCE"
_loaded = False


def _today_ist() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()


def _cache_path() -> Optional[Path]:
    shm = Path("/dev/shm")
    if not shm.is_dir() or not hasattr(os, "getuid"):
        return None                                  # Windows / no tmpfs: no cache
    return shm / f"pg-secrets-{os.getuid()}.json"


def _read_cache(secret_id: str) -> Optional[Dict[str, str]]:
    p = _cache_path()
    try:
        if p is None or not p.exists() or (p.stat().st_mode & 0o077) or p.stat().st_uid != os.getuid():
            return None                              # missing, or readable by others: ignore it
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("day") == _today_ist() and d.get("secret_id") == secret_id and isinstance(d.get("values"), dict):
            return {k: str(v) for k, v in d["values"].items() if k in KEYS}
    except Exception:                                # noqa: BLE001 - a bad cache is just ignored
        pass
    return None


def _write_cache(secret_id: str, values: Dict[str, str]) -> None:
    p = _cache_path()
    if p is None:
        return
    try:
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"day": _today_ist(), "secret_id": secret_id, "values": values}, f)
        os.replace(tmp, p)
    except Exception as e:                           # noqa: BLE001
        log.warning("secret cache not written: %s", e)


def fetch_vault(secret_id: str) -> Dict[str, str]:
    """The secret's JSON from OCI Vault, read with the server's instance principal."""
    import oci                                       # server only (deploy/oci/requirements-server.txt)
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.secrets.SecretsClient(config={"region": signer.region}, signer=signer,
                                       retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY)
    bundle = client.get_secret_bundle(secret_id, stage="CURRENT").data
    return parse(base64.b64decode(bundle.secret_bundle_content.content).decode("utf-8"))


def parse(text: str) -> Dict[str, str]:
    d = json.loads(text)
    if not isinstance(d, dict):
        raise ValueError("the Vault secret must be a JSON object")
    unknown = sorted(set(d) - set(KEYS))
    if unknown:
        log.warning("Vault secret: unknown keys ignored: %s", ", ".join(unknown))
    return {k: str(v).strip() for k, v in d.items() if k in KEYS and v not in (None, "")}


def load_secrets(root: Path, fetch: Callable[[str], Dict[str, str]] = fetch_vault, force: bool = False) -> str:
    """Put the secrets into os.environ. Returns (and stores in PG_SECRETS_SOURCE) where they came from."""
    global _loaded
    if _loaded and not force:
        return os.environ.get(SOURCE_VAR, "")
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(root) / ".env")
    except ImportError:
        pass
    secret_id = os.environ.get("OCI_SECRET_ID", "").strip()
    source = "env"
    if secret_id:
        values = _read_cache(secret_id)
        if values is not None:
            source = "vault (today's cached read)"
        else:
            try:
                values = fetch(secret_id)
                _write_cache(secret_id, values)
                source = "vault"
            except Exception as e:                   # noqa: BLE001 - fall back to .env, and say so
                log.warning("OCI Vault not readable (%s); using .env values if present", e)
                source = f"env (VAULT FAILED: {str(e)[:120]})"
                values = None
        if values:
            os.environ.update(values)                # Vault wins over anything left in .env
    os.environ[SOURCE_VAR] = source
    _loaded = True
    return source


def dotenv_secrets(root: Path) -> list:
    """Secret keys still written in .env (to remove once Vault works)."""
    p = Path(root) / ".env"
    try:
        from dotenv import dotenv_values
        return [k for k, v in dotenv_values(p).items() if k in KEYS and v]
    except Exception:                                # noqa: BLE001
        return []


def status(root: Path) -> str:
    source = load_secrets(root)
    found = [k for k in KEYS if os.environ.get(k)]
    lines = [f"secrets from: {source}", "found: " + (", ".join(found) or "none")]
    left = dotenv_secrets(root) if source.startswith("vault") else []
    if left:
        lines.append("still in .env (remove them now that Vault works): " + ", ".join(left))
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent
    if "--json-from-env" in argv:
        from dotenv import dotenv_values
        vals = {k: v for k, v in dotenv_values(root / ".env").items() if k in KEYS and v}
        vals.pop("INDSTOCKS_ACCESS_TOKEN", None)          # a pasted daily token does not belong in Vault
        sys.stdout.write(json.dumps(vals))
        return 0
    print(status(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
