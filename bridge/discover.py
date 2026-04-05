"""
Channel auto-discovery.
Scans the authenticated user's Telegram dialogs and populates
the monitored_channels table with every group/channel they belong to.

Usage:
  python main.py discover          # full refresh
  python main.py discover --dry    # print only, don't write DB
"""
import asyncio
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from pyrogram import Client
from pyrogram.enums import ChatType
from config import db, TG_API_ID, TG_API_HASH, TG_SESSION, DB_PATH, IST

log = logging.getLogger(__name__)

# Chat types we want to monitor
WATCH_TYPES = {ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP}


def _init_table():
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS monitored_channels (
                id             INTEGER PRIMARY KEY,
                name           TEXT NOT NULL,
                type           TEXT,
                members_count  INTEGER DEFAULT 0,
                discovered_at  TEXT NOT NULL,
                last_seen      TEXT,
                active         INTEGER DEFAULT 1
            )
        """)


async def _discover(dry: bool = False) -> list[dict]:
    channels = []
    async with Client(TG_SESSION, api_id=TG_API_ID, api_hash=TG_API_HASH) as app:
        log.info("Scanning dialogs...")
        async for dialog in app.get_dialogs():
            chat = dialog.chat
            if chat.type not in WATCH_TYPES:
                continue
            channels.append({
                "id":      chat.id,
                "name":    chat.title or str(chat.id),
                "type":    chat.type.name,
                "members": getattr(chat, "members_count", 0) or 0,
            })

    log.info("Found %d groups/channels in your account", len(channels))

    if dry:
        for ch in sorted(channels, key=lambda x: x["name"]):
            print(f"  {ch['type']:<12}  {ch['id']}  {ch['name']}")
        return channels

    now = datetime.now(IST).isoformat()
    _init_table()
    with db() as c:
        for ch in channels:
            c.execute("""
                INSERT INTO monitored_channels (id, name, type, members_count, discovered_at, active)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    name          = excluded.name,
                    type          = excluded.type,
                    members_count = excluded.members_count,
                    last_seen     = ?
            """, (ch["id"], ch["name"], ch["type"], ch["members"], now, now))
        c.commit()
    log.info("Stored %d channels in monitored_channels table", len(channels))
    return channels


def run(dry: bool = False) -> list[dict]:
    """Sync entry point."""
    return asyncio.run(_discover(dry=dry))


def get_active_ids() -> set[int]:
    """Return set of active channel IDs from DB (used by bridge at startup)."""
    _init_table()
    with db() as c:
        rows = c.execute(
            "SELECT id FROM monitored_channels WHERE active=1"
        ).fetchall()
    return {r[0] for r in rows}


def set_active(channel_id: int, active: bool) -> None:
    """Enable or disable monitoring for a specific channel."""
    with db() as c:
        c.execute(
            "UPDATE monitored_channels SET active=? WHERE id=?",
            (1 if active else 0, channel_id)
        )
    log.info("Channel %d → active=%s", channel_id, active)


def list_channels(active_only: bool = False) -> list[dict]:
    """Return all discovered channels from DB."""
    _init_table()
    with db() as c:
        if active_only:
            rows = c.execute("""
                SELECT id, name, type, members_count, active, discovered_at
                FROM monitored_channels WHERE active=1 ORDER BY name
            """).fetchall()
        else:
            rows = c.execute("""
                SELECT id, name, type, members_count, active, discovered_at
                FROM monitored_channels ORDER BY name
            """).fetchall()
    return [{"id":r[0],"name":r[1],"type":r[2],"members":r[3],
             "active":bool(r[4]),"discovered_at":r[5]} for r in rows]
