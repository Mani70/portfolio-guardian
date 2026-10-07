"""Thin client for the INDstocks REST API (read-only calls used by the guardian).

Docs: https://api-docs.indstocks.com/
- Base URL https://api.indstocks.com; header "Authorization: <access_token>".
- Read-only endpoints (holdings, quotes, historical, profile) do NOT need a whitelisted static IP.
- Tokens last 24h. Dashboard tokens are pasted into .env. TOTP tokens come from
  POST /generate/token, and ONLY THE NEWEST ONE IS VALID: every generation kills the previous
  token. So we cache the token on disk and reuse it, regenerating at most once per run.
- Error bodies come in several shapes; check the HTTP status first, then read
  error_type / debug_info / message / error.

This module never places orders. Phase 1 is alert-only by design.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.indstocks.com"

_MIN_INTERVAL_S = 0.25          # data & quote APIs allow 5 req/s; stay under it
_TOKEN_MAX_AGE_S = 23 * 3600    # docs: tokens live 24h; refresh an hour early
_TOKEN_MIN_GAP_S = 65           # /generate/token allows 1 call per 60s
_DAILY_RESET_HOUR_IST = 7       # dashboard: "all access tokens will reset daily at 7AM" (SEBI)
_RETRYABLE = {429, 500, 502, 503, 504}
_REGEN_GAP_S = 600              # one process logs in again at most every 10 minutes (no ping-pong between jobs)
_LOGIN_BACKOFF_S = 900          # after INDstocks refuses a login, no job tries again for 15 minutes: retrying
                                # every few minutes kept its bot-protection (HTTP 429) closed for an hour


def _last_reset_epoch(now: Optional[float] = None) -> float:
    """Epoch seconds of the most recent 07:00 IST."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    dt = datetime.fromtimestamp(now if now is not None else time.time(), ist)
    reset = dt.replace(hour=_DAILY_RESET_HOUR_IST, minute=0, second=0, microsecond=0)
    if dt < reset:
        reset -= timedelta(days=1)
    return reset.timestamp()


