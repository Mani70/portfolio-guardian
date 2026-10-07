"""Where alerts go: Telegram (if configured) and always the console/log."""
from __future__ import annotations

import logging
import os
from typing import List

import requests

from .rules import Alert

log = logging.getLogger(__name__)
ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}


def format_alerts(alerts: List[Alert], header: str) -> str:
    lines = [header]
    for a in alerts:
        lines.append(f"\n{ICON.get(a.severity, '•')} {a.title}\n{a.detail}")
    lines.append("\nAlert-only: no orders were placed.")
    return "\n".join(lines)


class Notifier:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.getenv("TELEGRAM_CHAT_ID")

    def send(self, text: str) -> None:
        print(text)
        if self.dry_run:
            log.info("Dry run: not sending to Telegram")
            return
        if not (self.tg_token and self.tg_chat):
            log.info("Telegram not configured; printed to console only")
            return
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                              json={"chat_id": self.tg_chat, "text": text[:4000]}, timeout=15)
            if r.status_code != 200:
                log.error("Telegram send failed: HTTP %s %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.error("Telegram send failed: %s", e)
