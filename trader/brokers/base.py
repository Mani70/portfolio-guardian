from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from backtest.engine import Costs
from backtest.intraday import IntradayCosts

from ..models import CNC, Order


@dataclass
class Fill:
    tag: str                 # our order tag (child legs carry the parent's tag)
    qty: int                 # filled in this event (always positive)
    price: float
    time: datetime
    charges: float
    leg: str = "main"        # main | stop | target
    side: str = ""


# liquid (debt) ETFs where idle cash is parked: no STT, a very tight market (research/cash_parking.py)
LIQUID_COSTS = Costs(stt_pct=0.0, slippage_pct=0.02, dp_per_sell=21.83)


def is_liquid(symbol: str) -> bool:
    return str(symbol or "").upper().startswith("LIQUID")


class CostModel:
    """Statutory charges + brokerage per fill (backtest classes, so paper and research agree)."""

    def __init__(self, delivery: Optional[Costs] = None, intraday: Optional[IntradayCosts] = None):
        self.delivery = delivery or Costs()
        self.intraday = intraday or IntradayCosts()

    def _model(self, product: str, symbol: str = ""):
        if product == CNC and is_liquid(symbol):
            return LIQUID_COSTS
        return self.delivery if product == CNC else self.intraday

    def charges(self, product: str, side: str, value: float, symbol: str = "") -> float:
        m = self._model(product, symbol)
        return m.buy(value) if side == "BUY" else m.sell(value)

    def slippage_pct(self, product: str, symbol: str = "") -> float:
        return self._model(product, symbol).slippage_pct


class Broker:
    """place() sends an order; update() returns new fills since the last call."""
    name = "base"
    live = False

    def place(self, order: Order, now: datetime) -> Order:
        raise NotImplementedError

    def cancel(self, order: Order, now: datetime) -> Order:
        raise NotImplementedError

    def cancel_child(self, order: Order, now: datetime) -> None:
        """Cancel a stop/target leg that belongs to an entry order (no-op if none)."""

    def update(self, orders: List[Order], now: datetime) -> List[Fill]:
        raise NotImplementedError

    def available_funds(self, product: str) -> Optional[float]:
        return None
