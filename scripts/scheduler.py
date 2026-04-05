#!/usr/bin/env python3
"""
fin-scheduler -- persistent failsafe scheduler (Layer 4 recovery).

Runs as a systemd service with Restart=always. Completely independent of cron,
atd, and the watchdog. Uses heartbeat files written by cron_guard.sh to avoid
double-running jobs that cron already handled.

If cron + atd + watchdog all fail, this catches it.

Schedule (IST, Mon-Fri):
  08:50  preopen   (5 min after cron's 08:45 — only fires if cron missed it)
  09:50  hourly    (5 min after cron's 09:45 — same logic)
  10:50  hourly
  11:50  hourly
  12:50  hourly
  13:50  hourly
  14:50  hourly
  15:20  hourly    (last scan)
  15:50  eod       (5 min after cron's 15:45)
  08:05  weekly    (Mon only, before preopen)

Each job only runs if its heartbeat file does not show today's date,
meaning cron_guard did NOT already run it successfully.
"""
import sys
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import BOT_TOKEN, OWNER_CHAT_ID, IST, is_market_open

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s %(message)s",
)
log = logging.getLogger("scheduler")

PYTHON       = sys.executable
HB_DIR       = ROOT / "logs" / "heartbeats"
POLL_SECS    = 30   # how often to check the schedule


# -- Helpers ------------------------------------------------------------------

def send(text: str) -> None:
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error("send failed: %s", e)


def ran_today(job: str) -> bool:
    """True if cron_guard already wrote a success heartbeat for today."""
    HB_DIR.mkdir(parents=True, exist_ok=True)
    hb = HB_DIR / f"{job}.last_ok"
    if not hb.exists():
        return False
    try:
        last = hb.read_text().strip()[:10]   # YYYY-MM-DD
        return last == datetime.now(IST).strftime("%Y-%m-%d")
    except Exception:
        return False


def run_job(job: str, cmd: list) -> bool:
    """Run a job, write heartbeat on success. Returns True if succeeded."""
    log.info("[FAILSAFE] Running %s (cron missed it)", job)
    send(f"[FAILSAFE] <b>{job}</b> missed by cron -- running now")
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            hb = HB_DIR / f"{job}.last_ok"
            hb.write_text(datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%SZ"))
            log.info("[FAILSAFE] %s completed OK", job)
            send(f"[OK] <b>{job}</b> completed by failsafe scheduler")
            return True
        else:
            tail = (result.stderr or result.stdout or "")[-600:]
            log.error("[FAILSAFE] %s failed: %s", job, tail[:200])
            send(f"[FAIL] <b>{job}</b> failed in failsafe scheduler\n<pre>{tail}</pre>")
            return False
    except subprocess.TimeoutExpired:
        log.error("[FAILSAFE] %s timed out", job)
        send(f"[FAIL] <b>{job}</b> timed out in failsafe scheduler")
        return False
    except Exception as e:
        log.error("[FAILSAFE] %s error: %s", job, e)
        send(f"[FAIL] <b>{job}</b> error in failsafe: {e}")
        return False


# -- Schedule definition ------------------------------------------------------
# Each entry: (hour_IST, minute_IST, job_name, cmd, weekdays)
# weekdays: set of weekday numbers 0=Mon..4=Fri, None=all weekdays

SCHEDULE = [
    # Weekly scorecard — Monday only, 8:05 AM IST
    (8,  5, "weekly",  [PYTHON, "main.py", "weekly"],  {0}),

    # Pre-open — 8:50 AM IST (cron runs at 8:45, we fire at 8:50 if missed)
    (8,  50, "preopen", [PYTHON, "main.py", "preopen"], None),

    # Hourly scans — :50 past each hour (cron fires at :45, we fire at :50 if missed)
    (9,  50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (10, 50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (11, 50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (12, 50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (13, 50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (14, 50, "hourly",  [PYTHON, "main.py", "hourly"],  None),
    (15, 20, "hourly",  [PYTHON, "main.py", "hourly"],  None),

    # EOD grader — 3:50 PM IST (cron fires at 3:45)
    (15, 50, "eod",     [PYTHON, "main.py", "eod"],     None),
]

# Track which schedule slots already fired today to avoid running twice
_fired: set = set()


def check_schedule(now: datetime) -> None:
    global _fired

    # Reset fired set at midnight IST
    today_key = now.strftime("%Y-%m-%d")
    _fired = {k for k in _fired if k.startswith(today_key)}

    if not is_market_open(now):
        return   # weekend or NSE holiday

    h, m = now.hour, now.minute

    for (sched_h, sched_m, job, cmd, weekdays) in SCHEDULE:
        if weekdays and now.weekday() not in weekdays:
            continue

        slot_key = f"{today_key}_{sched_h:02d}{sched_m:02d}_{job}"

        # Only fire within a 2-minute window after the scheduled time
        scheduled_mins = sched_h * 60 + sched_m
        current_mins   = h * 60 + m
        in_window = scheduled_mins <= current_mins < scheduled_mins + 2

        if in_window and slot_key not in _fired:
            _fired.add(slot_key)
            if not ran_today(job):
                run_job(job, cmd)
            else:
                log.debug("Slot %s: %s already ran today -- skipping", slot_key, job)


# -- Main loop ----------------------------------------------------------------

def main():
    log.info("Failsafe scheduler started")
    send(f"[OK] Failsafe scheduler online -- {datetime.now(IST).strftime('%H:%M IST')}")

    while True:
        try:
            now = datetime.now(IST)
            check_schedule(now)
        except Exception as e:
            log.exception("Schedule check error: %s", e)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
