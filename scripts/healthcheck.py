#!/usr/bin/env python3
"""
Health monitor and watchdog for the Financial Assistant stack.

Checks every component in order. Attempts auto-recovery where possible.
Sends a Telegram alert if anything is degraded or failed.

Run modes:
  python3 scripts/healthcheck.py          # full check, alert only on failure
  python3 scripts/healthcheck.py --report # full check + send OK summary to bot
  python3 scripts/healthcheck.py --quiet  # full check, no Telegram at all (log only)

Exit codes:
  0 = all checks passed
  1 = one or more checks failed (after recovery attempts)
"""
import sys
import os
import shutil
import sqlite3
import logging
import subprocess
import time
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from any directory
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, DB_PATH, BOT_TOKEN, OWNER_CHAT_ID, IST, is_market_open

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [healthcheck] %(levelname)s %(message)s",
)
log = logging.getLogger("healthcheck")

# -- Thresholds ---------------------------------------------------------------
DISK_WARN_MB        = 500      # warn if free space < this
DB_SIZE_WARN_MB     = 500      # warn if DB > this
BRIDGE_STALE_HOURS  = 2        # alert if no new messages for this long (market hours)
NSE_TIMEOUT         = 10       # seconds
YF_TIMEOUT          = 10
FF_TIMEOUT          = 8

# Market hours IST: Mon-Fri 09:15-15:30
MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 30)


def is_market_hours() -> bool:
    now = datetime.now(IST)
    if not is_market_open(now):
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


# -- Telegram alert (direct HTTP, no library dependency) ----------------------

def send_alert(text: str) -> bool:
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        log.warning("Bot credentials not set -- cannot send alert")
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.error("Alert send failed: %s", e)
        return False


def bot_is_reachable() -> bool:
    if not BOT_TOKEN:
        return False
    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
            timeout=8,
        )
        return r.status_code == 200 and r.json().get("ok")
    except Exception:
        return False


# -- Individual checks --------------------------------------------------------

class Check:
    def __init__(self, name: str):
        self.name    = name
        self.status  = "OK"     # OK | WARN | FAIL
        self.message = ""
        self.recovered = False

    def ok(self, msg=""):
        self.status  = "OK"
        self.message = msg
        log.info("  [OK] %s  %s", self.name, msg)

    def warn(self, msg):
        self.status  = "WARN"
        self.message = msg
        log.warning("  [WARN] %s  %s", self.name, msg)

    def fail(self, msg):
        self.status  = "FAIL"
        self.message = msg
        log.error("  [FAIL] %s  %s", self.name, msg)


def check_disk() -> Check:
    c = Check("Disk space")
    try:
        usage = shutil.disk_usage(ROOT)
        free_mb = usage.free // (1024 * 1024)
        total_mb = usage.total // (1024 * 1024)
        used_pct = round((usage.used / usage.total) * 100)
        if free_mb < DISK_WARN_MB:
            c.warn(f"{free_mb} MB free of {total_mb} MB ({used_pct}% used) -- LOW")
        else:
            c.ok(f"{free_mb} MB free of {total_mb} MB ({used_pct}% used)")
    except Exception as e:
        c.fail(str(e))
    return c


def check_db() -> Check:
    c = Check("SQLite DB")
    try:
        db_path = Path(DB_PATH)
        if not db_path.exists():
            c.fail(f"DB file not found: {DB_PATH}")
            return c
        size_mb = db_path.stat().st_size // (1024 * 1024)
        with db(timeout=5) as conn:
            conn.execute("SELECT 1")
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
        required = {"messages", "chats", "signal_log", "monitored_channels"}
        missing  = required - table_names
        if missing:
            c.fail(f"Missing tables: {missing}")
        elif size_mb > DB_SIZE_WARN_MB:
            c.warn(f"DB accessible ({size_mb} MB -- consider archiving old messages)")
        else:
            c.ok(f"accessible, {size_mb} MB, {len(table_names)} tables")
    except sqlite3.OperationalError as e:
        c.fail(f"DB locked or corrupt: {e}")
    except Exception as e:
        c.fail(str(e))
    return c


def check_bridge_service() -> Check:
    c = Check("fin-bridge service")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "fin-bridge"],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        if status == "active":
            c.ok("running")
        else:
            log.warning("  fin-bridge is %s -- attempting restart", status)
            subprocess.run(["systemctl", "restart", "fin-bridge"], timeout=15)
            time.sleep(5)
            result2 = subprocess.run(
                ["systemctl", "is-active", "fin-bridge"],
                capture_output=True, text=True, timeout=5
            )
            if result2.stdout.strip() == "active":
                c.warn(f"was {status} -- restarted successfully")
                c.recovered = True
            else:
                c.fail(f"is {status} -- restart failed")
    except FileNotFoundError:
        c.warn("systemctl not available (not running as systemd service)")
    except Exception as e:
        c.fail(str(e))
    return c


def check_bridge_freshness() -> Check:
    """Check if the bridge has received any messages recently (market hours only)."""
    c = Check("Bridge message freshness")
    if not is_market_hours():
        c.ok("market closed -- skipped")
        return c
    try:
        # DB timestamps are stored in UTC — compute threshold in UTC for correct string comparison
        threshold = (datetime.now(timezone.utc) - timedelta(hours=BRIDGE_STALE_HOURS)).isoformat()
        with db(timeout=15) as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM messages"
            ).fetchone()
        last_ts = row[0] if row and row[0] else None
        if not last_ts:
            c.warn("No messages in DB at all")
        elif last_ts < threshold:
            c.warn(f"Last message at {last_ts[:16]} -- bridge may be stalled")
        else:
            c.ok(f"last message at {last_ts[:16]}")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            c.warn("DB locked by bridge (bridge is active but busy)")
        else:
            c.fail(str(e))
    except Exception as e:
        c.fail(str(e))
    return c


