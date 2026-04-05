#!/usr/bin/env bash
# =============================================================================
# Financial Assistant -- backup script
#
# Usage:
#   ./scripts/backup.sh                  # backs up to ~/fa-backups/
#   ./scripts/backup.sh /path/to/dir     # custom local output dir
#
# What is backed up:
#   - store/messages.db    (all messages, signals, learning data)
#   - .env                 (credentials -- keep this safe)
#   - ~/tg_session.session (Pyrogram session file)
#
# Source code is NOT backed up here -- it lives on GitHub.
#
# Google Drive upload:
#   Requires rclone configured with a remote named "gdrive".
#   Run: rclone config  (one-time setup -- see README)
#   If rclone/gdrive not configured, local backup only.
#
# Retention:
#   Local:  keeps last 7 backups (older deleted automatically)
#   Remote: keeps last 30 backups on Google Drive
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$HOME/fa-backups}"
STAMP="$(date +%Y%m%d_%H%M)"
ARCHIVE_NAME="fa-backup-${STAMP}.tar.gz"
ARCHIVE="${OUT_DIR}/${ARCHIVE_NAME}"

GDRIVE_REMOTE="gdrive"          # rclone remote name
GDRIVE_FOLDER="fin-assistant-backups"
LOCAL_KEEP=7
REMOTE_KEEP=30

log() { echo "[$(date '+%H:%M:%S')] $*"; }

mkdir -p "${OUT_DIR}"

# -- Pause bridge for a clean DB snapshot -------------------------------------
BRIDGE_WAS_RUNNING=false
if systemctl is-active --quiet fin-bridge 2>/dev/null; then
    log "Pausing fin-bridge for clean snapshot..."
    systemctl stop fin-bridge
    BRIDGE_WAS_RUNNING=true
fi

# -- Create archive -----------------------------------------------------------
log "Creating archive: ${ARCHIVE}"

FILES_TO_BACKUP=()

# Database
DB="${REPO_DIR}/store/messages.db"
[[ -f "${DB}" ]] && FILES_TO_BACKUP+=("${DB}")

# .env (credentials)
ENV="${REPO_DIR}/.env"
[[ -f "${ENV}" ]] && FILES_TO_BACKUP+=("${ENV}")

# Pyrogram session file (try common locations)
# nullglob prevents unmatched globs from being treated as literal strings
shopt -s nullglob
for F in "${HOME}"/*.session "${REPO_DIR}"/*.session "${REPO_DIR}"/store/*.session; do
    [[ -f "${F}" ]] && FILES_TO_BACKUP+=("${F}")
done
shopt -u nullglob

if [[ ${#FILES_TO_BACKUP[@]} -eq 0 ]]; then
    log "Nothing to back up (no DB or session files found)"
    exit 0
fi

tar -czf "${ARCHIVE}" "${FILES_TO_BACKUP[@]}" 2>/dev/null
SIZE=$(du -sh "${ARCHIVE}" | cut -f1)
log "Archive created: ${ARCHIVE_NAME} (${SIZE})"

# -- Resume bridge ------------------------------------------------------------
if ${BRIDGE_WAS_RUNNING}; then
    systemctl start fin-bridge
    log "fin-bridge resumed"
fi

# -- Local retention: keep last N backups -------------------------------------
BACKUP_COUNT=$(find "${OUT_DIR}" -name "fa-backup-*.tar.gz" | wc -l)
if [[ ${BACKUP_COUNT} -gt ${LOCAL_KEEP} ]]; then
    TO_DELETE=$(( BACKUP_COUNT - LOCAL_KEEP ))
    log "Pruning ${TO_DELETE} old local backup(s) (keeping ${LOCAL_KEEP})"
    find "${OUT_DIR}" -name "fa-backup-*.tar.gz" \
        | sort | head -"${TO_DELETE}" | xargs rm -f
fi

# -- Google Drive upload ------------------------------------------------------
if ! command -v rclone &>/dev/null; then
    log "rclone not installed -- local backup only"
    log "To enable Google Drive: run scripts/setup-gdrive.sh"
elif ! rclone listremotes 2>/dev/null | grep -q "^${GDRIVE_REMOTE}:"; then
    log "rclone remote '${GDRIVE_REMOTE}' not configured -- local backup only"
    log "To configure: rclone config  (create a remote named '${GDRIVE_REMOTE}')"
else
    log "Uploading to Google Drive: ${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/"
    rclone copy "${ARCHIVE}" "${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/" \
        --stats-one-line --quiet 2>/dev/null \
        && log "Upload complete" \
        || log "Upload failed -- local backup preserved at ${ARCHIVE}"

    # Remote retention
    REMOTE_FILES=$(rclone lsf "${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/" \
        --files-only 2>/dev/null | sort || true)
    REMOTE_COUNT=$(echo "${REMOTE_FILES}" | grep -c "fa-backup-" 2>/dev/null || echo 0)
    if [[ ${REMOTE_COUNT} -gt ${REMOTE_KEEP} ]]; then
        TO_DELETE=$(( REMOTE_COUNT - REMOTE_KEEP ))
        log "Pruning ${TO_DELETE} old remote backup(s) (keeping ${REMOTE_KEEP})"
        echo "${REMOTE_FILES}" | grep "fa-backup-" | head -"${TO_DELETE}" | while read -r OLD; do
            rclone delete "${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/${OLD}" --quiet 2>/dev/null || true
        done
    fi
fi

# -- Summary ------------------------------------------------------------------
echo
log "---"
log "Backup complete"
log "  Archive : ${ARCHIVE} (${SIZE})"
log "  Local   : $(find "${OUT_DIR}" -name "fa-backup-*.tar.gz" | wc -l) backup(s) in ${OUT_DIR}"
if command -v rclone &>/dev/null && rclone listremotes 2>/dev/null | grep -q "^${GDRIVE_REMOTE}:"; then
    REMOTE_COUNT=$(rclone lsf "${GDRIVE_REMOTE}:${GDRIVE_FOLDER}/" --files-only 2>/dev/null \
        | grep -c "fa-backup-" 2>/dev/null || echo 0)
    log "  Remote  : ${REMOTE_COUNT} backup(s) on Google Drive"
fi
log "---"
echo
log "Restore instructions:"
log "  tar -xzf ${ARCHIVE_NAME}  (extracts DB, .env, session to their original paths)"
log "  Then run: ./scripts/setup.sh  and  systemctl start fin-bridge"
