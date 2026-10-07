"""Symbol -> security_id and tick size, from the INDstocks equity instrument master (cached daily)."""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

DEFAULT_TICK = 0.05


def _tick(raw: str) -> float:
    """INDstocks' master resembles Dhan's, where TICK_SIZE is in paise (5 = ₹0.05). Values below 1 are
    taken as rupees. A wrong tick only gets an order rejected (never a bad fill)."""
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TICK
    if t <= 0:
        return DEFAULT_TICK
    return t / 100 if t >= 1 else t


class Instruments:
    def __init__(self, table: Dict[str, Tuple[str, float]]):
        self.table = table          # symbol -> (security_id, tick)

    @classmethod
    def from_rows(cls, rows) -> "Instruments":
        rank = {"EQ": 0, "BE": 1, "BZ": 2}
        best: Dict[str, Tuple[int, str, float]] = {}
        for r in rows:
            if (r.get("EXCH") or "").strip().upper() != "NSE":
                continue
            if (r.get("EXPIRY_CODE") or "0").strip() not in ("0", ""):
                continue
            sid = (r.get("SECURITY_ID") or "").strip()
            if not sid:
                continue
            pr = rank.get((r.get("SERIES") or "").strip().upper(), 5)
            names = {(r.get("SYMBOL_NAME") or "").strip().upper(), (r.get("TRADING_SYMBOL") or "").strip().upper()}
            names |= {n[:-3] for n in names if n.endswith("-EQ")}
            for n in filter(None, names):
                if n not in best or pr < best[n][0]:
                    best[n] = (pr, sid, _tick(r.get("TICK_SIZE")))
        return cls({n: (v[1], v[2]) for n, v in best.items()})

    @classmethod
    def load(cls, client, cache_dir: Path) -> "Instruments":
        cache = Path(cache_dir) / f"instruments_nse_{date.today():%Y%m%d}.json"
        if cache.exists():
            return cls({k: tuple(v) for k, v in json.loads(cache.read_text(encoding="utf-8")).items()})
        inst = cls.from_rows(client.equity_instruments())
        cache.parent.mkdir(parents=True, exist_ok=True)
        for old in cache.parent.glob("instruments_nse_*.json"):
            try:
                old.unlink()
            except OSError:
                pass
        cache.write_text(json.dumps(inst.table), encoding="utf-8")
        return inst

    def security_id(self, symbol: str) -> Optional[str]:
        v = self.table.get(symbol.upper())
        return v[0] if v else None

    def tick(self, symbol: str) -> float:
        v = self.table.get(symbol.upper())
        return v[1] if v else DEFAULT_TICK


def round_tick(price: float, tick: float, how: str = "nearest") -> float:
    """Round to a valid price. 'up' for buy limits (never below the intended price), 'down' for sells."""
    n = price / tick
    n = math.ceil(n - 1e-9) if how == "up" else math.floor(n + 1e-9) if how == "down" else round(n)
    return round(n * tick, 4)
