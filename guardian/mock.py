"""Offline stand-in for IndStocksClient, fed from sample_data/*.json.

Lets you test rules and alert formatting without an access token.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from .broker import Candle, Holding


class MockClient:
    def __init__(self, data_dir: Path, exchange_prefix: str = "NSE"):
        self.exchange_prefix = exchange_prefix
        self._holdings = json.loads((data_dir / "holdings.json").read_text(encoding="utf-8"))
        self._prices = json.loads((data_dir / "prices.json").read_text(encoding="utf-8"))
        self._candles = json.loads((data_dir / "candles.json").read_text(encoding="utf-8"))

    def holdings(self) -> List[Holding]:
        return [Holding(symbol=r["symbol"].upper(), security_id=str(r["security_id"]),
                        qty=float(r["total_qty"]), avg_price=float(r["avg_price"]))
                for r in self._holdings["data"] if float(r["total_qty"]) > 0]

    def ltp(self, scrip_codes: Iterable[str]) -> Dict[str, float]:
        return {c: float(self._prices[c]) for c in scrip_codes if c in self._prices}

    def scrip_code(self, security_id: str, prefix: str = None) -> str:
        sid = str(security_id)
        return sid if "_" in sid else f"{prefix or self.exchange_prefix}_{sid}"

    def equity_instruments(self) -> List[dict]:
        return []

    def profile(self) -> dict:
        return {"first_name": "Sample", "ucc": "MOCK"}

    def candles_many(self, scrip_codes: Iterable[str], interval: str,
                     start_ms: int, end_ms: int) -> Dict[str, List[Candle]]:
        out = {}
        for code in scrip_codes:
            rows = self._candles.get(interval, {}).get(code)
            if rows:
                out[code] = [Candle(int(r["ts"]), r["o"], r["h"], r["l"], r["c"], r.get("v", 0)) for r in rows]
        return out
