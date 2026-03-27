#!/usr/bin/env bash
# Verify llama.cpp is running on NUMA node 0 cores

set -euo pipefail

echo "=== NUMA Topology ==="
numactl --hardware 2>/dev/null || echo "numactl not available"

echo ""
echo "=== llama-server NUMA memory usage ==="
PIDS=$(pgrep -x llama-server 2>/dev/null || true)
if [[ -z "$PIDS" ]]; then
    echo "llama-server is not running"
    exit 0
fi

for PID in $PIDS; do
    echo "PID: $PID"
    echo "  CPU affinity: $(taskset -cp "$PID" 2>/dev/null | awk '{print $NF}')"
    numastat -p "$PID" 2>/dev/null || echo "  numastat: permission denied (run as root)"
done

echo ""
echo "=== System NUMA stats ==="
numastat -n 2>/dev/null || echo "numastat -n: permission denied (run as root)"
