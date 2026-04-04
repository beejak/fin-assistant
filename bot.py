"""Telegram bot sender."""
import logging
import requests
from config import BOT_TOKEN, OWNER_CHAT_ID

log = logging.getLogger(__name__)


def send(text: str, chat_id: int | None = None, dry_run: bool = False) -> None:
    if dry_run:
        print(text)
        return
    cid = chat_id or OWNER_CHAT_ID
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    import time
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        r = requests.post(url, json={"chat_id": cid, "text": chunk,
                                     "parse_mode": "HTML"}, timeout=15)
        if not r.ok:
            log.error("Telegram send failed: %s", r.text[:200])
        time.sleep(0.3)
