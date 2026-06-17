"""Shared constants + helpers for all feature builders.

Path constants + numpy/sqlite helpers used by ≥2 builder modules. Logic
identical to original build_features.py — module split is pure refactor.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd

DB = '/home/polymarket_work/db/pm_btc5m.db'
OUT_DIR = Path('/home/polymarket_work/scratch/data')


def existing_cs(parquet_path: Path) -> set:
    """Set of cs (candle_start) already present in this sub-parquet (empty if absent).
    cs is unique per event (1 candle = 1 event) → the incremental key. Base features
    are per-event with NO cross-event dependency → an existing row never changes when
    new events arrive, so incremental = compute only new cs, reuse old rows verbatim."""
    if not parquet_path.exists():
        return set()
    return set(pd.read_parquet(parquet_path, columns=['cs'])['cs'].tolist())


def write_incremental(parquet_path: Path, new_records: list,
                      *, incremental: bool) -> 'pd.DataFrame':
    """Write builder records. Full mode: just write. Incremental + file exists:
    concat(old, new), dedup by cs (new wins), sort by cs → identical layout to a
    full rebuild (builders ORDER BY candle_start). Returns written DataFrame."""
    new_df = pd.DataFrame.from_records(new_records)
    if incremental and parquet_path.exists():
        old = pd.read_parquet(parquet_path)
        if len(new_df):
            df = pd.concat([old, new_df], ignore_index=True)
            df = (df.drop_duplicates(subset='cs', keep='last')
                    .sort_values('cs').reset_index(drop=True))
        else:
            df = old
    else:
        df = (new_df.sort_values('cs').reset_index(drop=True)
              if len(new_df) else new_df)
    df.to_parquet(parquet_path, index=False, compression='zstd')
    return df
