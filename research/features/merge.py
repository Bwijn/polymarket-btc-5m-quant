"""Merge all sub-parquets on (cid, cs) → features.parquet (mining input SSOT)."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pandas as pd

from ._common import DB, OUT_DIR


def build() -> Path:
    print(); print('=' * 70); print('merge.build → features.parquet'); print('=' * 70)
    # meta + label spine from events (cid/cs/era/up_won); features left-joined onto it
    with sqlite3.connect(DB) as con:
        df = pd.read_sql_query(
            "SELECT cid, candle_start AS cs, era, up_won FROM events "
            "WHERE up_won IS NOT NULL ORDER BY candle_start", con)
    print(f"  base events: {len(df)} labeled rows")
    for name in ('binance', 'pmtrades', 'futures', 'transforms'):
        path = OUT_DIR / f'_features_{name}.parquet'
        if path.exists():
            d = pd.read_parquet(path)
            df = df.merge(d, on=['cid', 'cs'], how='left')
            print(f"  merged {name}: +{len(d.columns) - 2} cols, total now {df.shape[1]}")
        else:
            print(f"  SKIP {name}: {path.name} not found")
    out = OUT_DIR / 'features.parquet'
    df.to_parquet(out, index=False, compression='zstd')
    print(f"  → {out} shape={df.shape}, size={out.stat().st_size/1024/1024:.2f} MB")
    return out
