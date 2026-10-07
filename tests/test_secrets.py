"""guardian/secrets.py: Vault first, today's RAM cache, .env fallback; never prints values."""
import json
import os
import stat

import pytest

from guardian import secrets as S

KEYS_CLEAN = list(S.KEYS) + ["OCI_SECRET_ID", S.SOURCE_VAR]


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in KEYS_CLEAN:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(S, "_loaded", False)
    monkeypatch.setattr(S, "_cache_path", lambda: tmp_path / "shm" / "pg-secrets.json")
    (tmp_path / "shm").mkdir()
    yield


def test_vault_wins_is_cached_for_the_day_and_env_is_the_fallback(tmp_path):
    (tmp_path / ".env").write_text("OCI_SECRET_ID=ocid1.vaultsecret.x\nTELEGRAM_CHAT_ID=old\nINDSTOCKS_MPIN=old\n")
    calls = []

    def fetch(sid):
        calls.append(sid)
        return S.parse(json.dumps({"TELEGRAM_CHAT_ID": "42", "INDSTOCKS_MPIN": "9999", "junk": "x"}))

    assert S.load_secrets(tmp_path, fetch) == "vault"
    assert os.environ["TELEGRAM_CHAT_ID"] == "42" and os.environ["INDSTOCKS_MPIN"] == "9999" and calls
    cache = tmp_path / "shm" / "pg-secrets.json"
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert S.load_secrets(tmp_path, lambda sid: 1 / 0, force=True) == "vault (today's cached read)"
    cache.unlink()
    os.environ.pop("TELEGRAM_CHAT_ID")
    src = S.load_secrets(tmp_path, lambda sid: (_ for _ in ()).throw(RuntimeError("401 not authorized")), force=True)
    assert src.startswith("env (VAULT FAILED: 401") and os.environ["TELEGRAM_CHAT_ID"] == "old"
    assert "INDSTOCKS_MPIN" in S.dotenv_secrets(tmp_path)


def test_no_secret_id_means_plain_env_and_status_never_shows_values(tmp_path, capsys):
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=supersecret\nINDSTOCKS_ACCESS_TOKEN=tok\n")
    assert S.load_secrets(tmp_path, lambda sid: 1 / 0) == "env"
    text = S.status(tmp_path)
    assert "TELEGRAM_BOT_TOKEN" in text and "supersecret" not in text
    with pytest.raises(ValueError):
        S.parse("[1, 2]")


def test_a_cache_readable_by_others_or_from_another_day_is_ignored(tmp_path):
    cache = tmp_path / "shm" / "pg-secrets.json"
    cache.write_text(json.dumps({"day": S._today_ist(), "secret_id": "s", "values": {"TELEGRAM_CHAT_ID": "1"}}))
    os.chmod(cache, 0o644)
    assert S._read_cache("s") is None
    os.chmod(cache, 0o600)
    assert S._read_cache("s") == {"TELEGRAM_CHAT_ID": "1"} and S._read_cache("other") is None
    cache.write_text(json.dumps({"day": "2000-01-01", "secret_id": "s", "values": {"TELEGRAM_CHAT_ID": "1"}}))
    os.chmod(cache, 0o600)
    assert S._read_cache("s") is None


def test_json_from_env_leaves_out_the_daily_token(tmp_path, monkeypatch, capsys):
    (tmp_path / "guardian").mkdir()
    monkeypatch.setattr(S, "__file__", str(tmp_path / "guardian" / "secrets.py"))
    (tmp_path / ".env").write_text("INDSTOCKS_CLIENT_ID=c\nINDSTOCKS_ACCESS_TOKEN=tok\nOTHER=x\n")
    S.main(["--json-from-env"])
    assert json.loads(capsys.readouterr().out) == {"INDSTOCKS_CLIENT_ID": "c"}