@contextmanager
def _token_lock(cache_path: Optional[Path]):
    """Only one process at a time reads-or-generates the shared token (Linux; a no-op where fcntl is missing)."""
    if not cache_path:
        yield
        return
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(cache_path.with_name(cache_path.name + ".lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _cached_token_usable(generated_at: float, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    return now - generated_at < _TOKEN_MAX_AGE_S and generated_at >= _last_reset_epoch(now)


@dataclass
class Holding:
    symbol: str
    security_id: str
    qty: float
    avg_price: float
    isin: str = ""
    used_qty: float = 0.0        # pledged, sold today or otherwise blocked: can't be sold again

    @property
    def invested(self) -> float:
        return self.qty * self.avg_price


@dataclass
class Candle:
    ts: int      # candle open time, epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float


class BrokerError(RuntimeError):
    def __init__(self, message: str, status: int = 0, error_type: str = ""):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class TokenError(BrokerError):
    """Access token missing, expired, replaced or revoked."""


def _error_detail(body: dict) -> str:
    for key in ("debug_info", "message", "error"):
        v = body.get(key)
        if v and v not in ("Bad Request", "Bad request"):
            return str(v)
    return str(body.get("message") or body)


def _find_token(body: dict) -> Optional[str]:
    """The docs say the field is `token`; the envelope isn't confirmed, so look in both places."""
    for scope in (body, body.get("data") if isinstance(body.get("data"), dict) else {}):
        for key in ("token", "access_token"):
            if isinstance(scope, dict) and scope.get(key):
                return str(scope[key])
    return None


class IndStocksClient:
    def __init__(self, access_token: Optional[str] = None, *, client_id: Optional[str] = None,
                 mpin: Optional[str] = None, totp_secret: Optional[str] = None,
                 exchange_prefix: str = "NSE", token_cache: Optional[Path] = None,
                 timeout: float = 15.0):
        self._env_token = access_token
        self._client_id = client_id
        self._mpin = mpin
        self._totp_secret = totp_secret
        self._cache_path = token_cache
        self.exchange_prefix = exchange_prefix
        self.timeout = timeout
        self._session = requests.Session()
        self._last_call = 0.0
        self._token: Optional[str] = None
        self._token_source = ""          # env | cache | generated
        self._last_generated = 0.0       # when THIS process last logged in again after a rejection (epoch s)

    @property
    def can_generate(self) -> bool:
        return bool(self._client_id and self._mpin and self._totp_secret)

    @classmethod
    def from_env(cls, exchange_prefix: str = "NSE", token_cache: Optional[Path] = None) -> "IndStocksClient":
        return cls(
            access_token=os.getenv("INDSTOCKS_ACCESS_TOKEN") or None,
            client_id=os.getenv("INDSTOCKS_CLIENT_ID") or None,
            mpin=os.getenv("INDSTOCKS_MPIN") or None,
            totp_secret=os.getenv("INDSTOCKS_TOTP_SECRET") or None,
            exchange_prefix=exchange_prefix,
            token_cache=token_cache,
        )

    # ---------- token handling ----------
    def _read_cache(self) -> Optional[dict]:
        if not self._cache_path or not self._cache_path.exists():
            return None
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def _write_cache(self, token: str) -> None:
        if not self._cache_path:
            return
        tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
        tmp.write_text(json.dumps({"token": token, "generated_at": time.time()}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._cache_path)               # readers never see a half-written file

    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        if self.can_generate:
            with _token_lock(self._cache_path):
                cached = self._read_cache()
                if cached and cached.get("token") and _cached_token_usable(cached.get("generated_at", 0)):
                    self._token, self._token_source = cached["token"], "cache"
                    return self._token
                self._token, self._token_source = self._generate_token(), "generated"
                return self._token
        if self._env_token:
            self._token, self._token_source = self._env_token, "env"
            return self._token
        raise TokenError(
            "No access token. Paste one from indstocks.com/app/api-trading/access-tokens into "
            "INDSTOCKS_ACCESS_TOKEN, or set INDSTOCKS_CLIENT_ID + INDSTOCKS_MPIN + INDSTOCKS_TOTP_SECRET.")

    def _backoff_path(self) -> Optional[Path]:
        return self._cache_path.with_name(self._cache_path.name + ".backoff") if self._cache_path else None

    def _check_backoff(self) -> None:
        p = self._backoff_path()
        if not p or not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            until = float(d.get("until", 0))
        except (ValueError, OSError, TypeError):
            return
        if time.time() < until:
            raise TokenError(f"Login to INDstocks paused until {time.strftime('%H:%M', time.localtime(until))}: it "
                             f"refused the last one ({str(d.get('why', ''))[:100]}). Trying sooner keeps it refusing.",
                             429, "TokenException")

    def _note_backoff(self, why: str) -> None:
        p = self._backoff_path()
        if p:
            try:
                p.write_text(json.dumps({"until": time.time() + _LOGIN_BACKOFF_S, "why": why[:200]}), encoding="utf-8")
            except OSError:
                pass

    def _generate_token(self) -> str:
        self._check_backoff()
        cached = self._read_cache()
        if cached and time.time() - cached.get("generated_at", 0) < _TOKEN_MIN_GAP_S:
            raise TokenError("A token was generated under a minute ago; INDstocks throttles to 1 per 60s. "
                             "Wait a minute and run again.")
        import pyotp  # lazy: only needed for TOTP login
        resp = self._session.post(
            f"{BASE_URL}/generate/token",
            headers={"x-api-key": self._client_id, "Content-Type": "application/json"},
            json={"mpin": self._mpin, "totp": pyotp.TOTP(self._totp_secret).now()},
            timeout=self.timeout,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"message": resp.text[:200]}
        if resp.status_code >= 400 or body.get("success") is False or body.get("status") == "error":
            # Never retry here: wrong codes count toward a 15-minute lockout, and repeated logins are rate-limited.
            detail = "rate-limited (HTML challenge page)" if "<html" in str(body.get("message", "")).lower() \
                else _error_detail(body)
            self._note_backoff(f"HTTP {resp.status_code}: {detail}")
            hint = ("INDstocks is limiting logins; every job waits 15 minutes before trying again."
                    if resp.status_code in (400, 429) and ("wait" in detail.lower() or "rate" in detail.lower()
                                                           or resp.status_code == 429)
                    else "Check MPIN/TOTP secret and that the clock is synced. Not retrying for 15 minutes, to "
                         "avoid a TOTP lockout.")
            raise TokenError(f"Token generation failed (HTTP {resp.status_code}): {detail}. {hint}", resp.status_code)
        token = _find_token(body)
        if not token:
            raise TokenError(f"Token generation returned no token field (keys: {list(body)})")
        self._write_cache(token)
        log.info("Generated a fresh access token via TOTP")
        return token

    def recover_token(self, rejected: str) -> bool:
        """After INDstocks rejected `rejected`: use a newer token another job already saved, else log in again
        (at most once per 10 minutes in this process). True if a different token is now in use.
        INDstocks keeps one token per account, so each login cancels the previous token. Without this, two
        all-day jobs would keep cancelling each other's tokens."""
        if self._token_source == "env" or not self.can_generate:
            return False
        with _token_lock(self._cache_path):
            cached = self._read_cache()
            if (cached and cached.get("token") and cached["token"] != rejected
                    and _cached_token_usable(cached.get("generated_at", 0))):
                self._token, self._token_source = cached["token"], "cache"
                log.warning("Access token rejected; using the newer one another job saved")
                return True
            if time.time() - self._last_generated < _REGEN_GAP_S:
                return False
            if cached and cached.get("token") == rejected and \
                    time.time() - cached.get("generated_at", 0) < _TOKEN_MIN_GAP_S:
                raise TokenError("A token generated under a minute ago was already rejected: something else is "
                                 "logging in to this INDstocks account with the same API credentials (another "
                                 "computer running the bot, or a new token made on the INDstocks website).",
                                 401, "TokenException")
            log.warning("Access token rejected; logging in again")
            self._last_generated = time.time()          # a failed attempt counts too: no retry storm
            self._token, self._token_source = self._generate_token(), "generated"
            return True

    # ---------- http ----------
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        for attempt in range(4):
            wait = _MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            token = self._ensure_token()
            try:
                resp = self._session.get(f"{BASE_URL}{path}", params=params,
                                         headers={"Authorization": token}, timeout=self.timeout)
            except requests.RequestException as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise BrokerError(f"Network error calling {path}: {e}")
            self._last_call = time.monotonic()

            if resp.status_code in (401, 403) and self._is_token_error(resp):
                if attempt < 3 and self.recover_token(token):
                    continue
                raise TokenError("Access token rejected (expired, replaced or revoked). "
                                 + ("Generate a new one on the dashboard and update .env."
                                    if self._token_source == "env" else
                                    "Logged in again less than 10 minutes ago, so not again now; the next run "
                                    "tries again. If this repeats, something else uses the same API login."),
                                 resp.status_code, "TokenException")
            if resp.status_code in _RETRYABLE and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            return self._parse(resp, path)
        raise BrokerError(f"Gave up on {path} after retries")

    @staticmethod
    def _is_token_error(resp: requests.Response) -> bool:
        try:
            body = resp.json()
        except ValueError:
            return True
        return body.get("error_type", "TokenException") == "TokenException"

    @staticmethod
    def _parse(resp: requests.Response, path: str = "") -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise BrokerError(f"{path}: HTTP {resp.status_code}, non-JSON response: {resp.text[:200]}",
                              resp.status_code)
        if resp.status_code >= 400 or body.get("success") is False or body.get("status") == "error":
            etype = body.get("error_type", "")
            raise BrokerError(f"{path}: HTTP {resp.status_code} {etype}: {_error_detail(body)}".replace("  ", " "),
                              resp.status_code, etype)
        return body

    # ---------- endpoints ----------
    def scrip_code(self, security_id: str, prefix: Optional[str] = None) -> str:
        sid = str(security_id)
        return sid if "_" in sid else f"{prefix or self.exchange_prefix}_{sid}"

    def profile(self) -> dict:
        return self._get("/user/profile").get("data", {})

    def holdings(self) -> List[Holding]:
        rows = self._get("/portfolio/holdings").get("data") or []
        out = []
        for r in rows:
            qty = float(r.get("total_qty") or 0)
            symbol = str(r.get("symbol") or "").strip().upper()
            sid = str(r.get("security_id") or "").strip()
            if qty <= 0:
                continue
            if not symbol or not sid:
                # e.g. mutual fund units held in demat: no exchange symbol, can't be quoted
                log.info("Skipping holding without symbol/security_id (ISIN %s, qty %g)", r.get("isin", "?"), qty)
                continue
            out.append(Holding(symbol=symbol, security_id=sid, qty=qty,
                               avg_price=float(r.get("avg_price") or 0), isin=str(r.get("isin") or ""),
                               used_qty=float(r.get("used_qty") or 0)))
        return out

    def raw_ltp(self, code: str) -> str:
        """Diagnostic: one LTP call, returning HTTP status and body text (no retries, no parsing)."""
        self._ensure_token()
        time.sleep(_MIN_INTERVAL_S)
        resp = self._session.get(f"{BASE_URL}/market/quotes/ltp", params={"scrip-codes": code},
                                 headers={"Authorization": self._token}, timeout=self.timeout)
        return f"HTTP {resp.status_code} {resp.text[:160]}"

    def ltp(self, scrip_codes: Iterable[str]) -> Dict[str, float]:
        """Live prices. One bad code makes INDstocks reject the whole batch (HTTP 400
        "Invalid scrip codes"), so on a 400 we retry code by code and skip the invalid ones."""
        codes = list(dict.fromkeys(scrip_codes))
        prices: Dict[str, float] = {}
        for i in range(0, len(codes), 1000):  # max 1000 per request
            batch = codes[i:i + 1000]
            try:
                prices.update(self._ltp_call(batch))
            except BrokerError as e:
                if e.status != 400 or len(batch) == 1:
                    raise
                log.warning("Batch quote rejected (%s); retrying one by one", e)
                for code in batch:
                    try:
                        prices.update(self._ltp_call([code]))
                    except BrokerError as e1:
                        if e1.status != 400:
                            raise
                        log.info("Invalid scrip code: %s", code)
        return prices

    def _ltp_call(self, codes: List[str]) -> Dict[str, float]:
        body = self._get("/market/quotes/ltp", {"scrip-codes": ",".join(codes)})
        return {code: float(q["live_price"]) for code, q in (body.get("data") or {}).items()
                if q and q.get("live_price") is not None}

    def equity_instruments(self) -> List[Dict[str, str]]:
        """Rows of the equity instrument master (raw CSV, not JSON)."""
        import csv
        import io
        self._ensure_token()
        for attempt in range(3):
            resp = self._session.get(f"{BASE_URL}/market/instruments", params={"source": "equity"},
                                     headers={"Authorization": self._token}, timeout=60)
            if resp.status_code in _RETRYABLE and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            break
        if resp.status_code >= 400:
            raise BrokerError(f"/market/instruments: HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
        return list(csv.DictReader(io.StringIO(resp.text)))

    def candles_many(self, scrip_codes: Iterable[str], interval: str,
                     start_ms: int, end_ms: int) -> Dict[str, List[Candle]]:
        """Up to 5 scrip codes per call (API limit). Codes with no data are simply absent."""
        codes = list(dict.fromkeys(scrip_codes))
        out: Dict[str, List[Candle]] = {}
        for i in range(0, len(codes), 5):
            batch = codes[i:i + 5]
            try:
                out.update(self._candles_call(batch, interval, start_ms, end_ms))
            except BrokerError as e:
                if e.status != 400 or len(batch) == 1:
                    raise
                log.warning("Historical batch rejected (%s); retrying one by one", e)
                for code in batch:
                    try:
                        out.update(self._candles_call([code], interval, start_ms, end_ms))
                    except BrokerError as e1:
                        if e1.status != 400:
                            raise
                        log.info("No history for %s: %s", code, e1)
        return out

    def _candles_call(self, codes: List[str], interval: str, start_ms: int, end_ms: int) -> Dict[str, List[Candle]]:
        body = self._get(f"/market/historical/{interval}",
                         {"scrip-codes": ",".join(codes), "start_time": start_ms, "end_time": end_ms})
        out: Dict[str, List[Candle]] = {}
        for code, series in (body.get("data") or {}).items():
            rows = (series or {}).get("candles") or []      # API sends null when a window has no trades
            parsed = []
            for c in rows:
                try:
                    parsed.append(Candle(int(c["ts"]), float(c["o"]), float(c["h"]), float(c["l"]),
                                         float(c["c"]), float(c.get("v") or 0)))
                except (KeyError, TypeError, ValueError):
                    continue                                 # skip malformed candles
            out[code] = parsed
        return out

    def candles(self, scrip_code: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]:
        return self.candles_many([scrip_code], interval, start_ms, end_ms).get(scrip_code, [])
