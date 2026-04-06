#!/usr/bin/env bash
# =============================================================================
# cron_guard.sh -- resilient cron wrapper
#
# - Retries failed jobs up to MAX_RETRIES times with backoff
# - Sends a Telegram alert only after all retries are exhausted
# - Sends a recovery notice if a retry succeeds after earlier failure
# - Writes a heartbeat file on success for external monitoring
#
# Usage:
#   cron_guard.sh <job_name> <command...>
#
# Example in crontab:
#   15 3 * * 1-5 cd /root/fin-assistant && bash scripts/cron_guard.sh preopen python3 main.py preopen
# =============================================================================
set -uo pipefail

JOB="$1"
shift

MAX_RETRIES=3
RETRY_WAIT=60   # seconds between retries

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HEARTBEAT_DIR="${REPO_DIR}/logs/heartbeats"
mkdir -p "${HEARTBEAT_DIR}"

# -- Exclusive lock: prevents two instances of the same job running concurrently --
# Uses flock on a per-job lock file. If the lock is already held (another instance
# is mid-run or mid-retry), this instance exits immediately with a log message.
# The lock is released automatically when the script exits (fd 9 is closed by OS).
LOCK_FILE="${HEARTBEAT_DIR}/${JOB}.lock"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "[cron_guard] job=${JOB} already running (lock held) -- skipping duplicate"
    exit 0
fi

# -- Load bot credentials from .env --
if [[ -f "${REPO_DIR}/.env" ]]; then
    set -a
    source <(grep -E '^(BOT_TOKEN|OWNER_CHAT_ID)=' "${REPO_DIR}/.env")
    set +a
fi

send_telegram() {
    local msg="$1"
    if [[ -z "${BOT_TOKEN:-}" || -z "${OWNER_CHAT_ID:-}" ]]; then
        echo "[cron_guard] Cannot send alert -- credentials not set"
        return
    fi
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${OWNER_CHAT_ID}" \
        -d parse_mode="HTML" \
        -d text="$msg" > /dev/null 2>&1 || true
}

tail_log() {
    local log="${REPO_DIR}/logs/cron.log"
    [[ -f "$log" ]] && tail -15 "$log" 2>/dev/null | sed 's/[<>&]/./g' || echo "(no log)"
}

# -- Retry loop --
attempt=0
last_exit=0

while [[ $attempt -lt $MAX_RETRIES ]]; do
    attempt=$(( attempt + 1 ))
    echo "[cron_guard] job=${JOB} attempt=${attempt}/${MAX_RETRIES} at $(TZ=Asia/Kolkata date '+%H:%M:%S IST')"

    "$@"
    last_exit=$?

    if [[ $last_exit -eq 0 ]]; then
        # Success
        date -u '+%Y-%m-%dT%H:%M:%SZ' > "${HEARTBEAT_DIR}/${JOB}.last_ok"

        if [[ $attempt -gt 1 ]]; then
            echo "[cron_guard] job=${JOB} recovered on attempt ${attempt}"
            send_telegram "[OK] <b>Cron recovered: ${JOB}</b>
Succeeded on attempt ${attempt}/${MAX_RETRIES} at $(TZ=Asia/Kolkata date '+%H:%M IST')"
        else
            echo "[cron_guard] job=${JOB} OK"
        fi

        exit 0
    fi

    echo "[cron_guard] job=${JOB} attempt=${attempt} failed (exit ${last_exit})"

    if [[ $attempt -lt $MAX_RETRIES ]]; then
        echo "[cron_guard] Waiting ${RETRY_WAIT}s before retry..."
        sleep "${RETRY_WAIT}"
    fi
done

# -- All retries exhausted: schedule one final at-based attempt --
# NOTE: the at-fallback runs the underlying command directly (NOT via cron_guard.sh)
# to avoid infinite _fallback_fallback_... chaining when fallbacks also fail.
AT_DELAY=10   # minutes
echo "[cron_guard] job=${JOB} FAILED after ${MAX_RETRIES} attempts -- scheduling at-fallback in ${AT_DELAY}min"

AT_CMD="cd ${REPO_DIR} && echo \"[at-fallback] job=${JOB} starting at \$(TZ=Asia/Kolkata date '+%H:%M:%S IST')\" >> logs/cron.log 2>&1 && $* >> logs/cron.log 2>&1 && date -u '+%Y-%m-%dT%H:%M:%SZ' > ${HEARTBEAT_DIR}/${JOB}.last_ok || echo \"[at-fallback] job=${JOB} FAILED\" >> logs/cron.log 2>&1"
echo "${AT_CMD}" | at "now + ${AT_DELAY} minutes" 2>/dev/null \
    && AT_MSG="Fallback attempt scheduled in ${AT_DELAY} min via at." \
    || AT_MSG="Could not schedule at-fallback (atd may be down)."

send_telegram "[FAIL] <b>Cron job failed: ${JOB}</b>
All ${MAX_RETRIES} attempts failed. Exit code: ${last_exit}
Time: $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M IST')
${AT_MSG}

<pre>$(tail_log)</pre>

To trigger manually: send /run_${JOB} to this bot"

exit "${last_exit}"
