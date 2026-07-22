"""Regime-relative transforms (zs24h / zs7d / rank24h / lag1).

Reads all base sub-parquets, computes 4 transforms per continuous numeric col:
  __zs24h:   rolling 288-event z-score (~24h)
  __zs7d:    rolling 2016-event z-score (~7d)
  __rank24h: rolling 288-event percentile rank
  __lag1:    1-event shift

Windows are event-count (events ≈ 5min spacing, so 288 ≈ 24h).

⚠ NOT SSOT-delegated:
  Mining uses pandas rolling vectorized (batch all-events at once).
  Scanner uses polybot.lib.compute.{compute_zs, compute_rank} per-event
  with FeatureHistory rolling buffer.

  Different paradigm (batch vs per-event) — byte-equivalence not guaranteed.
  TRANSFORM_SPEC window/min_periods MUST match polybot.lib.compute.constants
  TRANSFORM_SPEC (manually synced — both use 288/2016/50/200).

  Future: if scanner needs batch transforms or vectorized polybot impl emerges,
  refactor to SSOT.
"""
from __future__ import annotations
import time
from pathlib import Path

import numpy as np
import pandas as pd

from polybot.lib.compute.constants import TRANSFORM_SPEC

from ._common import OUT_DIR

# Windows + min_periods sourced from polybot SSOT — both mining batch + scanner per-event
# must agree on these. Constants delegated; math impl still parallel (see file docstring).
TRANSFORM_W_24H = TRANSFORM_SPEC['__zs24h']['window']           # 288
TRANSFORM_W_7D  = TRANSFORM_SPEC['__zs7d']['window']            # 2016
MIN_PERIODS_24H = TRANSFORM_SPEC['__zs24h']['min_periods']      # 50
MIN_PERIODS_7D  = TRANSFORM_SPEC['__zs7d']['min_periods']       # 200


def _is_transformable(series: pd.Series, min_unique: int = 50) -> bool:
    """A col is worth transforming if continuous numeric with enough variation."""
    if not pd.api.types.is_numeric_dtype(series):
        return False
    nonnull = series.dropna()
    if len(nonnull) < 1000:
        return False
    if nonnull.nunique() < min_unique:
        return False
    return True


def build() -> Path:
    """Build _features_transforms.parquet — regime-relative derived features."""
    print(); print('=' * 70); print('transforms.build → _features_transforms.parquet'); print('=' * 70)

    sources = ['binance', 'pmtrades', 'futures']
    dfs = []
    for name in sources:
        path = OUT_DIR / f'_features_{name}.parquet'
        if path.exists():
            dfs.append(pd.read_parquet(path))
        else:
            print(f"  WARN: {path.name} missing, skipping")
    base = dfs[0]
    for d in dfs[1:]:
        base = base.merge(d, on=['cid', 'cs'], how='left')
    base = base.sort_values('cs').reset_index(drop=True)
    print(f"  merged base shape: {base.shape}")

    skip_cols = {'cid', 'cs', 'era', 'up_won'}
    transformable = [c for c in base.columns
                     if c not in skip_cols and _is_transformable(base[c])]
    print(f"  transformable cols: {len(transformable)}")

    sub = base[transformable]

    t = time.time()
    print(f"  computing zs24h (rolling window={TRANSFORM_W_24H})...", flush=True)
    mean_24h = sub.rolling(window=TRANSFORM_W_24H, min_periods=MIN_PERIODS_24H, closed='left').mean()
    std_24h  = sub.rolling(window=TRANSFORM_W_24H, min_periods=MIN_PERIODS_24H, closed='left').std()
    zs_24h = ((sub - mean_24h) / std_24h.replace(0, np.nan))
    print(f"    done in {time.time()-t:.1f}s")

    t = time.time()
    print(f"  computing zs7d (rolling window={TRANSFORM_W_7D})...", flush=True)
    mean_7d = sub.rolling(window=TRANSFORM_W_7D, min_periods=MIN_PERIODS_7D, closed='left').mean()
    std_7d  = sub.rolling(window=TRANSFORM_W_7D, min_periods=MIN_PERIODS_7D, closed='left').std()
    zs_7d = ((sub - mean_7d) / std_7d.replace(0, np.nan))
    print(f"    done in {time.time()-t:.1f}s")

    t = time.time()
    print(f"  computing rank24h (pct rank in window)...", flush=True)
    rank_24h = sub.rolling(window=TRANSFORM_W_24H, min_periods=MIN_PERIODS_24H).rank(pct=True)
    print(f"    done in {time.time()-t:.1f}s")

    t = time.time()
    print(f"  computing lag1 (event-level shift)...", flush=True)
    lag1 = sub.shift(1)
    print(f"    done in {time.time()-t:.1f}s")

    out_df = pd.DataFrame({'cid': base['cid'].values, 'cs': base['cs'].values})
    for col in transformable:
        out_df[f'{col}__zs24h']   = zs_24h[col].values
        out_df[f'{col}__zs7d']    = zs_7d[col].values
        out_df[f'{col}__rank24h'] = rank_24h[col].values
        out_df[f'{col}__lag1']    = lag1[col].values

    out = OUT_DIR / '_features_transforms.parquet'
    out_df.to_parquet(out, index=False, compression='zstd')
    print(f"  → {out} shape={out_df.shape}, size={out.stat().st_size/1024/1024:.2f} MB")
    return out
