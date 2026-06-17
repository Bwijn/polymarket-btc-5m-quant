"""PM trades → _features_pmtrades.parquet builder (delegates to polybot.lib.compute).

Owns the FULL intra-candle feature surface post 4000-cap backfill, but the math
SSOT lives in polybot.lib.compute (so mining and scanner runtime always agree
byte-for-byte — no test_compute_equivalence needed for pmt cols, it's the same
function call).

Output: ~200 base cols (see compute.compute_pmtrades_features for definitions).

Memory-conscious: stream trades per cid via SQL (cid+ts idx), don't load all.
"""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

import pandas as pd

from polybot.lib.compute import compute_pmtrades_features, _pmt_nan_record

from ._common import DB, OUT_DIR, existing_cs, write_incremental


def build(incremental: bool = False) -> Path:
    """Build _features_pmtrades.parquet (~200 base cols).
    incremental=True: skip cs already in the parquet, compute only new events."""
    print(); print('=' * 70); print('pmtrades.build: trades → _features_pmtrades.parquet'); print('=' * 70)
    out = OUT_DIR / '_features_pmtrades.parquet'
    skip = existing_cs(out) if incremental else set()
    con = sqlite3.connect(DB)
    events = pd.read_sql("SELECT cid, candle_start AS cs, up_token, down_token FROM events ORDER BY candle_start", con)
    events = events[~events['cs'].isin(skip)]

    print('  building cid → has_trades index (skip-scan)...', flush=True)
    # skip-scan via recursive CTE: jump cid→next-cid (~28.5k seeks) instead of
    # scanning all 95M index rows. SQLite won't skip-scan DISTINCT automatically
    # (EXPLAIN shows full COVERING INDEX SCAN). Same result, 419s → 6s.
    cids_with_trades = set(r[0] for r in con.execute("""
        WITH RECURSIVE d(cid) AS (
            SELECT MIN(cid) FROM trades
            UNION ALL
            SELECT (SELECT MIN(cid) FROM trades WHERE cid > d.cid)
            FROM d WHERE d.cid IS NOT NULL
        )
        SELECT cid FROM d WHERE cid IS NOT NULL
    """).fetchall())
    print(f"  events: {len(events)} (incremental skip {len(skip)}), cids_with_trades: {len(cids_with_trades)}")

    records = []
    t_start = time.time()
    n_skip = n_have = 0
    cur = con.cursor()

    for i, ev in enumerate(events.itertuples(index=False)):
        cid, cs, up_token, dn_token = ev.cid, ev.cs, ev.up_token, ev.down_token

        if cid not in cids_with_trades:
            rec = {'cid': cid, 'cs': cs}
            rec.update(_pmt_nan_record())
            records.append(rec)
            n_skip += 1
            continue
        n_have += 1

        # Window: cs-300 .. cs+330 covers all pre/intra usages.
        rows = cur.execute(
            "SELECT ts, side, price, size, asset, proxy_wallet FROM trades "
            "WHERE cid=? AND ts >= ? AND ts < ? ORDER BY ts",
            (cid, cs - 300, cs + 330)
        ).fetchall()

        rec = {'cid': cid, 'cs': cs}
        rec.update(compute_pmtrades_features(rows, cs, up_token, dn_token))
        records.append(rec)
        if (i + 1) % 500 == 0:
            elapsed = time.time() - t_start
            print(f"  [{i+1}/{len(events)}] elapsed={elapsed:.0f}s rate={(i+1)/elapsed:.0f} ev/s, "
                  f"no-trade={n_skip}, have={n_have}", flush=True)

    con.close()
    df = write_incremental(out, records, incremental=incremental)
    print(f"  → {out} shape={df.shape}, no-trade events={n_skip}, with-trade={n_have}, "
          f"size={out.stat().st_size/1024/1024:.2f} MB")
    return out
