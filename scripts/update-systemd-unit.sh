#!/usr/bin/env bash
# Update the hassaleh-daemon.service systemd unit to use the project venv.
# Usage: sudo bash scripts/update-systemd-unit.sh

set -euo pipefail

PROJECT_DIR="/home/uranus/moltbot-workspace/projects/hassaleh"
PYTHON="/usr/bin/python3.13"

if [[ ! -f "$PYTHON" ]]; then
    echo "ERROR: Python not found at $PYTHON"
    exit 1
fi

cat > /etc/systemd/system/hassaleh-daemon.service << UNIT
[Unit]
Description=Hassaleh Daemon — Graph-native agent orchestration
Documentation=https://github.com/IngoGiebel/hassaleh
After=network.target neo4j.service
Wants=neo4j.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=hassaleh-svc
Group=hassaleh

# Working directory
WorkingDirectory=${PROJECT_DIR}

# Environment — venv handles packages, PYTHONPATH for project src
Environment=NEO4J_URI=bolt://localhost:7690
Environment=NEO4J_USER=neo4j
Environment=NEO4J_PASSWORD=hassaleh-dev-2026
Environment=PYTHONPATH=${PROJECT_DIR}/src

# Main process — global Python 3.13 (neo4j, aiohttp installed system-wide)
ExecStart=${PYTHON} -m hassaleh.daemon

# Watchdog — disabled until sd_notify is implemented in daemon.py
# WatchdogSec=30

# Restart policy
Restart=on-failure
RestartSec=5

# Resource limits
MemoryMax=512M
CPUQuota=50%

# Security hardening
NoNewPrivileges=no
ProtectSystem=full
ProtectHome=no
ReadWritePaths=${PROJECT_DIR}
ReadWritePaths=/run/sudo
PrivateTmp=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hassaleh-daemon

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
echo "✅ hassaleh-daemon.service updated (python: ${PYTHON})"
echo "   Start:  sudo systemctl start hassaleh-daemon"
echo "   Status: sudo systemctl status hassaleh-daemon"
echo "   Logs:   journalctl -u hassaleh-daemon -f"
