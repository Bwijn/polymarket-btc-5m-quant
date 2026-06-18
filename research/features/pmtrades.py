"""EP panel → _features_pmtrades.parquet (projection of the ep_panel table).

trades table was dropped (trades→EP prune 2026-06-17): EP (`p_intra_X` = entry
price) is now the only pmt feature. Its source-of-record is the slim `ep_panel`
db table (cid PK + 40 EP cols), populated by the ETL backfill (fetch /trades →
compute in-flight → durable INSERT, raw discarded). This builder just LEFT-JOINs
ep_panel onto the event spine — same db-table→parquet pattern as binance/futures.
Candles absent from ep_panel → NaN EP.

EP col set = polybot.lib.compute.pmtrades._pmt_nan_record().keys() (SSOT).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from polybot.lib.compute.pmtrades import _pmt_nan_record

from ._common import DB, OUT_DIR


def build(incremental: bool = False) -> Path:
    """Project ep_panel onto the full event spine → _features_pmtrades.parquet.
    `incremental` accepted for pipeline-API compat but ignored (full projection)."""
    print(); print('=' * 70); print('pmtrades.build: ep_panel → _features_pmtrades.parquet'); print('=' * 70)
    out = OUT_DIR / '_features_pmtrades.parquet'
    ep_cols = list(_pmt_nan_record().keys())          # SSOT EP col set (p_intra + staleness)

    with sqlite3.connect(DB) as con:
        spine = pd.read_sql("SELECT cid, candle_start AS cs FROM events ORDER BY candle_start", con)
        ep = pd.read_sql(f"SELECT cid, {','.join(chr(34)+c+chr(34) for c in ep_cols)} "
                         f"FROM ep_panel", con)

    df = spine.merge(ep, on='cid', how='left')        # candles absent from ep_panel → NaN
    for c in ep_cols:                                 # guarantee full EP schema
        if c not in df.columns:
            df[c] = np.nan
    df = df[['cid', 'cs'] + ep_cols].sort_values('cs').reset_index(drop=True)

    n_ep = df[ep_cols].notna().any(axis=1).sum()
    df.to_parquet(out, index=False, compression='zstd')
    print(f"  → {out} shape={df.shape}, EP cols={len(ep_cols)}, with-EP={n_ep}, "
          f"NaN-EP={len(df) - n_ep}, size={out.stat().st_size/1024/1024:.2f} MB")
    return out
