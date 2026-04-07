"""Telegram bot sender."""
import time
import logging
import requests
from config import BOT_TOKEN, OWNER_CHAT_ID

log = logging.getLogger(__name__)

_MAX_RETRIES = 3


def send(text: str, chat_id: int | None = None, dry_run: bool = False) -> None:
    if dry_run:
        print(text)
        return
    cid = chat_id or OWNER_CHAT_ID
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chunk in _split_html(text, 4000):
        _send_chunk(url, cid, chunk)
        time.sleep(0.3)


def _split_html(text: str, limit: int) -> list[str]:
    """Split text into chunks ≤ limit chars, breaking only on newlines.
    This prevents Telegram HTML parse errors from tags being split across chunks.
    """
    chunks = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1   # +1 for the \n
        if current_len + line_len > limit and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def _send_chunk(url: str, cid: int, chunk: str) -> None:
    """Send one chunk with exponential backoff on rate limits."""
    wait = 1
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = requests.post(url, json={"chat_id": cid, "text": chunk,
                                         "parse_mode": "HTML"}, timeout=15)
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", wait)
                log.warning("Telegram 429 rate limit — waiting %ds (attempt %d/%d)",
                            retry_after, attempt, _MAX_RETRIES)
                time.sleep(retry_after)
                wait *= 2
                continue
            if not r.ok:
                log.error("Telegram send failed (attempt %d/%d): %s",
                          attempt, _MAX_RETRIES, r.text[:200])
                if attempt < _MAX_RETRIES:
                    time.sleep(wait)
                    wait *= 2
                continue
            return   # success
        except requests.RequestException as e:
            log.error("Telegram send exception (attempt %d/%d): %s",
                      attempt, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES:
                time.sleep(wait)
                wait *= 2
    log.error("Telegram send gave up after %d attempts", _MAX_RETRIES)
