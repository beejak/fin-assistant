#!/usr/bin/env python3
"""
Telegram bot command listener — persistent failover interface.

Polls the Telegram Bot API for commands from OWNER_CHAT_ID.
Runs as a systemd service (fin-bot-listener) independent of cron.

Commands:
  /run_preopen   -- run pre-open briefing now
  /run_hourly    -- run hourly signal scan now
  /run_eod       -- run EOD grader now
  /run_weekly    -- run weekly scorecard now
  /health        -- run health check and report
  /status        -- show service and bridge status

Security: only responds to OWNER_CHAT_ID. All other senders are silently ignored.
"""
import sys
import os
import time
import subprocess
import logging
import requests
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import db, BOT_TOKEN, OWNER_CHAT_ID, IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bot_listener] %(levelname)s %(message)s",
)
log = logging.getLogger("bot_listener")

POLL_TIMEOUT  = 30    # long-poll timeout seconds
PYTHON        = sys.executable

COMMANDS = {
    "/run_preopen": ("preopen",  [PYTHON, "main.py", "preopen"]),
    "/run_hourly":  ("hourly",   [PYTHON, "main.py", "hourly"]),
    "/run_eod":     ("eod",      [PYTHON, "main.py", "eod"]),
    "/run_weekly":  ("weekly",   [PYTHON, "main.py", "weekly"]),
    "/health":      ("health",   [PYTHON, "scripts/healthcheck.py", "--report"]),
    "/status":      None,   # handled separately
}


# -- Telegram helpers ---------------------------------------------------------

def api(method: str, **kwargs) -> dict:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=kwargs, timeout=35,
        )
        return r.json()
    except Exception as e:
        log.error("API %s failed: %s", method, e)
        return {}


def send(text: str) -> None:
    api("sendMessage", chat_id=OWNER_CHAT_ID, text=text, parse_mode="HTML")


# -- Command handlers ---------------------------------------------------------

def run_job(name: str, cmd: list[str]) -> None:
    send(f"[RUN] Starting <b>{name}</b>...")
    log.info("Running job: %s", name)
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed = round(time.time() - start)
        if result.returncode == 0:
            send(f"[OK] <b>{name}</b> completed in {elapsed}s")
            log.info("Job %s OK (%ds)", name, elapsed)
        else:
            tail = (result.stderr or result.stdout or "")[-800:]
            send(
                f"[FAIL] <b>{name}</b> failed (exit {result.returncode}) in {elapsed}s\n"
                f"<pre>{tail}</pre>"
            )
            log.error("Job %s failed: %s", name, tail[:200])
    except subprocess.TimeoutExpired:
        send(f"[FAIL] <b>{name}</b> timed out after 300s")
        log.error("Job %s timed out", name)
    except Exception as e:
        send(f"[FAIL] <b>{name}</b> error: {e}")
        log.error("Job %s error: %s", name, e)


def handle_status() -> None:
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    lines = [f"<b>Status — {now}</b>\n"]

    # fin-bridge
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "fin-bridge"],
            capture_output=True, text=True, timeout=5
        )
        status = r.stdout.strip()
        lines.append(f"fin-bridge: {status}")
    except Exception:
        lines.append("fin-bridge: unknown")

    # fin-bot-listener itself
    lines.append("bot-listener: running (you're talking to it)")

    # DB message count today
    try:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        with db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today,)
            ).fetchone()[0]
            s = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE date=? AND result='OPEN'", (today,)
            ).fetchone()[0]
        lines.append(f"Messages today: {n}")
        lines.append(f"Open signals today: {s}")
    except Exception as e:
        lines.append(f"DB: {e}")

    send("\n".join(lines))


# -- Main poll loop -----------------------------------------------------------

def main():
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        log.error("BOT_TOKEN or OWNER_CHAT_ID not set -- exiting")
        sys.exit(1)

    log.info("Bot listener started. Polling for commands from chat_id=%s", OWNER_CHAT_ID)
    send(f"[OK] Bot listener online — {datetime.now(IST).strftime('%H:%M IST')}\n"
         f"Commands: /run_preopen /run_hourly /run_eod /run_weekly /health /status")

    offset = None

    while True:
        try:
            params = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset

            resp = api("getUpdates", **params)
            updates = resp.get("result", [])

            for update in updates:
                msg     = update.get("message", {})
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text    = (msg.get("text") or "").strip().lower().split()[0]

                # Advance offset unconditionally so we never re-process this
                # update even if execution fails. Commands are idempotent.
                offset = update["update_id"] + 1

                # Security: only owner
                if chat_id != str(OWNER_CHAT_ID):
                    continue

                log.info("Command received: %s", text)

                try:
                    if text == "/status":
                        handle_status()
                    elif text in COMMANDS:
                        spec = COMMANDS[text]
                        if spec:
                            name, cmd = spec
                            run_job(name, cmd)
                        else:
                            handle_status()
                    elif text.startswith("/"):
                        send(
                            "Unknown command. Available:\n"
                            "/run_preopen  /run_hourly  /run_eod  /run_weekly\n"
                            "/health  /status"
                        )
                except Exception as e:
                    log.error("Command handler failed for '%s': %s", text, e)
                    send(f"[ERROR] Command <code>{text}</code> failed:\n{e}")

        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Poll loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
