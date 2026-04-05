#!/usr/bin/env bash
# =============================================================================
# watchdog.sh -- cron-based failover for critical jobs
#
# Runs every 30 minutes during market day.
# Checks heartbeat files written by cron_guard.sh after each successful run.
# If a critical job missed its window or its heartbeat shows a past date,
# re-runs it once and alerts via Telegram on what it did.
#
# This is completely independent of cron_guard -- it acts as a second layer
# that catches anything cron_guard missed or could not recover.
#
# Crontab entry (added automatically by install.sh):
#   */30 2-11 * * 1-5 cd /root/fin-assistant && bash scripts/watchdog.sh >> logs/watchdog.log 2>&1
# =============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HB_DIR="${REPO_DIR}/logs/heartbeats"
LOG="${REPO_DIR}/logs/watchdog.log"
PYTHON=/usr/bin/python3

mkdir -p "${HB_DIR}"

# Load credentials
if [[ -f "${REPO_DIR}/.env" ]]; then
    set -a
    source <(grep -E '^(BOT_TOKEN|OWNER_CHAT_ID)=' "${REPO_DIR}/.env")
    set +a
fi

log() { echo "[$(date '+%H:%M:%S')] $*"; }

send_telegram() {
    local msg="$1"
    [[ -z "${BOT_TOKEN:-}" || -z "${OWNER_CHAT_ID:-}" ]] && return
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${OWNER_CHAT_ID}" \
        -d parse_mode="HTML" \
        -d text="$msg" > /dev/null 2>&1 || true
}

# Returns 0 if job ran successfully today, 1 if not
ran_today() {
    local job="$1"
    local hb="${HB_DIR}/${job}.last_ok"
    [[ ! -f "$hb" ]] && return 1
    local last_ok
    last_ok=$(cat "$hb" 2>/dev/null | cut -c1-10)   # first 10 chars = YYYY-MM-DD
    # Validate format before comparing to avoid corrupt files passing the check
    if [[ ! "$last_ok" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        return 1
    fi
    local today
    today=$(TZ=Asia/Kolkata date '+%Y-%m-%d')   # IST — consistent with scheduler.py
    [[ "$last_ok" == "$today" ]]
}

run_job() {
    local job="$1"
    shift
    log "Re-running: ${job}"
    bash "${REPO_DIR}/scripts/cron_guard.sh" "${job}_watchdog" "$@"
    return $?
}

# Current IST hour/minute (UTC+5:30)
IST_HOUR=$(TZ=Asia/Kolkata date '+%H')
IST_MIN=$(TZ=Asia/Kolkata date '+%M')
IST_TIME=$(( IST_HOUR * 60 + IST_MIN ))  # minutes since midnight IST
WEEKDAY=$(date '+%u')   # 1=Mon ... 5=Fri ... 7=Sun

log "=== Watchdog starting (IST ${IST_HOUR}:${IST_MIN} weekday=${WEEKDAY}) ==="

# NSE public holidays 2026 — equity/cash segment (weekday closures only)
# Update annually when NSE publishes the next year's circular.
NSE_HOLIDAYS_2026=(
    "2026-01-26"   # Republic Day
    "2026-03-03"   # Holi
    "2026-03-26"   # Shri Ram Navami
    "2026-03-31"   # Shri Mahavir Jayanti
    "2026-04-03"   # Good Friday
    "2026-04-14"   # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01"   # Maharashtra Day
    "2026-05-28"   # Bakri Id (Eid ul-Adha)
    "2026-06-26"   # Muharram
    "2026-09-14"   # Ganesh Chaturthi
    "2026-10-02"   # Mahatma Gandhi Jayanti
    "2026-10-20"   # Dussehra
    "2026-11-10"   # Diwali — Balipratipada
    "2026-11-24"   # Prakash Gurpurb (Guru Nanak Jayanti)
    "2026-12-25"   # Christmas
)

is_market_open() {
    local today
    today=$(TZ=Asia/Kolkata date '+%Y-%m-%d')
    for h in "${NSE_HOLIDAYS_2026[@]}"; do
        [[ "$h" == "$today" ]] && return 1   # holiday → market closed
    done
    return 0   # open
}

# Only act on weekdays and non-holidays
if [[ $WEEKDAY -ge 6 ]]; then
    log "Weekend -- nothing to watch"
    exit 0
fi
if ! is_market_open; then
    log "NSE holiday -- nothing to watch"
    exit 0
fi

ACTIONS=()
FAILURES=()

# ── preopen: should run at 8:45 AM IST ───────────────────────────────────────
# Check after 9:00 AM (540 min)
if [[ $IST_TIME -ge 540 ]] && ! ran_today "preopen"; then
    log "preopen missed -- re-running"
    if run_job "preopen" "${PYTHON}" main.py preopen; then
        ACTIONS+=("preopen re-run OK")
    else
        FAILURES+=("preopen re-run FAILED")
    fi
fi

# ── hourly: should run at :45 past each hour 9:45-15:15 IST ─────────────────
# Check after 10:45 AM (645 min) -- if no hourly heartbeat at all today
if [[ $IST_TIME -ge 645 ]] && ! ran_today "hourly"; then
    log "hourly missed -- re-running"
    if run_job "hourly" "${PYTHON}" main.py hourly; then
        ACTIONS+=("hourly re-run OK")
    else
        FAILURES+=("hourly re-run FAILED")
    fi
fi

# ── eod: should run at 3:45 PM IST (945 min) ─────────────────────────────────
# Check after 4:15 PM (975 min)
if [[ $IST_TIME -ge 975 ]] && ! ran_today "eod"; then
    log "eod missed -- re-running"
    if run_job "eod" "${PYTHON}" main.py eod; then
        ACTIONS+=("eod re-run OK")
    else
        FAILURES+=("eod re-run FAILED")
    fi
fi

# ── Report ────────────────────────────────────────────────────────────────────
if [[ ${#ACTIONS[@]} -eq 0 && ${#FAILURES[@]} -eq 0 ]]; then
    log "All jobs on track -- nothing to do"
    exit 0
fi

MSG="[WATCHDOG] Recovered missed cron jobs"$'\n'
for a in "${ACTIONS[@]}"; do
    MSG+="[OK] ${a}"$'\n'
    log "${a}"
done
for f in "${FAILURES[@]}"; do
    MSG+="[FAIL] ${f}"$'\n'
    log "${f}"
done

send_telegram "$MSG"
log "=== Watchdog done ==="

[[ ${#FAILURES[@]} -gt 0 ]] && exit 1 || exit 0