def check_nse() -> Check:
    c = Check("NSE API")
    try:
        import requests
        r = requests.get(
            "https://www.nseindia.com/api/marketStatus",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://www.nseindia.com/",
            },
            timeout=NSE_TIMEOUT,
        )
        if r.status_code == 200 and r.text.strip().startswith("{"):
            status = r.json().get("marketState", [{}])[0].get("marketStatus", "?")
            c.ok(f"reachable (market: {status})")
        elif r.status_code in (403, 429):
            c.warn(f"HTTP {r.status_code} -- rate-limited or blocked (yfinance fallback active)")
        else:
            c.warn(f"HTTP {r.status_code} (yfinance fallback active)")
    except Exception as e:
        c.warn(f"unreachable -- {e} (yfinance fallback active)")
    return c


def check_yfinance() -> Check:
    c = Check("yfinance fallback")
    try:
        import yfinance as yf
        fi = yf.Ticker("^NSEI").fast_info
        if fi.last_price:
            c.ok(f"NIFTY 50 last = {fi.last_price:,.0f}")
        else:
            c.warn("returned no data for ^NSEI")
    except Exception as e:
        c.fail(f"unavailable -- {e}")
    return c


def check_forex_factory() -> Check:
    c = Check("Macro calendar (ForexFactory)")
    try:
        import requests
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=FF_TIMEOUT,
        )
        if r.status_code == 200:
            events = r.json()
            high = sum(1 for e in events if e.get("impact") == "High")
            c.ok(f"reachable ({len(events)} events this week, {high} high-impact)")
        else:
            c.warn(f"HTTP {r.status_code} -- macro calendar unavailable (non-critical)")
    except Exception as e:
        c.warn(f"unreachable -- {e} (non-critical, reports will skip this section)")
    return c


def check_telegram_bot() -> Check:
    c = Check("Telegram bot token")
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        c.fail("BOT_TOKEN not configured in .env")
        return c
    try:
        if bot_is_reachable():
            c.ok("token valid, bot reachable")
        else:
            c.fail("getMe returned error -- token may be revoked")
    except Exception as e:
        c.fail(str(e))
    return c


def check_logs_dir() -> Check:
    c = Check("Logs directory")
    log_dir = ROOT / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        test = log_dir / ".write_test"
        test.write_text("ok")
        test.unlink()
        c.ok(str(log_dir))
    except Exception as e:
        c.fail(f"not writable: {e}")
    return c



# -- Report formatter ---------------------------------------------------------

STATUS_EM = {"OK": "[OK]", "WARN": "[WARN]", "FAIL": "[FAIL]"}

def format_report(checks: list[Check], elapsed: float) -> str:
    now     = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    n_fail  = sum(1 for c in checks if c.status == "FAIL")
    n_warn  = sum(1 for c in checks if c.status == "WARN")
    n_ok    = sum(1 for c in checks if c.status == "OK")
    overall = "[OK] ALL OK" if n_fail == 0 and n_warn == 0 else \
              ("[FAIL] FAILURES DETECTED" if n_fail > 0 else "[WARN] WARNINGS")

    lines = [
        f"[CHECK] <b>HEALTH CHECK -- {now}</b>",
        f"{overall}  ({n_ok} OK  {n_warn} WARN  {n_fail} FAIL)  [{elapsed:.1f}s]",
        "",
    ]
    for c in checks:
        em  = STATUS_EM[c.status]
        rec = "  (recovered)" if c.recovered else ""
        lines.append(f"{em} <b>{c.name}</b>{rec}")
        if c.message and c.status != "OK":
            lines.append(f"  └ {c.message}")
    return "\n".join(lines)


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true",
                        help="Send full status to Telegram even if all OK")
    parser.add_argument("--quiet",  action="store_true",
                        help="No Telegram output -- log only")
    args = parser.parse_args()

    log.info("=== Health check starting ===")
    t0 = time.time()

    checks = [
        check_logs_dir(),
        check_disk(),
        check_db(),
        check_bridge_service(),
        check_bridge_freshness(),
        check_nse(),
        check_yfinance(),
        check_forex_factory(),
        check_telegram_bot(),
    ]

    elapsed = time.time() - t0
    n_fail  = sum(1 for c in checks if c.status == "FAIL")
    n_warn  = sum(1 for c in checks if c.status == "WARN")
    recovered = [c for c in checks if c.recovered]

    report = format_report(checks, elapsed)

    if not args.quiet:
        should_send = args.report or n_fail > 0 or n_warn > 0 or recovered
        if should_send:
            sent = send_alert(report)
            if sent:
                log.info("Alert sent to Telegram")
            else:
                log.warning("Could not send Telegram alert -- check BOT_TOKEN / OWNER_CHAT_ID")

    log.info("=== Health check complete: %d fail, %d warn, %d OK ===",
             n_fail, n_warn, sum(1 for c in checks if c.status == "OK"))

    # Print summary to stdout (captured in cron log)
    for c in checks:
        print(f"[{c.status:4}] {c.name}: {c.message}")

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
