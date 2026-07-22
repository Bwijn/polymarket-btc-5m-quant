"""Binance klines → _features_binance.parquet builder.

Math SSOT: polybot.lib.compute.compute_bn_features. This module is ETL only.
"""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from polybot.lib.compute.binance import compute_bn_features

from ._common import DB, OUT_DIR, existing_cs, write_incremental


def build(incremental: bool = False) -> Path:
    """Build _features_binance.parquet from binance_klines.
    incremental=True: skip cs already in the parquet, compute only new events."""
    print(); print('=' * 70); print('binance.build: binance_klines → _features_binance.parquet'); print('=' * 70)
    out = OUT_DIR / '_features_binance.parquet'
    skip = existing_cs(out) if incremental else set()
    con = sqlite3.connect(DB)
    events = pd.read_sql("SELECT cid, candle_start AS cs FROM events ORDER BY candle_start", con)
    klines = pd.read_sql("""
        SELECT open_ts_ms/1000 AS ts, open, high, low, close,
               volume, quote_volume, taker_buy_volume, n_trades
        FROM binance_klines ORDER BY open_ts_ms
    """, con)
    con.close()
    events = events[~events['cs'].isin(skip)]
    print(f"  events: {len(events)} (incremental skip {len(skip)}), klines: {len(klines)}")

    klines['ts'] = klines['ts'].astype(np.int64)
    klines.set_index('ts', inplace=True)

    records = []
    t_start = time.time()
    for i, (cid, cs) in enumerate(events.itertuples(index=False)):
        rec = {'cid': cid, 'cs': cs}
        rec.update(compute_bn_features(klines, cs))   # ← delegate to polybot SSOT
        records.append(rec)
        if (i + 1) % 5000 == 0:
            elapsed = time.time() - t_start
            print(f"  [{i+1}/{len(events)}] elapsed={elapsed:.0f}s rate={(i+1)/elapsed:.0f} ev/s", flush=True)

    df = write_incremental(out, records, incremental=incremental)
    print(f"  → {out} shape={df.shape}, size={out.stat().st_size/1024/1024:.2f} MB")
    return out
