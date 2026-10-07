"""Remembers which alerts already fired so you get one message per breach, not one per run.

An alert re-arms once its condition clears (e.g. price recovers above the level).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .rules import Alert


class AlertState:
    def __init__(self, path: Path):
        self.path = path
        self.active: Dict[str, str] = {}
        if path.exists():
            try:
                self.active = json.loads(path.read_text(encoding="utf-8")).get("active", {})
            except (ValueError, OSError):
                self.active = {}

    def diff(self, alerts: List[Alert], repeat: bool = False) -> Tuple[List[Alert], List[str]]:
        """Return (new_alerts_to_send, rule_ids_that_cleared)."""
        current = {a.rule_id for a in alerts}
        new = alerts if repeat else [a for a in alerts if a.rule_id not in self.active]
        cleared = [rid for rid in self.active if rid not in current]
        return new, cleared

    def save(self, alerts: List[Alert]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.active = {a.rule_id: self.active.get(a.rule_id, now) for a in alerts}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active": self.active}, indent=2), encoding="utf-8")
