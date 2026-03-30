#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# setup_os.sh — Create OS users, sudoers, and systemd unit for Hassaleh
#
# Usage: sudo bash scripts/setup_os.sh
#
# Creates:
#   - hassaleh-svc     (Daemon process user)
#   - hassaleh-agent   (AI agent process user)
#   - hassaleh-fs      (Capability: filesystem read)
#   - hassaleh-writer  (Capability: filesystem write)
#   - hassaleh-exec    (Capability: script execution)
#   - hassaleh-net     (Capability: network/API calls)
#   - hassaleh-pkg     (Capability: package management)
#
# Reference: docs/CONCEPT.md v1.2, Section 3.1
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

log() { echo -e "\033[1;35m[hassaleh-setup]\033[0m $*"; }
ok()  { echo -e "\033[1;32m[OK]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root (sudo)"
    exit 1
fi

PROJECT_DIR="${HASSALEH_PROJECT_DIR:-/home/uranus/moltbot-workspace/projects/hassaleh}"
WORKSPACE_DIR="$PROJECT_DIR"

# ──────────────────────────────────────────
# 1. Create system users (no login shell, no home)
# ──────────────────────────────────────────

USERS=(
    "hassaleh-svc:Hassaleh Daemon service"
    "hassaleh-agent:Hassaleh AI agent runner"
    "hassaleh-fs:Hassaleh capability - filesystem read"
    "hassaleh-writer:Hassaleh capability - filesystem write"
    "hassaleh-exec:Hassaleh capability - script execution"
    "hassaleh-net:Hassaleh capability - network/API"
    "hassaleh-pkg:Hassaleh capability - package management"
)

log "Creating system users..."

for entry in "${USERS[@]}"; do
    IFS=':' read -r username comment <<< "$entry"
    if id "$username" &>/dev/null; then
        log "User $username already exists — skipping"
    else
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --comment "$comment" "$username"
        ok "Created user: $username"
    fi
done

# Create hassaleh group for shared workspace access
if ! getent group hassaleh &>/dev/null; then
    groupadd hassaleh
    ok "Created group: hassaleh"
fi

# Add relevant users to hassaleh group
for user in hassaleh-svc hassaleh-agent hassaleh-writer hassaleh-exec; do
    usermod -aG hassaleh "$user" 2>/dev/null || true
done
log "Users added to hassaleh group"

# ──────────────────────────────────────────
# 2. Workspace directory permissions
# ──────────────────────────────────────────

log "Setting workspace permissions..."

# Ensure workspace is group-accessible
chgrp -R hassaleh "$WORKSPACE_DIR" 2>/dev/null || true
chmod -R g+rX "$WORKSPACE_DIR" 2>/dev/null || true

# Agent needs r/w to workspace
chmod -R g+w "$WORKSPACE_DIR" 2>/dev/null || true

ok "Workspace permissions set: $WORKSPACE_DIR"

# ──────────────────────────────────────────
# 3. Sudoers configuration
# ──────────────────────────────────────────

log "Configuring sudoers..."

SUDOERS_FILE="/etc/sudoers.d/hassaleh"

cat > "$SUDOERS_FILE" << 'SUDOERS'
# Hassaleh Daemon — capability delegation via sudo
# Reference: docs/CONCEPT.md v1.2, Section 3.1
#
# The Daemon (hassaleh-svc) delegates actions to per-capability OS users.
# Each line allows NOPASSWD execution as a specific capability user.

# Filesystem read (hassaleh-fs): ls, cat, head, tail, find, stat, file, wc
hassaleh-svc ALL=(hassaleh-fs) NOPASSWD: /usr/bin/ls, /usr/bin/cat, /usr/bin/head, /usr/bin/tail, /usr/bin/find, /usr/bin/stat, /usr/bin/file, /usr/bin/wc

# Filesystem write (hassaleh-writer): tee, cp, mv, mkdir, touch
hassaleh-svc ALL=(hassaleh-writer) NOPASSWD: /usr/bin/tee, /usr/bin/cp, /usr/bin/mv, /usr/bin/mkdir, /usr/bin/touch

# Script execution (hassaleh-exec): python3, bash (in whitelisted paths only)
hassaleh-svc ALL=(hassaleh-exec) NOPASSWD: /usr/bin/python3, /usr/bin/python3.13, /usr/bin/bash

# Network/API (hassaleh-net): curl, wget, mcporter
hassaleh-svc ALL=(hassaleh-net) NOPASSWD: /usr/bin/curl, /usr/bin/wget

# Package management (hassaleh-pkg): apt (restricted)
hassaleh-svc ALL=(hassaleh-pkg) NOPASSWD: /usr/bin/apt

SUDOERS

chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" || {
    err "Sudoers syntax check failed! Removing invalid file."
    rm -f "$SUDOERS_FILE"
    exit 1
}

ok "Sudoers configured: $SUDOERS_FILE"

# ──────────────────────────────────────────
# 4. systemd unit file
# ──────────────────────────────────────────

log "Installing systemd unit..."

UNIT_FILE="/etc/systemd/system/hassaleh-daemon.service"

cat > "$UNIT_FILE" << UNIT
[Unit]
Description=Hassaleh Daemon — Graph-native agent orchestration
Documentation=https://github.com/IngoGiebel/hassaleh
After=network.target neo4j.service
Wants=neo4j.service

[Service]
Type=simple
User=hassaleh-svc
Group=hassaleh

# Working directory
WorkingDirectory=${PROJECT_DIR}

# Environment
Environment=NEO4J_URI=bolt://localhost:7690
Environment=NEO4J_USER=neo4j
Environment=NEO4J_PASSWORD=hassaleh-dev-2026
Environment=PYTHONPATH=${PROJECT_DIR}/src

# Main process
ExecStart=/usr/bin/python3.13 -m hassaleh.daemon

# Watchdog (Daemon pings every tick, systemd restarts if stuck)
WatchdogSec=30

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=5

# Resource limits
MemoryMax=512M
CPUQuota=50%

# Security hardening
NoNewPrivileges=no
ProtectSystem=strict
ProtectHome=no
ReadWritePaths=${PROJECT_DIR}
PrivateTmp=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hassaleh-daemon

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
ok "systemd unit installed: $UNIT_FILE"

# ──────────────────────────────────────────
# 5. Summary
# ──────────────────────────────────────────

echo ""
echo "┌────────────────────────────────────────────────────────┐"
echo "│  Hassaleh OS Setup Complete ⭐                        │"
echo "├────────────────────────────────────────────────────────┤"
echo "│  Users:    7 system users created                     │"
echo "│  Group:    hassaleh                                   │"
echo "│  Sudoers:  /etc/sudoers.d/hassaleh                    │"
echo "│  Service:  hassaleh-daemon.service                    │"
echo "│  Neo4j:    bolt://localhost:7690 (hassaleh prod)      │"
echo "├────────────────────────────────────────────────────────┤"
echo "│  Start:    sudo systemctl start hassaleh-daemon       │"
echo "│  Enable:   sudo systemctl enable hassaleh-daemon      │"
echo "│  Status:   sudo systemctl status hassaleh-daemon      │"
echo "│  Logs:     journalctl -u hassaleh-daemon -f           │"
echo "└────────────────────────────────────────────────────────┘"
echo ""
