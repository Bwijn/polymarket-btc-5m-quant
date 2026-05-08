#!/bin/bash
# Deploy polybot code to VPS. Never touches polybot.db or .venv.
set -e

echo "=== Deploying to VPS ==="
rsync -avz \
  --exclude='polybot.db' \
  --exclude='.venv' \
  --exclude='polybot.log' \
  --exclude='__pycache__' \
  --exclude='.env' \
  --exclude='*.pyc' \
  --exclude='backups/' \
  /home/polymarket_work/polybot/ vps:/opt/polybot/

echo "=== Restarting service ==="
ssh vps "systemctl restart polybot"

echo "=== Checking status ==="
ssh vps "systemctl status polybot --no-pager -l | head -15"

echo "=== Done ==="
