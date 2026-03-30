#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# neo4j-instances.sh — Manage multiple Neo4j Community instances
#                      for Hassaleh (graph-native agentic framework)
#
# Usage:
#   ./neo4j-instances.sh setup              # Create prod + test1 + test2 instances
#   ./neo4j-instances.sh start  [prod|test1|test2|all]
#   ./neo4j-instances.sh stop   [prod|test1|test2|all]
#   ./neo4j-instances.sh status [prod|test1|test2|all]
#   ./neo4j-instances.sh clone  [prod|test1|test2|all]  # Fresh copy from reference DB
#   ./neo4j-instances.sh destroy [prod|test1|test2|all]  # Remove instance data
#   ./neo4j-instances.sh info                             # Show connection details
#
# Hassaleh instances:
#   prod  (production):  bolt://localhost:7690  http://localhost:7477
#   test1 (integration): bolt://localhost:7691  http://localhost:7478
#   test2 (dev/sandbox): bolt://localhost:7692  http://localhost:7479
#
# Port allocation across projects:
#   GWW3 reference:      7687 / 7474
#   GWW3 test1:          7688 / 7475
#   GWW3 test2:          7689 / 7476
#   Hassaleh prod:       7690 / 7477
#   Hassaleh test1:      7691 / 7478
#   Hassaleh test2:      7692 / 7479
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ──
MAIN_HOME="/var/lib/neo4j"
MAIN_CONF="/etc/neo4j"
HASSALEH_PASS="hassaleh-dev-2026"

BASE_DIR="/var/lib/neo4j-instances/hassaleh"
CONF_DIR="/etc/neo4j-instances/hassaleh"
LOG_DIR="/var/log/neo4j-instances/hassaleh"

# Instance definitions: name bolt_port http_port heap pagecache
INSTANCES=(
    "prod  7690 7477 1g   512m"
    "test1 7691 7478 512m 512m"
    "test2 7692 7479 512m 512m"
)

NEO4J_BIN="/usr/share/neo4j/bin/neo4j"

