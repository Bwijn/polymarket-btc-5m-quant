#!/bin/bash
# Run a scratch probe/fix script on VPS with auto-cleanup.
#
# Usage: ./run_probe.sh scratch/probe_xxx_YYYYMMDD.py
#
# Flow: scp -> ssh run via /opt/polybot venv -> rm from VPS /tmp/
# Guarantees: VPS /tmp is cleaned even if the script itself errors.
# Local scratch/ original is preserved (time capsule).

set -euo pipefail

SCRIPT="${1:-}"
if [[ -z "$SCRIPT" ]]; then
    echo "usage: $0 <path/to/scratch/script.py>" >&2
    exit 1
fi
if [[ ! -f "$SCRIPT" ]]; then
    echo "error: file not found: $SCRIPT" >&2
    exit 1
fi

NAME=$(basename "$SCRIPT")
REMOTE="/tmp/$NAME"

cleanup() {
    ssh vps "rm -f $REMOTE" 2>/dev/null || true
    echo "[cleanup] $REMOTE removed from VPS"
}
trap cleanup EXIT

echo "[1/3] scp $SCRIPT -> vps:$REMOTE"
scp -q "$SCRIPT" "vps:$REMOTE"

echo "[2/3] exec via /opt/polybot/.venv/bin/python3"
ssh vps "/opt/polybot/.venv/bin/python3 $REMOTE"

echo "[3/3] done"
