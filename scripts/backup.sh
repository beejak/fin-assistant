#!/usr/bin/env bash
# Backup script — creates a portable archive of everything needed to restore
# Usage: ./scripts/backup.sh [output_dir]
# Restore: tar -xzf fin-assistant-backup-YYYYMMDD.tar.gz

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$HOME}"
STAMP="$(date +%Y%m%d_%H%M)"
ARCHIVE="$OUT_DIR/fin-assistant-backup-$STAMP.tar.gz"

echo "Backing up to $ARCHIVE ..."

# Stop bridge temporarily for clean DB snapshot
BRIDGE_RUNNING=false
if systemctl is-active --quiet fin-bridge 2>/dev/null; then
    systemctl stop fin-bridge
    BRIDGE_RUNNING=true
fi

tar -czf "$ARCHIVE" \
    --exclude="$REPO_DIR/__pycache__" \
    --exclude="$REPO_DIR/**/__pycache__" \
    --exclude="$REPO_DIR/logs" \
    --exclude="$REPO_DIR/.venv" \
    -C "$(dirname "$REPO_DIR")" \
    "$(basename "$REPO_DIR")" \
    -C "$HOME" \
    "$(basename "$(ls "$HOME"/*.session 2>/dev/null | head -1)" 2>/dev/null || true)" \
    2>/dev/null || true

# Restart bridge
if $BRIDGE_RUNNING; then
    systemctl start fin-bridge
fi

SIZE=$(du -sh "$ARCHIVE" | cut -f1)
echo "Backup complete: $ARCHIVE ($SIZE)"
echo ""
echo "To restore on a new machine:"
echo "  1. tar -xzf $(basename "$ARCHIVE") -C ~"
echo "  2. cd fin-assistant && ./scripts/setup.sh"
echo "  3. Restore .env manually (not included — keep separately)"
