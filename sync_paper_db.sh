#!/bin/bash
# Pull live VPS paper db to local backups/. Idempotent, safe.
# Usage: bash sync_paper_db.sh

set -e
DEST=/home/polymarket_work/polybot/backups/polybot_live.db
rsync -avz --no-perms vps:/opt/polybot/polybot.db "$DEST"
echo "synced @ $(date '+%Y-%m-%d %H:%M:%S')"
echo "  → $DEST"
echo "  $(stat -c%s "$DEST" | numfmt --to=iec) ($(stat -c%s "$DEST") bytes)"
