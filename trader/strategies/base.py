"""Strategy interfaces. Strategies only read prices and return Signals; they never place orders."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from ..models import Position, Signal


class SwingStrategy:
    engine = "swing"
    description = ""

    def __init__(self, name: str, params: Optional[dict] = None, min_strength: float = float("-inf")):
        self.name = name
        self.params = params or {}
        self.min_strength = min_strength
        self.journal = None          # set by the engine: persistent key/value state via get/put
        self.sessions = None         # set in replay: exact trading days (live uses weekdays minus holidays)
        self.holidays = set()        # set by the engine: NSE trading holidays (trader/holidays.py)

    def state_get(self, key, default=None):
        return self.journal.get(f"{self.name}:{key}", default) if self.journal is not None else default

    def state_put(self, key, value) -> None:
        if self.journal is not None:
            self.journal.put(f"{self.name}:{key}", value)

    def slots(self) -> int:
        return int(self.params.get("slots", 10))

    def commit(self, ok: bool = True) -> None:
        """Called by the engine after the orders for a rebalance were sent. If the broker rejected some,
        the period stays open and the strategy runs again the next evening."""
        period = getattr(self, "_pending_period", None)
        if ok and period:
            self.state_put("last_period", period)
        self._pending_period = None

    def symbols(self, engine_universe: List[str]) -> List[str]:
        """The instruments this strategy trades (default: the engine's stock universe)."""
        return list(engine_universe)

    def due_period(self, d: date) -> Optional[str]:
        """The rebalance period to run on session d, or None.
        - normally the last session of each month (missed ones are caught up in the first week);
        - `_force` (preview): always;
        - `_start_now` (the scheduled swing-plan): a strategy that has never traded starts on its first run
          instead of waiting for month-end. That first run is labelled as LAST month's period, so this
          month's month-end rebalance still happens. Replays don't set it, so they match the research.
        - params `review_session` (k or "last"): reviewed on the k-th session of each month instead (tranches,
          trader/experiments.yaml); such a strategy always waits for its own day.
        """
        from .momentum import last_session_of_month, rebalance_period, review_period
        last = self.state_get("last_period")
        k = self.params.get("review_session")
        if k is not None:
            # a tranche reviewed on a fixed session of the month; it starts on its own day (no _start_now)
            period = review_period(d, k, self.sessions, last, self.holidays)
            if self.params.get("_force"):
                return period or f"{d:%Y-%m}#s{k}"
            return period
        period = rebalance_period(d, self.sessions, last, self.holidays)
        if self.params.get("_force"):
            return period or f"{d:%Y-%m}"
        month_end = last_session_of_month(d, self.sessions, self.holidays)
        if last is None and not month_end:
            return f"{d.replace(day=1) - timedelta(days=1):%Y-%m}" if self.params.get("_start_now") else None
        return period

    def signals(self, frames: Dict[str, pd.DataFrame], bench: Optional[pd.DataFrame], d: date,
                held: Dict[str, Position], now: datetime) -> List[Signal]:
        """Called after the close of session `d` with daily bars up to and including `d`."""
        raise NotImplementedError


class IntradayStrategy:
    engine = "intraday"
    description = ""

    def symbols(self, engine_universe: List[str]) -> List[str]:
        return list(engine_universe)

    def __init__(self, name: str, params: Optional[dict] = None, min_strength: float = float("-inf")):
        self.name = name
        self.params = params or {}
        self.min_strength = min_strength

    def prepare(self, day: date, daily: Dict[str, pd.DataFrame], symbols: List[str]) -> None:
        """Before the open: daily bars up to the PREVIOUS session only."""

    def on_bar(self, now: datetime, bars: Dict[str, pd.DataFrame], held: Dict[str, Position]) -> List[Signal]:
        """After each completed 5-minute bar. `bars` holds today's completed bars per symbol."""
        raise NotImplementedError
