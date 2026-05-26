#!/bin/bash
# Deploy polybot code to VPS. Never touches polybot.db or .venv.
set -e

echo "=== SSOT verify: mining ↔ polybot.lib.compute byte-equal ==="
cd /home/polymarket_work
uv run python scratch/tools/verify_compute_ssot.py --n-events 30
# Fails (exit 1) if mining batch impl diverges from polybot per-event impl.
# Common cause: someone added math to mining/features/<x>.py without delegating
# to polybot.lib.compute, or modified polybot without re-syncing mining.

echo "=== Dashboard SSOT verify: ACTIVE ⊆ paper_candidates ==="
uv run python scratch/tools/verify_active_audit.py
# Fails (exit 1) if any ACTIVE strategy has no paper_candidates audit row.
# Common cause: deploying a strategy without going through mining cycle INSERT.

echo "=== Regen BASE_COLS from features.parquet (SSOT codegen) ==="
uv run python scratch/tools/gen_base_cols.py

echo "=== Deploying to VPS ==="
rsync -avz \
  --exclude='polybot.db' \
  --exclude='.venv' \
  --exclude='polybot.log' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='backups/' \
  /home/polymarket_work/polybot/ vps:/opt/polybot/

echo "=== Syncing VPS venv to uv.lock ==="
ssh vps "cd /opt/polybot && uv sync"

echo "=== Restarting service ==="
ssh vps "systemctl restart polybot"

echo "=== Checking status ==="
ssh vps "systemctl status polybot --no-pager -l | head -15"

echo "=== Done ==="
