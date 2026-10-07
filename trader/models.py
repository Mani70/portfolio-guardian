"""Plain data types shared by strategies, risk checks, brokers and the journal."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

BUY, SELL = "BUY", "SELL"
CNC, INTRADAY = "CNC", "INTRADAY"

# order states we keep (INDstocks has more; see brokers/indstocks.py for the mapping)
NEW, OPEN, FILLED, PARTIAL, CANCELLED, REJECTED, UNKNOWN = (
    "NEW", "OPEN", "FILLED", "PARTIAL", "CANCELLED", "REJECTED", "UNKNOWN")
CANCEL_SENT = "CANCEL_SENT"      # cancel requested; final state (and any last fills) comes from the next sync
TERMINAL = {FILLED, CANCELLED, REJECTED}
ACTIVE = [NEW, OPEN, PARTIAL, UNKNOWN, CANCEL_SENT]


@dataclass
class Signal:
    """What a strategy wants. It becomes an order only after the strength gate and risk checks."""
    strategy: str
    symbol: str
    side: str                    # BUY | SELL
    kind: str                    # entry | exit
    strength: float              # strategy's own score; compared with its min_strength
    ref_price: float             # price the strategy saw when it decided
    time: datetime
    product: str = CNC           # CNC (swing, delivery) | INTRADAY
    stop: Optional[float] = None
    target: Optional[float] = None
    reason: str = ""

    @property
    def id(self) -> str:
        return f"{self.strategy}|{self.symbol}|{self.side}|{self.kind}|{self.time:%Y%m%d%H%M}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = self.time.isoformat()
        d["id"] = self.id
        return d


@dataclass
class OrderRequest:
    tag: str                     # our id; sent to INDstocks as `remarks` for reconciliation
    strategy: str
    symbol: str
    security_id: str
    side: str
    qty: int
    product: str
    limit_price: float
    signal_id: str
    kind: str                    # entry | exit
    created: datetime
    stop: Optional[float] = None         # protective stop leg (intraday smart orders)
    target: Optional[float] = None
    ref_price: float = 0.0
    amo: bool = False            # after-market order: goes into the next session's opening auction


@dataclass
class Order:
    req: OrderRequest
    status: str = NEW
    broker_id: str = ""
    child_id: str = ""           # GTT stop/target leg id (live smart orders)
    filled_qty: int = 0
    avg_price: float = 0.0
    charges: float = 0.0
    message: str = ""
    updated: Optional[datetime] = None


@dataclass
class Position:
    strategy: str
    symbol: str
    product: str
    qty: int                     # > 0 long, < 0 short
    avg_price: float
    entry_time: datetime
    entry_tag: str
    security_id: str = ""
    stop: Optional[float] = None
    target: Optional[float] = None
    entry_charges: float = 0.0
    child_id: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "long" if self.qty > 0 else "short"
