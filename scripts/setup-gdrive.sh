#!/usr/bin/env bash
# =============================================================================
# Google Drive setup for automated backups
#
# Run once: ./scripts/setup-gdrive.sh
# After this, backup.sh will automatically upload to Google Drive.
# =============================================================================
set -euo pipefail

REMOTE_NAME="gdrive"
FOLDER_NAME="fin-assistant-backups"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}  [OK] $*${RESET}"; }
info() { echo -e "${CYAN}  -> $*${RESET}"; }
ask()  { echo -e "${YELLOW}${BOLD}$*${RESET}"; }

echo
echo -e "${BOLD}Google Drive backup setup${RESET}"
echo

# -- Install rclone if needed -------------------------------------------------
if ! command -v rclone &>/dev/null; then
    info "Installing rclone..."
    curl -fsSL https://rclone.org/install.sh | sudo bash -s -- --quiet
    ok "rclone installed ($(rclone version | head -1))"
else
    ok "rclone already installed ($(rclone version | head -1))"
fi

# -- Check if already configured ----------------------------------------------
if rclone listremotes 2>/dev/null | grep -q "^${REMOTE_NAME}:"; then
    ok "rclone remote '${REMOTE_NAME}' is already configured"
    echo
    echo -e "  To reconfigure it: ${CYAN}rclone config${RESET}"
    echo -e "  Current remotes:"
    rclone listremotes | sed 's/^/    /'
    echo
    exit 0
fi

# -- Interactive rclone config ------------------------------------------------
echo -e "  This will open rclone's interactive setup to connect Google Drive."
echo
echo -e "  ${BOLD}What you'll do in rclone config:${RESET}"
echo -e "    1. Type ${BOLD}n${RESET} (new remote)"
echo -e "    2. Name it: ${BOLD}${REMOTE_NAME}${RESET}  <- important, must match exactly"
echo -e "    3. Storage type: type ${BOLD}drive${RESET} or its number"
echo -e "    4. Client ID / Secret: press Enter (use defaults)"
echo -e "    5. Scope: choose ${BOLD}1${RESET} (full access)"
echo -e "    6. Root folder ID: press Enter (skip)"
echo -e "    7. Service account: press Enter (skip)"
echo -e "    8. Auto config: ${BOLD}y${RESET} -- opens browser for Google login"
echo -e "    9. Log in with your Google account, allow rclone access"
echo -e "   10. Team drive: ${BOLD}n${RESET}"
echo -e "   11. Confirm with ${BOLD}y${RESET}, then ${BOLD}q${RESET} to quit config"
echo
read -rp "  Press Enter to start rclone config..."
echo

rclone config

# -- Verify -------------------------------------------------------------------
echo
if rclone listremotes 2>/dev/null | grep -q "^${REMOTE_NAME}:"; then
    ok "Remote '${REMOTE_NAME}' configured"
    info "Creating backup folder on Google Drive..."
    rclone mkdir "${REMOTE_NAME}:${FOLDER_NAME}" 2>/dev/null || true
    ok "Folder ready: ${REMOTE_NAME}:${FOLDER_NAME}"
    echo
    echo -e "  ${GREEN}${BOLD}Setup complete.${RESET}"
    echo -e "  Backups will now upload to Google Drive automatically."
    echo -e "  To test:  ${CYAN}./scripts/backup.sh${RESET}"
else
    echo -e "  ${YELLOW}Remote '${REMOTE_NAME}' was not found after config.${RESET}"
    echo -e "  Make sure you named it exactly: ${BOLD}${REMOTE_NAME}${RESET}"
    echo -e "  Re-run: ${CYAN}./scripts/setup-gdrive.sh${RESET}"
    exit 1
fi
