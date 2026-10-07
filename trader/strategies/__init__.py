"""Strategy registry. trader.yaml refers to strategies by these names."""
from __future__ import annotations

from typing import List

from .gap_fade import GapFade
from .gap_reversal import GapReversal
from .gtaa import TrendAllocation
from .momentum import MomentumRotation

REGISTRY = {
    "momentum_rotation": MomentumRotation,
    "trend_allocation": TrendAllocation,
    "gap_reversal": GapReversal,
    "gap_fade": GapFade,
}


def build(cfg: dict) -> List:
    out = []
    for name, sc in (cfg.get("strategies") or {}).items():
        if not sc or not sc.get("enabled", False):
            continue
        cls = REGISTRY.get(sc.get("class", name))
        if cls is None:
            raise ValueError(f"Unknown strategy '{name}' in trader.yaml (known: {', '.join(REGISTRY)})")
        ms = sc.get("min_strength")
        out.append(cls(name, dict(sc.get("params") or {}), float("-inf") if ms is None else float(ms)))
    return out
