"""Broader test universes, chosen without hindsight.

`liquid`: every NSE equity (series EQ, ETFs excluded), ranked by median daily traded value
(close x volume) over the year BEFORE the test starts, keeping the top N. Choosing by liquidity at the
start, not today's index membership, avoids picking stocks because they later did well.
It still only contains companies listed today (delisted ones are missing), so some survivorship
bias remains.
"""
from __future__ import annotations

import re
from typing import Dict, List

import pandas as pd

NOT_STOCKS = re.compile(r"(BEES|ETF|IETF|LIQUID|GOLD|SILVER|NIFTY|SENSEX|BHARATBOND|MAFANG|MON100|MOM\d|NV20|"
                        r"CPSE|PSUBNK|BANKBEES|INAV|-RE\d?$)")


def nse_equity_symbols(client) -> List[str]:
    out = set()
    for row in client.equity_instruments():
        if (row.get("EXCH") or "").strip().upper() != "NSE":
            continue
        if (row.get("SERIES") or "").strip().upper() != "EQ":
            continue
        if (row.get("EXPIRY_CODE") or "0").strip() not in ("0", ""):
            continue
        sym = (row.get("TRADING_SYMBOL") or row.get("SYMBOL_NAME") or "").strip().upper()
        sym = sym[:-3] if sym.endswith("-EQ") else sym
        if sym and not NOT_STOCKS.search(sym):
            out.add(sym)
    return sorted(out)


def liquid_top(frames: Dict[str, pd.DataFrame], start: pd.Timestamp, top: int = 200,
               window: int = 250, min_days: int = 200) -> List[str]:
    """Top `top` symbols by median traded value in the `window` sessions before `start`."""
    value = {}
    for s, df in frames.items():
        pre = df.loc[:start].iloc[-window - 1:-1]
        if len(pre) < min_days:
            continue
        value[s] = float((pre["close"] * pre["volume"]).median())
    return [s for s, _ in sorted(value.items(), key=lambda kv: -kv[1])[:top]]
