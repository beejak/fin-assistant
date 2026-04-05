"""
Historical message backfill.
Fetches messages from all active channels in monitored_channels DB table.

Usage:
  python main.py fetch [days=3] [limit=500]
  python bridge/fetch.py 7 500
"""
import asyncio
import sqlite3
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pyrogram import Client
from config import db, TG_API_ID, TG_API_HASH, TG_SESSION, DB_PATH, IST
from bridge.discover import list_channels, _init_table

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DAYS  = int(sys.argv[1]) if len(sys.argv) > 1 else 3
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 500

SINCE_UTC = (datetime.now(IST) - timedelta(days=DAYS)).replace(
    hour=0, minute=0, second=0, microsecond=0
).astimezone(timezone.utc)


def write_to_db(chat_id, chat_name, msg_id, sender_id, sender_name, text, ts):
    jid = f"tg:{chat_id}"
    with db() as conn:
        conn.execute("""
            INSERT INTO chats (jid, name, last_message_time, channel, is_group)
            VALUES (?,?,?,'telegram',1)
            ON CONFLICT(jid) DO UPDATE SET
                name=excluded.name,
                last_message_time=MAX(last_message_time,excluded.last_message_time)
        """, (jid, chat_name, ts.isoformat()))
        conn.execute("""
            INSERT OR REPLACE INTO messages
              (id, chat_jid, sender, sender_name, content, timestamp, is_from_me, is_bot_message)
            VALUES (?,?,?,?,?,?,0,0)
        """, (f"tg_{chat_id}_{msg_id}", jid, str(sender_id), sender_name, text, ts.isoformat()))
        conn.commit()


async def fetch_chat(app, chat_id, chat_name):
    count = 0
    try:
        async for msg in app.get_chat_history(chat_id, limit=LIMIT):
            if not msg.date:
                continue
            ts = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            if ts < SINCE_UTC:
                break
            text = msg.text or msg.caption or ""
            if not text.strip():
                continue
            sender_id   = msg.from_user.id         if msg.from_user else chat_id
            sender_name = msg.from_user.first_name if msg.from_user else chat_name
            write_to_db(chat_id, chat_name, msg.id, sender_id, sender_name, text, ts)
            count += 1
    except Exception as e:
        log.warning("[%s] error: %s", chat_name, e)
    if count:
        log.info("[%s] %d messages", chat_name, count)
    return count


async def main():
    _init_table()
    channels = list_channels(active_only=True)

    if not channels:
        log.warning("No channels in DB. Run: python main.py discover")
        return

    async with Client(TG_SESSION, api_id=TG_API_ID, api_hash=TG_API_HASH) as app:
        since_ist = SINCE_UTC.astimezone(IST)
        log.info("Fetching %d channels since %s IST (limit %d/chat)",
                 len(channels), since_ist.strftime("%Y-%m-%d"), LIMIT)
        total = 0
        for ch in channels:
            total += await fetch_chat(app, ch["id"], ch["name"])
            await asyncio.sleep(0.4)
        log.info("Done — %d messages across %d channels", total, len(channels))


asyncio.run(main())