# ── Helpers ──
log() { echo -e "\033[1;35m[hassaleh-neo4j]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }
ok()  { echo -e "\033[1;32m[OK]\033[0m $*"; }

get_instance_fields() {
    local inst="$1"
    for entry in "${INSTANCES[@]}"; do
        local name bolt http heap pagecache
        read -r name bolt http heap pagecache <<< "$entry"
        if [[ "$name" == "$inst" ]]; then
            echo "$name $bolt $http $heap $pagecache"
            return 0
        fi
    done
    return 1
}

all_names() {
    for entry in "${INSTANCES[@]}"; do
        echo "$entry" | awk '{print $1}'
    done
}

resolve_targets() {
    local target="${1:-all}"
    if [[ "$target" == "all" ]]; then
        all_names
    else
        echo "$target"
    fi
}

# ── Setup: create directories + config for each instance ──
cmd_setup() {
    log "Setting up Hassaleh Neo4j instances..."

    sudo mkdir -p "$BASE_DIR" "$CONF_DIR" "$LOG_DIR"

    for entry in "${INSTANCES[@]}"; do
        local name bolt http heap pagecache
        read -r name bolt http heap pagecache <<< "$entry"

        local inst_home="$BASE_DIR/$name"
        local inst_conf="$CONF_DIR/$name"
        local inst_log="$LOG_DIR/$name"

        log "Creating instance: $name (Bolt: $bolt, HTTP: $http, Heap: $heap)"

        # Create directories
        sudo mkdir -p "$inst_home/data/databases" "$inst_home/data/transactions"
        sudo mkdir -p "$inst_home/run" "$inst_home/import"
        sudo mkdir -p "$inst_conf" "$inst_log"

        # Create neo4j.conf for this instance
        sudo tee "$inst_conf/neo4j.conf" > /dev/null << CONF
# Auto-generated config for Hassaleh instance: $name
# Managed by neo4j-instances.sh — do not edit manually

# ── Directories ──
server.directories.data=$inst_home/data
server.directories.plugins=/var/lib/neo4j/plugins
server.directories.logs=$inst_log
server.directories.lib=/usr/share/neo4j/lib
server.directories.import=$inst_home/import
server.directories.run=$inst_home/run

# ── Network (unique ports) ──
server.bolt.enabled=true
server.bolt.listen_address=:$bolt
server.bolt.advertised_address=:$bolt
server.http.enabled=true
server.http.listen_address=:$http
server.http.advertised_address=:$http
server.https.enabled=false

# ── Memory ──
server.memory.heap.initial_size=$heap
server.memory.heap.max_size=$heap
server.memory.pagecache.size=$pagecache

# ── Query language ──
db.query.default_language=CYPHER_25

# ── Recovery (allow starting from cloned DB files without transaction logs) ──
db.recovery.fail_on_missing_files=false

# ── Logging ──
server.logs.config=$MAIN_CONF/server-logs.xml
server.logs.user.config=$MAIN_CONF/user-logs.xml

# ── JVM ──
server.jvm.additional=-XX:+UseG1GC
server.jvm.additional=-XX:-OmitStackTraceInFastThrow
server.jvm.additional=-XX:+AlwaysPreTouch
server.jvm.additional=-XX:+UnlockExperimentalVMOptions
server.jvm.additional=-XX:+TrustFinalNonStaticFields
server.jvm.additional=-XX:+DisableExplicitGC
server.jvm.additional=-Djdk.nio.maxCachedBufferSize=1024
server.jvm.additional=-Dio.netty.tryReflectionSetAccessible=true
server.jvm.additional=-Dio.netty.leakDetection.level=DISABLED
server.jvm.additional=-Djdk.tls.ephemeralDHKeySize=2048
server.jvm.additional=-Djdk.tls.rejectClientInitiatedRenegotiation=true
server.jvm.additional=-XX:FlightRecorderOptions=stackdepth=256
server.jvm.additional=-XX:+UnlockDiagnosticVMOptions
server.jvm.additional=-XX:+DebugNonSafepoints
server.jvm.additional=--add-opens=java.base/java.nio=ALL-UNNAMED
server.jvm.additional=--add-opens=java.base/java.io=ALL-UNNAMED
server.jvm.additional=--add-opens=java.base/sun.nio.ch=ALL-UNNAMED
server.jvm.additional=--add-opens=java.base/java.util.concurrent=ALL-UNNAMED
server.jvm.additional=--enable-native-access=ALL-UNNAMED
server.jvm.additional=-Dorg.neo4j.shaded.lucene9.vectorization.upperJavaFeatureVersion=25
server.jvm.additional=-Dlog4j2.disable.jmx=true
server.jvm.additional=-Dlog4j.layout.jsonTemplate.maxStringLength=32768

# ── APOC ──
dbms.security.procedures.unrestricted=apoc.*
dbms.security.procedures.allowlist=apoc.*
CONF

        # Copy logging configs
        sudo cp "$MAIN_CONF/server-logs.xml" "$inst_conf/" 2>/dev/null || true
        sudo cp "$MAIN_CONF/user-logs.xml" "$inst_conf/" 2>/dev/null || true

        # Symlink shared directories from main installation
        for dir in web products labs certificates licenses; do
            if [[ -d "$MAIN_HOME/$dir" ]] && [[ ! -e "$inst_home/$dir" ]]; then
                sudo ln -s "$MAIN_HOME/$dir" "$inst_home/$dir"
            fi
        done

        # Copy packaging_info if present
        [[ -f "$MAIN_HOME/packaging_info" ]] && sudo cp "$MAIN_HOME/packaging_info" "$inst_home/" 2>/dev/null || true

        # Set ownership
        sudo chown -R neo4j:adm "$inst_home" "$inst_conf" "$inst_log"

        ok "Instance $name configured"
    done

    # Initialize system databases for each instance
    for name in $(all_names); do
        local inst_home="$BASE_DIR/$name"
        local inst_conf="$CONF_DIR/$name"

        if [[ ! -d "$inst_home/data/databases/system" ]]; then
            log "Initializing system database for $name..."
            sudo NEO4J_HOME="$inst_home" NEO4J_CONF="$inst_conf" \
                neo4j-admin dbms set-initial-password "$HASSALEH_PASS" 2>/dev/null || true
        fi
    done

    ok "Setup complete!"
    echo ""
    cmd_info
}

# ── Clone: copy reference DB into instance (for Hassaleh, clone from prod) ──
cmd_clone() {
    local targets
    targets=$(resolve_targets "${1:-all}")

    local source_home="$BASE_DIR/prod"

    for name in $targets; do
        local inst_home="$BASE_DIR/$name"

        # Don't clone prod from itself
        if [[ "$name" == "prod" ]]; then
            log "Skipping prod (clone source = prod)"
            continue
        fi

        # Stop instance if running
        if _is_running "$name"; then
            log "Stopping $name before cloning..."
            cmd_stop "$name"
            sleep 2
        fi

        log "Cloning prod → $name ..."

        # Remove old neo4j data (keep system DB intact)
        sudo rm -rf "$inst_home/data/databases/neo4j"
        sudo rm -rf "$inst_home/data/transactions/neo4j"

        # Copy database files
        sudo cp -a "$source_home/data/databases/neo4j" "$inst_home/data/databases/neo4j"

        # Copy transaction logs (critical!)
        if [[ -d "$source_home/data/transactions/neo4j" ]]; then
            sudo cp -a "$source_home/data/transactions/neo4j" "$inst_home/data/transactions/neo4j"
            log "Transaction logs copied"
        else
            sudo mkdir -p "$inst_home/data/transactions/neo4j"
            log "No transaction logs found in prod — recovery mode will handle it"
        fi

        # Fix ownership
        sudo chown -R neo4j:adm "$inst_home/data"

        ok "$name cloned from prod"
    done
}

# ── Start ──
cmd_start() {
    local targets
    targets=$(resolve_targets "${1:-all}")

    for name in $targets; do
        if _is_running "$name"; then
            log "$name is already running (PID: $(_get_pid "$name"))"
            continue
        fi

        local inst_home="$BASE_DIR/$name"
        local inst_conf="$CONF_DIR/$name"

        log "Starting $name ..."
        sudo NEO4J_HOME="$inst_home" NEO4J_CONF="$inst_conf" "$NEO4J_BIN" start

        ok "$name started"
    done
}

# ── Stop ──
cmd_stop() {
    local targets
    targets=$(resolve_targets "${1:-all}")

    for name in $targets; do
        if ! _is_running "$name"; then
            log "$name is not running"
            continue
        fi

        local inst_home="$BASE_DIR/$name"
        local inst_conf="$CONF_DIR/$name"

        log "Stopping $name ..."
        sudo NEO4J_HOME="$inst_home" NEO4J_CONF="$inst_conf" "$NEO4J_BIN" stop

        ok "$name stopped"
    done
}

# ── Status ──
cmd_status() {
    local targets
    targets=$(resolve_targets "${1:-all}")

    echo ""
    printf "%-12s %-10s %-8s %-10s %-10s\n" "INSTANCE" "STATUS" "PID" "BOLT" "HTTP"
    printf "%-12s %-10s %-8s %-10s %-10s\n" "--------" "------" "---" "----" "----"

    for name in $targets; do
        local fields bolt http
        fields=$(get_instance_fields "$name")
        read -r _ bolt http _ _ <<< "$fields"

        local pid status_text
        pid=$(_get_pid "$name")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            status_text="\033[1;32mrunning\033[0m"
        else
            status_text="\033[1;31mstopped\033[0m"
            pid="-"
        fi
        printf "%-12s ${status_text}     %-8s %-10s %-10s\n" "$name" "$pid" "$bolt" "$http"
    done
    echo ""
}

# ── Info ──
cmd_info() {
    echo ""
    echo "┌──────────────────────────────────────────────────────────────┐"
    echo "│  Hassaleh ⭐ Neo4j Instances                                │"
    echo "├──────────┬──────────────────────┬───────────────────────────┤"
    echo "│ Instance │ Bolt                 │ Browser                   │"
    echo "├──────────┼──────────────────────┼───────────────────────────┤"
    echo "│ prod     │ bolt://localhost:7690 │ http://localhost:7477     │"
    echo "│ test1    │ bolt://localhost:7691 │ http://localhost:7478     │"
    echo "│ test2    │ bolt://localhost:7692 │ http://localhost:7479     │"
    echo "├──────────┴──────────────────────┴───────────────────────────┤"
    echo "│ User: neo4j  Password: $HASSALEH_PASS                │"
    echo "│ Memory: prod=1G heap, test=512M heap + 512M cache each     │"
    echo "└────────────────────────────────────────────────────────────┘"
    echo ""
    echo "Python connection:"
    echo "  Production:   NEO4J_URI=bolt://localhost:7690"
    echo "  Test 1:       NEO4J_URI=bolt://localhost:7691"
    echo "  Test 2:       NEO4J_URI=bolt://localhost:7692"
    echo ""
    echo "Clone prod → test instances:"
    echo "  $0 clone all"
    echo ""
}

# ── Destroy ──
cmd_destroy() {
    local targets
    targets=$(resolve_targets "${1:-all}")

    for name in $targets; do
        cmd_stop "$name" 2>/dev/null || true
        local inst_home="$BASE_DIR/$name"
        log "Removing $name data..."
        sudo rm -rf "$inst_home/data/databases/neo4j" "$inst_home/data/transactions/neo4j"
        ok "$name data removed"
    done
}

# ── Internal helpers ──
_get_pid() {
    local name="$1"
    local pidfile="$BASE_DIR/$name/run/neo4j.pid"
    cat "$pidfile" 2>/dev/null || echo ""
}

_is_running() {
    local pid
    pid=$(_get_pid "$1")
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

# ── Main dispatch ──
case "${1:-help}" in
    setup)   cmd_setup ;;
    start)   cmd_start "${2:-all}" ;;
    stop)    cmd_stop "${2:-all}" ;;
    status)  cmd_status "${2:-all}" ;;
    clone)   cmd_clone "${2:-all}" ;;
    destroy) cmd_destroy "${2:-all}" ;;
    info)    cmd_info ;;
    help|*)
        echo "Usage: $0 {setup|start|stop|status|clone|destroy|info} [prod|test1|test2|all]"
        ;;
esac
