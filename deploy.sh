#!/bin/bash
# Deploy polybot code to VPS. Never touches polybot.db or .venv.
set -e

cd /home/polymarket_work

echo "=== pytest: unit + integration (scanner smoke + live-dry contract) ==="
uv run --project polybot python -m pytest tests/ -q
# Aborts deploy if any test red. Covers: roster load+validate, dashboard view, compute+
# evaluate per expr, INSERT/seed roundtrip, live order construction (no-fund-touch).
# NOTE: tests pin their own factor_roster (tests/conftest.py) — the live roster is data
# in prod polybot.db, edited there directly, so deploy cannot validate it. A bad expr
# fails loud at service start instead (load_roster raises before any order is placed).

echo "=== Regen BASE_COLS from features.parquet (SSOT codegen) ==="
uv run python tools/gen_base_cols.py

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
