#!/bin/bash
# Incremental ingest orchestrator — bring pm_btc5m.db + features.parquet to today.
#
# Usage:  bash /home/polymarket_work/ingest_incremental.sh
#
# Runs 5 stages serially (trades LAST among ingests — async proxy-wedge risk):
#   1. events + raw_history (PM Gamma + prices-history)
#   2. Binance 1m klines (~2 min)
#   3. Binance futures meta (funding / OI / long-short, ~3 min)
#   4. PM trades (async, 8 concurrent, ~1-2h; timeout 3h guard)
#   5. Rebuild features.parquet (--incremental, ~45s)
#
# Idempotent — re-running brings DB to current state with no duplicates.
# All scripts use INSERT OR IGNORE or auto-resume cursors.
#
# Stop on first failure (set -e). Audit per-stage timing printed to stdout.

set -e

cd /home/polymarket_work

DB=/home/polymarket_work/db/pm_btc5m.db
banner() { echo; echo "════════════════════════════════════════════════════════════════════"; echo "$1"; echo "════════════════════════════════════════════════════════════════════"; }
stamp()  { date '+%H:%M:%S'; }

T0=$(date +%s)

banner "[0/5] pre-flight: db + features status"
echo "  db          : $(ls -lh $DB | awk '{print $5}')"
echo "  events  MAX : $(sqlite3 $DB 'SELECT datetime(MAX(candle_start), "unixepoch") FROM events')"
echo "  trades  MAX : $(sqlite3 $DB 'SELECT datetime(MAX(ts), "unixepoch") FROM trades')"
echo "  binance MAX : $(sqlite3 $DB 'SELECT datetime(MAX(open_ts_ms)/1000, "unixepoch") FROM binance_klines')"
echo "  features    : $(ls -lh scratch/data/features.parquet 2>/dev/null | awk '{print $5, $6, $7, $8}' || echo 'missing')"

banner "[1/5] $(stamp) events + raw_history (backfill_extend)"
T_S=$(date +%s)
# Optional env var: FORCE_FROM=YYYY-MM-DD to re-discover historical days (e.g., bug fix)
uv run python scratch/research/ingestion/backfill_extend_20260511.py ${FORCE_FROM:+--force-from $FORCE_FROM}
echo "  ⏱  $(($(date +%s) - T_S))s"

banner "[2/5] $(stamp) Binance 1m klines"
T_S=$(date +%s)
uv run python scratch/research/ingestion/ingest_binance_klines_20260511.py
echo "  ⏱  $(($(date +%s) - T_S))s"

banner "[3/5] $(stamp) Binance futures meta (funding/OI/long-short)"
T_S=$(date +%s)
uv run python scratch/research/ingestion/ingest_binance_futures_meta_20260512.py
echo "  ⏱  $(($(date +%s) - T_S))s"

# Trades LAST among ingests: async httpx is the known Clash-proxy wedge risk.
# A wedge = hang (not error) → set -e can't catch it, so wrap in `timeout` (kill
# after 3h; ETA ~1.5-2h). Placed after binance so a wedge can't block the cheap
# reliable klines/futures. Re-run resumes via _ingest_v3_progress (per-cid).
banner "[4/5] $(stamp) PM trades (async, 8 concurrent; timeout 3h)"
T_S=$(date +%s)
timeout 10800 uv run python scratch/research/ingestion/ingest_pm_trades_v3_4kcap_backfill_async_20260523.py
echo "  ⏱  $(($(date +%s) - T_S))s"

banner "[5/5] $(stamp) rebuild features.parquet (incremental upsert + merge)"
T_S=$(date +%s)
uv run python scratch/research/build_features.py --incremental
echo "  ⏱  $(($(date +%s) - T_S))s"

banner "DONE @ $(stamp)  total $(($(date +%s) - T0))s"
echo "  events  MAX : $(sqlite3 $DB 'SELECT datetime(MAX(candle_start), "unixepoch") FROM events')"
echo "  trades  MAX : $(sqlite3 $DB 'SELECT datetime(MAX(ts), "unixepoch") FROM trades')"
echo "  binance MAX : $(sqlite3 $DB 'SELECT datetime(MAX(open_ts_ms)/1000, "unixepoch") FROM binance_klines')"
echo "  features    : $(ls -lh scratch/data/features.parquet | awk '{print $5, $6, $7, $8}')"
