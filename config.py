"""
Central config. All secrets come from .env (never hardcoded here).
"""
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# Telegram MTProto (Pyrogram user client)
TG_API_ID   = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION  = os.getenv("TG_SESSION", str(ROOT / "store" / "tg_session"))

# Telegram Bot (for sending messages)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

# Paths
DB_PATH = ROOT / "store" / "messages.db"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Timezone
from datetime import timezone, timedelta, date as _date
IST = timezone(timedelta(hours=5, minutes=30))

# Chat JIDs to exclude from signal scanning (own account, service chats, etc.)
# Format used by the bridge: 'tg:<numeric_chat_id>'
# The default entry is the user's own Saved Messages / self-chat.
IGNORED_CHAT_IDS: set[str] = set(
    f"tg:{jid}" for jid in
    os.getenv("IGNORED_CHAT_IDS", "476254580").split(",")
    if jid.strip()
)

# NSE market holidays — equity/cash segment (weekday closures only)
# Source: NSE annual circular, cross-verified via Groww + IntegratedIndia
# Update this list each year when NSE publishes the next year's circular.
NSE_HOLIDAYS: dict[int, set[_date]] = {
    2026: {
        _date(2026,  1, 26),   # Republic Day
        _date(2026,  3,  3),   # Holi
        _date(2026,  3, 26),   # Shri Ram Navami
        _date(2026,  3, 31),   # Shri Mahavir Jayanti
        _date(2026,  4,  3),   # Good Friday
        _date(2026,  4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        _date(2026,  5,  1),   # Maharashtra Day
        _date(2026,  5, 28),   # Bakri Id (Eid ul-Adha)
        _date(2026,  6, 26),   # Muharram
        _date(2026,  9, 14),   # Ganesh Chaturthi
        _date(2026, 10,  2),   # Mahatma Gandhi Jayanti
        _date(2026, 10, 20),   # Dussehra
        _date(2026, 11, 10),   # Diwali — Balipratipada
        _date(2026, 11, 24),   # Prakash Gurpurb (Guru Nanak Jayanti)
        _date(2026, 12, 25),   # Christmas
    },
}


def db(timeout: float = 15.0) -> sqlite3.Connection:
    """
    Open and return a WAL-mode SQLite connection to the main DB.

    WAL allows concurrent readers while a write is in progress, preventing
    the bridge from blocking report queries and vice versa. All callers
    should use this instead of sqlite3.connect(DB_PATH) directly.
    """
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL; faster than FULL
    return conn


def is_market_open(dt=None) -> bool:
    """Return True if the NSE equity market is open on the given date.

    Accepts a date, datetime (aware or naive), or defaults to today IST.
    Returns False on weekends and on any date listed in NSE_HOLIDAYS.
    """
    from datetime import datetime
    if dt is None:
        dt = datetime.now(IST)
    # Accept both date and datetime objects
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    holidays = NSE_HOLIDAYS.get(d.year, set())
    return d not in holidays
