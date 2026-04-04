"""
Central config. All secrets come from .env (never hardcoded here).
"""
import os
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
from datetime import timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
