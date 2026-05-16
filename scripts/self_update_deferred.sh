#!/bin/bash
# =============================================================================
# self_update_deferred.sh — GitOps Auto-Update Script (Pull & Defer Pattern)
# =============================================================================
#
# This script is designed to be called by a CI runner (GitHub Actions, self-hosted)
# running ON the headnode. It performs a safe rolling update of the cluster:
#
#   1. Pull latest code on the headnode
#   2. Sync dependencies
#   3. Signal each worker to update via their webhook
#   4. Schedule a deferred restart of headnode services (after script exits)
#   5. Exit 0 immediately — the CI reports success before the restart happens
#
# THE TRICK: If we restart the headnode services while this script is running,
# we kill the GitHub Actions runner that launched us. By scheduling the restart
# in a detached background process with a 10s delay, we exit cleanly first.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$BASE_DIR/.env"

# Load environment
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

echo "=== [1/4] Pulling latest code on Headnode ==="
cd "$BASE_DIR"
git pull origin main
echo "✅ Code updated to: $(git rev-parse --short HEAD)"

echo "=== [2/4] Syncing dependencies ==="
UV_CMD="${HOME}/.local/bin/uv"
if [ -x "$UV_CMD" ]; then
    "$UV_CMD" sync 2>&1 || echo "⚠️ uv sync had warnings (non-fatal)"
else
    echo "⚠️ uv not found, skipping dependency sync"
fi

echo "=== [3/4] Signaling workers to update ==="
WORKER_COUNT="${WORKER_COUNT:-0}"
CLUSTER_TOKEN="${CLUSTER_TOKEN:-}"

update_worker() {
    local worker_ip="$1"
    local worker_port="${2:-6000}"
    local url="http://${worker_ip}:${worker_port}/webhook/update_self"

    echo "  → Sending update to $worker_ip..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${CLUSTER_TOKEN}" \
        --connect-timeout 5 \
        --max-time 10 \
        2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "202" ]; then
        echo "  ✅ Worker $worker_ip accepted update (HTTP $HTTP_CODE)"
    elif [ "$HTTP_CODE" = "000" ]; then
        echo "  ⚠️ Worker $worker_ip unreachable (will update on next restart)"
    else
        echo "  ⚠️ Worker $worker_ip returned HTTP $HTTP_CODE"
    fi
}

for ((i=1; i<=WORKER_COUNT; i++)); do
    ip_var="WORKER_${i}_IP"
    ip_val="${!ip_var:-}"
    if [ -n "$ip_val" ]; then
        update_worker "$ip_val"
    fi
done

echo "=== [4/4] Scheduling deferred restart of Headnode services ==="
# We use nohup + disown to fully detach the restart from this process tree.
# The 10-second delay ensures this script has time to exit 0 and the CI runner
# reports success before the services (and the runner itself) are restarted.
nohup bash -c '
    sleep 10
    echo "[DEFERRED] Restarting cluster-scheduler..."
    sudo systemctl restart cluster-scheduler 2>/dev/null || true
    echo "[DEFERRED] Restarting cluster-scheduler-loop..."
    sudo systemctl restart cluster-scheduler-loop 2>/dev/null || true
    echo "[DEFERRED] Restart complete at $(date)"
' > /tmp/cluster-ci-deferred-restart.log 2>&1 &
disown

echo ""
echo "=============================================="
echo "✅ Auto-update complete!"
echo "   Workers: signaled to pull & restart"
echo "   Headnode: restart scheduled in 10 seconds"
echo "   Exiting now (CI will report success)"
echo "=============================================="
exit 0
