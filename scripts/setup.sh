#!/usr/bin/env bash
# Full setup for the financial assistant on a fresh Ubuntu/Debian system
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "Setting up in $REPO_DIR"

# 1. System packages
apt-get update -qq
apt-get install -y python3 python3-pip sqlite3 git curl

# 2. Python deps
pip3 install --break-system-packages -r "$REPO_DIR/requirements.txt"

# 3. Create dirs
mkdir -p "$REPO_DIR/store" "$REPO_DIR/logs"

# 4. DB schema
sqlite3 "$REPO_DIR/store/messages.db" < "$REPO_DIR/db/schema.sql"
echo "DB initialised"

# 5. .env
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "Created .env — FILL IN YOUR CREDENTIALS before running"
fi

# 6. Systemd service for bridge
cp "$REPO_DIR/systemd/fin-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable fin-bridge.service
echo "Bridge service installed (not started — run: systemctl start fin-bridge)"

# 7. Crontab
crontab "$REPO_DIR/systemd/crontab.txt"
echo "Crontab installed"

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Authenticate Pyrogram: python3 bridge/fetch.py 1"
echo "  3. Start bridge: systemctl start fin-bridge"
echo "  4. Back-fill history: python3 main.py fetch 7"
