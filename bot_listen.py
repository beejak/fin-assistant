"""
Telegram long-polling listener.

Receives messages sent directly to the bot and routes them to bot_query.
Only messages from OWNER_CHAT_ID are processed — everything else is silently
acknowledged (offset advanced) so the queue stays clear.

Run via:  python3 main.py listen
Or as a systemd service: fin-listen
"""
import logging
import time
from pathlib import Path

import requests

from config import BOT_TOKEN, OWNER_CHAT_ID, ROOT
import bot_query

log = logging.getLogger(__name__)

_OFFSET_FILE = ROOT / "store" / "listener_offset.txt"


def run() -> None:
    log.info("Telegram listener started — owner chat_id=%s", OWNER_CHAT_ID)
    url    = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    offset = _load_offset()

    while True:
        try:
            r = requests.get(
                url,
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            if not r.ok:
                log.warning("getUpdates HTTP %s — retrying in 5s", r.status_code)
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                _save_offset(offset)
                _process(update)

        except requests.Timeout:
            pass   # normal for long-polling; just loop again
        except Exception as e:
            log.error("Listener loop error: %s", e)
            time.sleep(5)


def _process(update: dict) -> None:
    msg     = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text    = (msg.get("text") or "").strip()

    if not text or chat_id != OWNER_CHAT_ID:
        return

    log.info("Query from owner: %s", text[:100])
    try:
        bot_query.handle(text, chat_id)
    except Exception as e:
        log.error("Query handler error: %s", e)
        from bot import send
        send(f"Error processing query: {e}", chat_id=chat_id)


def _load_offset() -> int:
    try:
        return int(_OFFSET_FILE.read_text())
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        _OFFSET_FILE.write_text(str(offset))
    except Exception:
        pass
