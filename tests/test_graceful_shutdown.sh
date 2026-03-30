#!/usr/bin/env bash
# Test graceful shutdown of Hassaleh Daemon
# Usage: sudo bash tests/test_graceful_shutdown.sh
set -euo pipefail

cd /home/uranus/moltbot-workspace/projects/hassaleh

echo "=== Graceful Shutdown Test ==="

# 1. Verify daemon is running
echo "1. Checking daemon is running..."
systemctl is-active hassaleh-daemon || { echo "❌ Daemon not running"; exit 1; }
echo "   ✅ Daemon active"

# 2. Check health
echo "2. Health check..."
HEALTH=$(curl -s http://127.0.0.1:9100/health)
echo "   $HEALTH"

# 3. Send SIGTERM via systemctl stop
echo "3. Stopping daemon (SIGTERM)..."
systemctl stop hassaleh-daemon
echo "   ✅ Stop command sent"

# 4. Check no zombie Intents left
echo "4. Checking for zombie Intents..."
ZOMBIES=$(cypher-shell -u neo4j -p "hassaleh-dev-2026" -a bolt://localhost:7690 \
    "MATCH (i:Intent {lifecycle: 'running'}) RETURN count(i) AS c" 2>/dev/null | tail -1)
if [[ "$ZOMBIES" == "0" ]]; then
    echo "   ✅ No zombie Intents"
else
    echo "   ❌ Found $ZOMBIES zombie Intent(s)!"
fi

# 5. Restart daemon
echo "5. Restarting daemon..."
systemctl start hassaleh-daemon
sleep 2
systemctl is-active hassaleh-daemon && echo "   ✅ Daemon restarted" || echo "   ❌ Daemon failed to restart"

# 6. Health check after restart
echo "6. Post-restart health check..."
sleep 1
curl -s http://127.0.0.1:9100/health | python3.13 -m json.tool

echo ""
echo "=== Graceful Shutdown Test Complete ==="
