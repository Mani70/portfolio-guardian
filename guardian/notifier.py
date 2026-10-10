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

    def send_audio(self, path, caption: str = "", title: str = "") -> bool:
        """An audio clip to the Telegram chat: a local file, or an https URL Telegram fetches itself."""
        print(f"[audio] {path}\n{caption}")
        if self.dry_run or not (self.tg_token and self.tg_chat):
            return False
        data = {"chat_id": self.tg_chat, "caption": caption[:1000], "title": title[:60]}
        url = f"https://api.telegram.org/bot{self.tg_token}/sendAudio"
        try:
            if str(path).startswith("https://"):
                r = requests.post(url, data={**data, "audio": str(path)}, timeout=120)
            else:
                with open(path, "rb") as fh:
                    r = requests.post(url, data=data, files={"audio": fh}, timeout=120)
            if r.status_code != 200:
                log.error("Telegram audio failed: HTTP %s %s", r.status_code, r.text[:200])
            return r.status_code == 200
        except (OSError, requests.RequestException) as e:
            log.error("Telegram audio failed: %s", e)
            return False

    def send_video(self, path, caption: str = "") -> bool:
        """A video file (e.g. the daily Reel) to the Telegram chat. True if Telegram accepted it."""
        print(f"[video] {path}\n{caption}")
        if self.dry_run or not (self.tg_token and self.tg_chat):
            return False
        try:
            with open(path, "rb") as fh:
                r = requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendVideo",
                                  data={"chat_id": self.tg_chat, "caption": caption[:1000], "supports_streaming": "true"},
                                  files={"video": fh}, timeout=180)
            if r.status_code != 200:
                log.error("Telegram video failed: HTTP %s %s", r.status_code, r.text[:200])
            return r.status_code == 200
        except (OSError, requests.RequestException) as e:
            log.error("Telegram video failed: %s", e)
            return False
